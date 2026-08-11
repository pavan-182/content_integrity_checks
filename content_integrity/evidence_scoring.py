from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import ParsedRecord
from .pair_evidence import PairEvidence, collect_pair_evidence
from .utils import normalize_for_matching, normalize_label


PRIMARY_WEIGHTS = {
    "exact_original_body": 1.0,
    "exact_results_section": 1.0,
    "substantial_shared_original_block": 0.85,
    "strong_masked_body_with_original_support": 0.75,
    "masked_title_template_with_original_support": 0.65,
}
SECTION_WEIGHTS = {"result": 0.20, "conclusion": 0.20, "background": 0.10, "method": 0.05}
SUPPORTING_SECTION_THRESHOLD = 0.75


def _context(left: ParsedRecord, right: ParsedRecord) -> tuple[str, ...]:
    evidence = []
    if set(left.trial_ids) & set(right.trial_ids):
        evidence.append("shared_trial_id")
    if set(map(normalize_for_matching, left.authors)) & set(map(normalize_for_matching, right.authors)) - {""}:
        evidence.append("shared_author")
    if set(map(normalize_for_matching, left.affiliations)) & set(map(normalize_for_matching, right.affiliations)) - {""}:
        evidence.append("shared_affiliation")
    return tuple(evidence)


def _section_support(left: ParsedRecord, right: ParsedRecord) -> tuple[tuple[str, ...], float]:
    left_sections = {normalize_label(item.get("section", "")): item.get("text", "") for item in left.abstract_sections}
    right_sections = {normalize_label(item.get("section", "")): item.get("text", "") for item in right.abstract_sections}
    evidence, score = [], 0.0
    for section in left_sections.keys() & right_sections.keys():
        left_text = normalize_for_matching(left_sections[section])
        right_text = normalize_for_matching(right_sections[section])
        if not left_text or not right_text:
            continue
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, left_text.split(), right_text.split(), autojunk=False).ratio()
        if similarity < SUPPORTING_SECTION_THRESHOLD:
            continue
        label = section.lower()
        weight = next((value for key, value in SECTION_WEIGHTS.items() if key in label), 0.05)
        evidence.append(f"masked_{label}_section_similarity")
        score = max(score, weight)
    return tuple(sorted(evidence)), score


def _review_score(primary_score: float, supporting_score: float) -> float:
    return min(1.0, primary_score + min(supporting_score, 0.25)) if primary_score else 0.0


@dataclass(frozen=True, slots=True)
class TieredEvidenceScore:
    left_record_id: str
    right_record_id: str
    primary_evidence: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    contextual_evidence: tuple[str, ...]
    primary_score: float
    supporting_score: float
    review_score: float
    evidence_state: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for field in ("primary_evidence", "supporting_evidence", "contextual_evidence"):
            data[field] = " | ".join(data[field])
        return data


def score_pair_evidence(
    records: list[ParsedRecord], evidence_rows: list[PairEvidence] | None = None,
) -> list[TieredEvidenceScore]:
    lookup = {record.record_id: record for record in records}
    scores = []
    for evidence in evidence_rows or collect_pair_evidence(records):
        primary = tuple(item for item in evidence.direct_evidence if item in PRIMARY_WEIGHTS)
        primary_score = max((PRIMARY_WEIGHTS[item] for item in primary), default=0.0)
        section_evidence, section_score = _section_support(lookup[evidence.left_record_id], lookup[evidence.right_record_id])
        supporting = list(section_evidence)
        supporting_score = 0.0
        if evidence.exact_masked_title and "masked_title_template_with_original_support" not in primary:
            supporting.append("exact_masked_title_without_original_support")
            supporting_score = max(supporting_score, 0.10)
        elif evidence.masked_title_similarity >= 0.90:
            supporting.append("high_masked_title_similarity")
            supporting_score = max(supporting_score, 0.10)
        if evidence.masked_body_similarity >= 0.75 and "strong_masked_body_with_original_support" not in primary:
            supporting.append("high_masked_body_similarity")
            supporting_score = max(supporting_score, 0.15)
        supporting_score = max(supporting_score, section_score)
        state = "primary" if primary else "supporting_only" if supporting else "none"
        scores.append(TieredEvidenceScore(
            left_record_id=evidence.left_record_id,
            right_record_id=evidence.right_record_id,
            primary_evidence=primary,
            supporting_evidence=tuple(sorted(supporting)),
            contextual_evidence=_context(lookup[evidence.left_record_id], lookup[evidence.right_record_id]),
            primary_score=primary_score,
            supporting_score=supporting_score,
            review_score=_review_score(primary_score, supporting_score),
            evidence_state=state,
        ))
    return scores
