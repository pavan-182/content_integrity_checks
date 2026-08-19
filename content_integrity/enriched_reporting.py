from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from .editorial_scoring import assign_editorial_priority
from .entity_substitutions import collect_entity_substitutions
from .evidence_scoring import score_pair_evidence
from .family_clustering import build_suspicious_families
from .models import ParsedRecord
from .pair_classification import classify_pairs
from .pair_evidence import collect_pair_evidence
from .study_context import compare_study_context
from .template_features import TemplateFeatures, build_template_features
from .utils import normalize_for_matching, split_sentences, text_tokens


REPORT_VERSION = "asco-enriched-report-v1"
PAIR_REPORT_COLUMNS = [
    "report_version", "pair_id", "left_record_id", "right_record_id", "left_title", "right_title", "retrieval_routes",
    "pair_class", "confidence", "editorial_score", "review_priority", "rule_path", "detector_evidence", "primary_evidence", "supporting_evidence",
    "contextual_evidence", "direct_evidence", "masked_title_similarity", "original_title_similarity",
    "masked_body_similarity", "original_body_similarity", "strongest_section", "strongest_masked_section_similarity",
    "largest_shared_original_block_words", "context_interpretation", "shared_trial_ids", "shared_databases",
    "shared_populations", "shared_endpoints", "explicit_companion_wording", "family_id", "left_family_status",
    "right_family_status", "limitations", "priority_reason",
    "shared_entities", "left_only_entities", "right_only_entities", "likely_substitutions",
    "left_supporting_sentences", "right_supporting_sentences", "left_matched_text", "right_matched_text", "matching_text_evidence",
    "editor_label", "editor_notes",
]
FAMILY_REPORT_COLUMNS = [
    "report_version", "family_id", "family_size", "representative_record_id", "family_edge_score",
    "accepted_edge_count", "member_ids", "outlier_record_ids", "excluded_pair_count",
]
ABSTRACT_REPORT_COLUMNS = [
    "report_version", "record_id", "source_file", "title", "candidate_pair_count", "finding_pair_count",
    "highest_review_priority", "strongest_matched_record_id", "family_id", "family_size", "family_edge_score", "family_member_status", "reporting_note",
]


def _joined(values: tuple[str, ...]) -> str:
    return " | ".join(values)


def _reverse_substitutions(value: object) -> str:
    reversed_items = []
    for item in str(value or "").split("; "):
        label, separator, substitution = item.partition(": ")
        left, arrow, right = substitution.partition(" -> ")
        if separator and arrow:
            reversed_items.append(f"{label}: {right} -> {left}")
    return "; ".join(reversed_items)


def _abstract_windows(record: ParsedRecord) -> list[str]:
    sections = [item.get("text", "") for item in record.abstract_sections] or [record.abstract_text]
    sentences = [sentence for section in sections for sentence in split_sentences(section)]
    return sentences + [" ".join(sentences[index:index + 2]) for index in range(len(sentences) - 1)]


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, text_tokens(left), text_tokens(right), autojunk=False).ratio()


def _aligned_text(left: ParsedRecord, right: ParsedRecord, anchor: str = "", section: str = "") -> tuple[str, str]:
    left_windows, right_windows = _abstract_windows(left), _abstract_windows(right)
    if anchor:
        normalized_anchor = normalize_for_matching(anchor)
        return (
            max(left_windows, key=lambda text: _similarity(normalize_for_matching(text), normalized_anchor), default=""),
            max(right_windows, key=lambda text: _similarity(normalize_for_matching(text), normalized_anchor), default=""),
        )
    label = normalize_for_matching(section)
    return tuple(next((item.get("text", "") for item in record.abstract_sections if normalize_for_matching(item.get("section", "")) == label), record.abstract_text) for record in (left, right))


def directional_finding_rows(pair_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = []
    swapped = {
        "left_record_id": "right_record_id", "left_title": "right_title",
        "left_family_status": "right_family_status", "left_only_entities": "right_only_entities",
        "left_supporting_sentences": "right_supporting_sentences",
        "left_matched_text": "right_matched_text",
    }
    for pair in pair_rows:
        if pair["review_priority"] == "None":
            continue
        rows.append(pair)
        reverse = dict(pair)
        for left, right in swapped.items():
            reverse[left], reverse[right] = pair[right], pair[left]
        reverse["likely_substitutions"] = _reverse_substitutions(pair.get("likely_substitutions", ""))
        rows.append(reverse)
    return rows


def build_enriched_reports(
    records: list[ParsedRecord], detector_findings: list[Any] | None = None,
    features: list[TemplateFeatures] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    features = features if features is not None else [build_template_features(record) for record in records]
    evidence = collect_pair_evidence(records, detector_findings, features)
    scores = score_pair_evidence(records, evidence)
    contexts = compare_study_context(records, evidence, features)
    substitutions = collect_entity_substitutions(records, evidence, features)
    classifications = classify_pairs(records, scores, contexts)
    priorities = [assign_editorial_priority(item) for item in classifications]
    families = build_suspicious_families(classifications)
    record_lookup = {record.record_id: record for record in records}
    detector_lookup: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for finding in detector_findings or []:
        detector_lookup[tuple(sorted((finding.record_id, finding.matched_record_id)))].append(finding)
    evidence_lookup = {(item.left_record_id, item.right_record_id): item for item in evidence}
    context_lookup = {(item.left_record_id, item.right_record_id): item for item in contexts}
    substitution_lookup = {(item.left_record_id, item.right_record_id): item for item in substitutions}
    priority_lookup = {(item.left_record_id, item.right_record_id): item for item in priorities}
    family_lookup = {item.record_id: item for item in families}

    pair_rows = []
    for item in classifications:
        key = (item.left_record_id, item.right_record_id)
        pair_evidence, context, priority, substitution = evidence_lookup[key], context_lookup[key], priority_lookup[key], substitution_lookup[key]
        left_family, right_family = family_lookup.get(item.left_record_id), family_lookup.get(item.right_record_id)
        exact_match = next(
            (
                f"{finding.record_id}: {' | '.join(finding.record_matched_sentences)} || "
                f"{finding.matched_record_id}: {' | '.join(finding.matched_record_sentences)}"
                for finding in detector_lookup.get(tuple(sorted(key)), [])
                if getattr(finding, "record_matched_sentences", None)
            ),
            "",
        )
        matched_block = next((
            block
            for finding in detector_lookup.get(tuple(sorted(key)), [])
            for block in getattr(finding, "matched_text_blocks", [])
        ), "")
        left_matched_text, right_matched_text = _aligned_text(
            record_lookup[item.left_record_id], record_lookup[item.right_record_id], matched_block,
            pair_evidence.strongest_section,
        )
        matching_text = (
            f"{item.left_record_id}: {left_matched_text} || {item.right_record_id}: {right_matched_text}"
            if left_matched_text and right_matched_text else exact_match
        )
        pair_rows.append({
            "report_version": REPORT_VERSION,
            "pair_id": f"PAIR-{min(item.left_record_id, item.right_record_id)}--{max(item.left_record_id, item.right_record_id)}",
            "left_record_id": item.left_record_id,
            "right_record_id": item.right_record_id,
            "left_title": record_lookup[item.left_record_id].title,
            "right_title": record_lookup[item.right_record_id].title,
            "retrieval_routes": _joined(pair_evidence.retrieval_routes),
            "pair_class": item.pair_class,
            "confidence": next((finding.confidence for finding in detector_lookup.get(tuple(sorted(key)), [])), ""),
            "editorial_score": priority.editorial_score,
            "review_priority": priority.review_priority,
            "rule_path": item.rule_path,
            "detector_evidence": _joined(pair_evidence.detector_evidence),
            "primary_evidence": _joined(item.primary_evidence),
            "supporting_evidence": _joined(item.supporting_evidence),
            "contextual_evidence": _joined(item.contextual_evidence),
            "direct_evidence": _joined(pair_evidence.direct_evidence),
            "masked_title_similarity": pair_evidence.masked_title_similarity,
            "original_title_similarity": pair_evidence.original_title_similarity,
            "masked_body_similarity": pair_evidence.masked_body_similarity,
            "original_body_similarity": pair_evidence.original_body_similarity,
            "strongest_section": pair_evidence.strongest_section,
            "strongest_masked_section_similarity": pair_evidence.strongest_masked_section_similarity,
            "largest_shared_original_block_words": pair_evidence.largest_shared_original_block_words,
            "context_interpretation": context.context_interpretation,
            "shared_trial_ids": _joined(context.shared_trial_ids),
            "shared_databases": _joined(context.shared_databases),
            "shared_populations": _joined(context.shared_populations),
            "shared_endpoints": _joined(context.shared_endpoints),
            "explicit_companion_wording": context.explicit_companion_wording,
            "family_id": left_family.family_id if left_family and right_family and left_family.family_id == right_family.family_id else "",
            "left_family_status": left_family.member_status if left_family else "",
            "right_family_status": right_family.member_status if right_family else "",
            "limitations": item.limitations,
            "priority_reason": priority.priority_reason,
            "shared_entities": substitution.shared_entities,
            "left_only_entities": substitution.left_only_entities,
            "right_only_entities": substitution.right_only_entities,
            "likely_substitutions": substitution.likely_substitutions,
            "left_supporting_sentences": substitution.left_supporting_sentences,
            "right_supporting_sentences": substitution.right_supporting_sentences,
            "left_matched_text": left_matched_text,
            "right_matched_text": right_matched_text,
            "matching_text_evidence": matching_text,
            "editor_label": "not_reviewed",
            "editor_notes": "",
        })

    family_groups: dict[str, list] = defaultdict(list)
    for item in families:
        family_groups[item.family_id].append(item)
    family_rows = [
        {
            "report_version": REPORT_VERSION,
            "family_id": family_id,
            "family_size": members[0].family_size,
            "representative_record_id": members[0].representative_record_id,
            "family_edge_score": members[0].family_edge_score,
            "accepted_edge_count": members[0].accepted_edge_count,
            "member_ids": _joined(tuple(sorted(item.record_id for item in members))),
            "outlier_record_ids": _joined(tuple(sorted(item.record_id for item in members if item.member_status == "outlier"))),
            "excluded_pair_count": members[0].excluded_pair_count,
        }
        for family_id, members in sorted(family_groups.items())
    ]

    linked: dict[str, list] = defaultdict(list)
    for item in classifications:
        linked[item.left_record_id].append(item)
        linked[item.right_record_id].append(item)
    priority_rank = {"None": 0, "Low": 1, "Medium": 2, "High": 3}
    abstract_rows = []
    for record in records:
        matches = linked[record.record_id]
        strongest = max(
            matches,
            key=lambda item: (priority_rank[priority_lookup[(item.left_record_id, item.right_record_id)].review_priority], item.review_score),
            default=None,
        )
        family = family_lookup.get(record.record_id)
        abstract_rows.append({
            "report_version": REPORT_VERSION,
            "record_id": record.record_id,
            "source_file": record.source_file,
            "title": record.title,
            "candidate_pair_count": len(matches),
            "finding_pair_count": sum(priority_lookup[(item.left_record_id, item.right_record_id)].review_priority != "None" for item in matches),
            "highest_review_priority": priority_lookup[(strongest.left_record_id, strongest.right_record_id)].review_priority if strongest else "None",
            "strongest_matched_record_id": (
                strongest.right_record_id if strongest.left_record_id == record.record_id else strongest.left_record_id
            ) if strongest else "",
            "family_id": family.family_id if family else "",
            "family_size": family.family_size if family else 0,
            "family_edge_score": family.family_edge_score if family else 0.0,
            "family_member_status": family.member_status if family else "",
            "reporting_note": "No primary-evidence pair." if matches and not any(item.primary_evidence for item in matches) else "No routed candidate." if not matches else "Primary-evidence pair available for review.",
        })
    return pair_rows, family_rows, abstract_rows
