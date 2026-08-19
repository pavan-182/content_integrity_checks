from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from .candidate_routes import generate_candidate_pairs
from .models import ParsedRecord
from .template_features import TemplateFeatures, build_template_features
from .thresholds import (
    PAIR_EVIDENCE_MIN_SHARED_BLOCK_WORDS as MIN_SHARED_BLOCK_WORDS,
    PAIR_EVIDENCE_ORIGINAL_SUPPORT as ORIGINAL_SUPPORT,
    PAIR_EVIDENCE_STRONG_MASKED_BODY as STRONG_MASKED_BODY,
)
from .utils import normalize_for_matching, normalize_label, text_tokens


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, text_tokens(left), text_tokens(right), autojunk=False).ratio() if left and right else 0.0


def _largest_shared_block(left: str, right: str) -> int:
    blocks = SequenceMatcher(None, text_tokens(left), text_tokens(right), autojunk=False).get_matching_blocks()
    return max((block.size for block in blocks), default=0)


def _section_evidence(left: TemplateFeatures, right: TemplateFeatures) -> tuple[float, str, bool]:
    left_sections = {normalize_label(item.section): item for item in left.sections}
    right_sections = {normalize_label(item.section): item for item in right.sections}
    candidates = [
        (_similarity(left_sections[label].masked, right_sections[label].masked), label,
         left_sections[label].normalized == right_sections[label].normalized)
        for label in left_sections.keys() & right_sections.keys()
    ]
    return max(candidates, default=(0.0, "", False))


@dataclass(frozen=True, slots=True)
class PairEvidence:
    left_record_id: str
    right_record_id: str
    retrieval_routes: tuple[str, ...]
    masked_title_similarity: float
    original_title_similarity: float
    exact_masked_title: bool
    masked_body_similarity: float
    original_body_similarity: float
    exact_original_body: bool
    strongest_masked_section_similarity: float
    strongest_section: str
    exact_results_section: bool
    largest_shared_original_block_words: int
    detector_evidence: tuple[str, ...]
    direct_evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["retrieval_routes"] = " | ".join(self.retrieval_routes)
        data["detector_evidence"] = " | ".join(self.detector_evidence)
        data["direct_evidence"] = " | ".join(self.direct_evidence)
        return data


def collect_pair_evidence(
    records: list[ParsedRecord], detector_findings: list[Any] | None = None,
    features: list[TemplateFeatures] | None = None,
) -> list[PairEvidence]:
    features = features if features is not None else [build_template_features(record) for record in records]
    lookup = {feature.record_id: feature for feature in features}
    candidates = {
        (pair.left_record_id, pair.right_record_id): set(pair.routes)
        for pair in generate_candidate_pairs(records, features)
    }
    detector_lookup: dict[tuple[str, str], list[Any]] = {}
    for finding in detector_findings or []:
        key = tuple(sorted((finding.record_id, finding.matched_record_id)))
        detector_lookup.setdefault(key, []).append(finding)
        candidates.setdefault(key, set()).add(finding.check_type)
    evidence = []
    for (left_id, right_id), routes in sorted(candidates.items()):
        left, right = lookup[left_id], lookup[right_id]
        masked_title = _similarity(normalize_for_matching(left.title.masked), normalize_for_matching(right.title.masked))
        original_title = _similarity(left.title.normalized, right.title.normalized)
        masked_body = _similarity(normalize_for_matching(left.abstract.masked), normalize_for_matching(right.abstract.masked))
        original_body = _similarity(left.abstract.normalized, right.abstract.normalized)
        strongest_section, section_name, exact_section = _section_evidence(left, right)
        exact_results = exact_section and "result" in section_name.lower()
        largest_block = _largest_shared_block(left.abstract.normalized, right.abstract.normalized)
        direct = []
        if left.abstract.normalized == right.abstract.normalized and left.abstract.normalized:
            direct.append("exact_original_body")
        if exact_results:
            direct.append("exact_results_section")
        if largest_block >= MIN_SHARED_BLOCK_WORDS:
            direct.append("substantial_shared_original_block")
        if masked_body >= STRONG_MASKED_BODY and original_body >= ORIGINAL_SUPPORT:
            direct.append("strong_masked_body_with_original_support")
        if masked_title == 1.0 and original_title >= ORIGINAL_SUPPORT:
            direct.append("masked_title_template_with_original_support")
        detector_signal_set = {item.match_type for item in detector_lookup.get((left_id, right_id), [])}
        detector_signals = tuple(sorted(detector_signal_set))
        if any(item.check_type == "entity_normalized_template" for item in detector_lookup.get((left_id, right_id), [])):
            direct.append("entity_normalized_template")
        if detector_signal_set & {
            "multiple_distinctive_sentences", "rare_exact_phrase", "substantial_shared_text", "partial_or_reordered_reuse",
        }:
            direct.append("distinctive_shared_text")
        evidence.append(PairEvidence(
            left_record_id=left_id,
            right_record_id=right_id,
            retrieval_routes=tuple(sorted(routes)),
            masked_title_similarity=masked_title,
            original_title_similarity=original_title,
            exact_masked_title=masked_title == 1.0,
            masked_body_similarity=masked_body,
            original_body_similarity=original_body,
            exact_original_body=left.abstract.normalized == right.abstract.normalized and bool(left.abstract.normalized),
            strongest_masked_section_similarity=strongest_section,
            strongest_section=section_name,
            exact_results_section=exact_results,
            largest_shared_original_block_words=largest_block,
            detector_evidence=detector_signals,
            direct_evidence=tuple(dict.fromkeys(direct)),
        ))
    return evidence
