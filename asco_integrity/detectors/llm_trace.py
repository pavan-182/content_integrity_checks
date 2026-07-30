from __future__ import annotations

from collections.abc import Iterable

from ..models import Finding, ParsedRecord
from ..rules import LLMTraceRule, load_llm_trace_rules
from .llm_trace_context import TraceCandidate, context_for_span, trace_blocks_for_record


LLMRule = LLMTraceRule


def built_in_llm_rules() -> list[LLMRule]:
    return load_llm_trace_rules()


def detect_llm_trace_candidates(
    record: ParsedRecord,
    rules: Iterable[LLMRule],
) -> list[TraceCandidate]:
    candidates: list[TraceCandidate] = []
    seen: set[tuple[str, int, int, str]] = set()
    for block in trace_blocks_for_record(record):
        for rule in rules:
            for compiled in rule.compiled_patterns:
                for match in compiled.finditer(block.source_text):
                    identity = (rule.rule_id, block.block_index, match.start(), block.section_label)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    exact = block.source_text[match.start() : match.end()]
                    candidates.append(
                        TraceCandidate(
                            record_id=record.record_id,
                            source_file=record.source_file,
                            field_name=block.field_name,
                            section_label=block.section_label,
                            block_type=block.block_type,
                            block_index=block.block_index,
                            source_text=block.source_text,
                            source_start=match.start(),
                            source_end=match.end(),
                            matched_text=exact,
                            context=context_for_span(block.source_text, match.start(), match.end()),
                            match_type="known_pattern",
                            mapped_rule_id=rule.rule_id,
                            category=rule.category,
                            severity=rule.severity,
                            confidence=rule.confidence,
                            rule=rule,
                        )
                    )
    return sorted(
        candidates,
        key=lambda item: (
            item.record_id,
            item.block_index,
            item.source_start,
            item.mapped_rule_id,
            item.source_end,
        ),
    )


def candidate_to_finding(candidate: TraceCandidate) -> Finding:
    if not candidate.validation_status:
        needs_validation = (
            candidate.match_type != "known_pattern"
            or candidate.rule is None
            or candidate.rule.requires_validation
            or candidate.context.quotation_context != "not_quoted"
        )
        candidate.validation_status = "pending" if needs_validation else "not_required"
    if not candidate.review_status:
        if candidate.rule and candidate.rule.signal_level == "supporting":
            candidate.review_status = "supporting_only"
        elif candidate.validation_status == "rejected":
            candidate.review_status = "excluded_by_validation"
        elif candidate.validation_status == "uncertain":
            candidate.review_status = "needs_editor_review"
        elif candidate.validation_status in {"pending"}:
            candidate.review_status = "needs_validation"
        else:
            candidate.review_status = "needs_review"
    return Finding(
        finding_id="",
        record_id=candidate.record_id,
        source_file=candidate.source_file,
        detector_type="llm_response_trace",
        category=candidate.category,
        matched_text=candidate.matched_text,
        evidence_snippet=candidate.context.containing_sentence or candidate.context.containing_paragraph,
        section_or_field=candidate.section_label,
        severity=candidate.severity,
        confidence=candidate.confidence,
        rule_id=candidate.mapped_rule_id,
        check_type=candidate.match_type,
        validation_status=candidate.validation_status,
        validation_reason=candidate.validation_reason,
        validated_by=candidate.validated_by,
        review_status=candidate.review_status,
    )


def detect_llm_trace(record: ParsedRecord, rules: Iterable[LLMRule]) -> list[Finding]:
    return [candidate_to_finding(candidate) for candidate in detect_llm_trace_candidates(record, rules)]
