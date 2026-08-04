from __future__ import annotations

from collections.abc import Iterable

from ..models import Finding
from ..rules import LLMTraceRule
from .llm_trace_context import TraceCandidate


def _overlaps(left: TraceCandidate, right: TraceCandidate) -> bool:
    return left.source_start < right.source_end and right.source_start < left.source_end


def _adds_lexical_evidence(candidate: TraceCandidate, known: TraceCandidate) -> bool:
    overlap_start = max(candidate.source_start, known.source_start)
    overlap_end = min(candidate.source_end, known.source_end)
    outside = (
        candidate.source_text[candidate.source_start:overlap_start]
        + candidate.source_text[overlap_end:candidate.source_end]
    )
    return any(character.isalnum() for character in outside)


def _preference(candidate: TraceCandidate) -> tuple[int, float, str, str]:
    path_rank = {"known_pattern": 2, "semantic_variant": 1, "novel_pattern_candidate": 0}
    return (
        -path_rank.get(candidate.match_type, -1),
        -candidate.confidence,
        candidate.mapped_rule_id,
        candidate.category,
    )


def fuse_llm_trace_candidates(candidates: Iterable[TraceCandidate]) -> list[TraceCandidate]:
    selected: list[TraceCandidate] = []
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.record_id,
            item.section_label.casefold(),
            item.block_index,
            item.source_start,
            item.source_end,
            item.category,
            item.mapped_rule_id,
        ),
    )
    strong_known = [
        candidate
        for candidate in ordered
        if candidate.match_type == "known_pattern"
        and candidate.rule is not None
        and candidate.rule.signal_level == "strong"
    ]
    for candidate in ordered:
        if candidate.match_type != "known_pattern" and any(
            known.record_id == candidate.record_id
            and known.section_label.casefold() == candidate.section_label.casefold()
            and known.block_index == candidate.block_index
            and known.category != candidate.category
            and _overlaps(known, candidate)
            and not _adds_lexical_evidence(candidate, known)
            for known in strong_known
        ):
            continue
        duplicates = [
            current
            for current in selected
            if current.record_id == candidate.record_id
            and current.section_label.casefold() == candidate.section_label.casefold()
            and current.block_index == candidate.block_index
            and current.category == candidate.category
            and _overlaps(current, candidate)
        ]
        if not duplicates:
            selected.append(candidate)
            continue
        current = min(duplicates, key=_preference)
        if _preference(candidate) < _preference(current):
            selected[selected.index(current)] = candidate
    return sorted(
        selected,
        key=lambda item: (
            item.record_id,
            item.section_label.casefold(),
            item.block_index,
            item.source_start,
            item.source_end,
            item.mapped_rule_id,
        ),
    )


def llm_reviewer_priority(findings: Iterable[Finding], rules: Iterable[LLMTraceRule]) -> str:
    rule_by_id = {rule.rule_id: rule for rule in rules}
    priorities: list[str] = []
    for finding in findings:
        if finding.detector_type != "llm_response_trace":
            continue
        rule = rule_by_id.get(finding.rule_id)
        if (
            finding.validation_status == "rejected"
            or finding.review_status == "supporting_only"
            or (rule is not None and not rule.priority_eligible)
        ):
            continue
        if (
            finding.check_type == "known_pattern"
            and rule
            and rule.signal_level == "strong"
            and finding.validation_status in {"", "not_required", "confirmed"}
        ):
            priorities.append("High")
        elif finding.validation_status == "confirmed":
            priorities.append("High" if rule and rule.signal_level == "strong" else "Medium")
        elif finding.validation_status in {"pending", "uncertain"}:
            priorities.append("Low")
        elif rule and rule.signal_level == "contextual":
            priorities.append("Low")
        elif rule is None and finding.check_type not in {"semantic_variant", "novel_pattern_candidate"}:
            legacy_priority = finding.severity.title()
            if legacy_priority in {"Low", "Medium", "High"}:
                priorities.append(legacy_priority)
    return max(priorities, key={"Low": 1, "Medium": 2, "High": 3}.get, default="None")
