from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from hashlib import blake2b
from itertools import combinations
from typing import Any

from ..models import ParsedRecord
from ..template_detection import (
    DATE_PATTERNS,
    DRUG_SUFFIX_PATTERN,
    EMAIL_PATTERN,
    GENE_PATTERN,
    NUMBER_PATTERN,
    PERCENT_PATTERN,
    PLACEHOLDER_TOKEN_RE,
    PLACEHOLDER_TOKENS,
    PVAL_PATTERN,
    SECTION_WEIGHTS,
    TRIAL_PATTERN,
    URL_PATTERN,
    _candidate_pairs,
    _content_class,
    _shared_excerpt,
    _similarity,
)
from ..utils import normalize_for_matching, normalize_label, normalize_whitespace, text_tokens
from .exact_text_reuse import _non_generic_text


# ponytail: uncalibrated ASCO V1 thresholds; replace after labelled-corpus calibration.
MASKED_SIMILARITY_THRESHOLD = 0.88
ORIGINAL_SUPPORT_THRESHOLD = 0.55
MINIMUM_SKELETON_WORDS = 30
MAXIMUM_PLACEHOLDER_RATIO = 0.35
MINIMUM_SUBSTITUTIONS = 1
SECTION_SIMILARITY_THRESHOLD = 0.88

EXPLICIT_GENE_PATTERN = re.compile(
    r"\b(?:EGFR|ALK|KRAS|NRAS|BRAF|RET|ROS1|MET|PIK3CA|PTEN|TP53|APC)\b"
)
DISEASE_PATTERN = re.compile(
    r"\b(?:(?:non-small[- ]cell|small[- ]cell)\s+lung|lung|breast|colorectal|"
    r"colon|rectal|prostate|ovarian|pancreatic|gastric|endometrial|cervical|"
    r"renal(?: cell)?|hepatocellular|urothelial|thyroid|head and neck)\s+"
    r"(?:cancer|carcinoma)\b|\b(?:melanoma|mesothelioma|glioblastoma|"
    r"multiple myeloma|hodgkin lymphoma|non-hodgkin lymphoma|acute myeloid "
    r"leukemia|chronic lymphocytic leukemia)\b",
    re.IGNORECASE,
)
BIOMARKER_PATTERN = re.compile(
    r"\b(?:tumou?r mutational burden|microsatellite instability|mismatch repair "
    r"deficien(?:cy|t)|circulating tumou?r DNA|minimal residual disease)\b",
    re.IGNORECASE,
)
EXPLICIT_RELATION_RE = re.compile(
    r"\b(?:same|parent|companion|previously reported)\s+(?:trial|cohort|study|protocol)\b|"
    r"\b(?:analysis of|derived from)\s+the\s+(?:same|parent)\s+(?:trial|cohort|study)\b",
    re.IGNORECASE,
)
HIGH_VALUE_SECTIONS = ("result", "conclusion")
MEANINGFUL_ENTITY_TYPES = {
    "trial_id", "date", "pvalue", "percent", "gene", "drug", "disease",
    "biomarker", "number",
}


@dataclass(frozen=True, slots=True)
class EntityNormalizedTemplateFinding:
    pair_id: str
    check_type: str
    check_triggered: bool
    evidence: str
    severity: str
    confidence: str
    matched_source_type: str
    matched_source_id: str
    review_reason: str
    record_id: str
    matched_record_id: str
    source_file: str
    matched_source_file: str
    title: str
    matched_title: str
    match_type: str
    masked_skeleton_similarity: float
    original_text_similarity: float
    ngram_similarity: float
    weighted_section_similarity: float
    high_value_section_similarity: float
    matched_sections: list[str]
    variable_substitutions: str
    shared_skeleton_excerpt: str
    relationship_context: str
    review_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _Representation:
    original: str
    original_normalized: str
    normalized: str
    skeleton: str
    section_skeletons: dict[str, str]
    placeholder_count: int
    placeholder_ratio: float
    meaningful_word_count: int
    entities: dict[str, tuple[str, ...]]


def _mask_and_capture(text: str) -> tuple[str, dict[str, tuple[str, ...]]]:
    captured: dict[str, list[str]] = defaultdict(list)
    masked = normalize_whitespace(text)

    def replace(pattern: re.Pattern[str], label: str, value: str) -> None:
        nonlocal masked

        def replacement(match: re.Match[str]) -> str:
            captured[label].append(
                normalize_whitespace(match.group(0)).rstrip("-") if label == "gene"
                else normalize_whitespace(match.group(0))
            )
            return value

        masked = pattern.sub(replacement, masked)

    replace(TRIAL_PATTERN, "trial_id", "<TRIAL_ID>")
    replace(GENE_PATTERN, "gene", "<GENE>")
    replace(EXPLICIT_GENE_PATTERN, "gene", "<GENE>")
    masked = masked.lower().replace("<trial_id>", "<TRIAL_ID>").replace("<gene>", "<GENE>")
    masked = re.sub(r"<GENE>\s*-?\s*(?=[a-z])", "<GENE> ", masked)
    replace(URL_PATTERN, "url", "<URL>")
    replace(EMAIL_PATTERN, "email", "<EMAIL>")
    for pattern in DATE_PATTERNS:
        replace(pattern, "date", "<DATE>")
    replace(PVAL_PATTERN, "pvalue", "<PVAL>")
    replace(PERCENT_PATTERN, "percent", "<PCT>")
    replace(DISEASE_PATTERN, "disease", "<DISEASE>")
    replace(BIOMARKER_PATTERN, "biomarker", "<BIOMARKER>")
    replace(DRUG_SUFFIX_PATTERN, "drug", "<DRUG>")
    replace(NUMBER_PATTERN, "number", "<NUM>")
    return normalize_whitespace(masked), {
        label: tuple(values) for label, values in captured.items()
    }


def _representation(record: ParsedRecord) -> _Representation:
    original = record.abstract_text or record.title or record.raw_text
    comparison_text = _non_generic_text(original)
    skeleton, entities = _mask_and_capture(comparison_text)
    section_skeletons: dict[str, str] = {}
    for item in record.abstract_sections:
        section_text = _non_generic_text(item.get("text", ""))
        if section_text:
            section_skeletons[normalize_label(item.get("section", "")) or "Abstract"] = (
                _mask_and_capture(section_text)[0]
            )
    if not section_skeletons and original:
        section_skeletons["Abstract"] = skeleton
    placeholder_count = len(PLACEHOLDER_TOKEN_RE.findall(skeleton))
    meaningful_word_count = sum(token not in PLACEHOLDER_TOKENS for token in text_tokens(skeleton))
    denominator = placeholder_count + meaningful_word_count
    return _Representation(
        original=original,
        original_normalized=normalize_for_matching(original),
        normalized=normalize_for_matching(comparison_text),
        skeleton=skeleton,
        section_skeletons=section_skeletons,
        placeholder_count=placeholder_count,
        placeholder_ratio=placeholder_count / denominator if denominator else 1.0,
        meaningful_word_count=meaningful_word_count,
        entities=entities,
    )


def _section_candidate_pairs(
    representations: dict[str, _Representation],
) -> set[tuple[str, str]]:
    blocks: dict[tuple[str, str], list[str]] = defaultdict(list)
    for record_id, representation in representations.items():
        for section, skeleton in representation.section_skeletons.items():
            if len(text_tokens(skeleton)) >= 5:
                blocks[
                    normalize_for_matching(section),
                    blake2b(skeleton.encode(), digest_size=16).hexdigest(),
                ].append(record_id)
    return {
        pair
        for members in blocks.values()
        if len(members) >= 2
        for pair in combinations(sorted(members), 2)
    }


def _ngram_similarity(left: str, right: str, size: int = 5) -> float:
    def shingles(text: str) -> set[tuple[str, ...]]:
        tokens = text_tokens(text)
        return {tuple(tokens[index:index + size]) for index in range(len(tokens) - size + 1)}

    left_shingles, right_shingles = shingles(left), shingles(right)
    if not left_shingles or not right_shingles:
        return 0.0
    return len(left_shingles & right_shingles) / min(len(left_shingles), len(right_shingles))


def _section_scores(left: _Representation, right: _Representation) -> dict[str, float]:
    return {
        section: _similarity(left.section_skeletons[section], right.section_skeletons[section])
        for section in left.section_skeletons.keys() & right.section_skeletons.keys()
        if normalize_for_matching(section) != "abstract"
    }


def _section_evidence(scores: dict[str, float]) -> tuple[float, float]:
    weighted = [
        (score, weight)
        for section, score in scores.items()
        for label, weight in SECTION_WEIGHTS.items()
        if label in normalize_for_matching(section)
    ]
    weighted_score = (
        sum(score * weight for score, weight in weighted) / sum(weight for _, weight in weighted)
        if weighted
        else 0.0
    )
    high_value = max(
        (
            score
            for section, score in scores.items()
            if any(label in normalize_for_matching(section) for label in HIGH_VALUE_SECTIONS)
        ),
        default=0.0,
    )
    return weighted_score, high_value


def _substitutions(left: _Representation, right: _Representation) -> tuple[int, str]:
    changes: list[str] = []
    count = 0
    for label in sorted(MEANINGFUL_ENTITY_TYPES):
        left_values = left.entities.get(label, ())
        right_values = right.entities.get(label, ())
        if left_values == right_values or (not left_values and not right_values):
            continue
        width = max(len(left_values), len(right_values))
        pairs = [
            (left_values[index] if index < len(left_values) else "none",
             right_values[index] if index < len(right_values) else "none")
            for index in range(width)
        ]
        changed = list(dict.fromkeys(
            (old, new)
            for old, new in pairs
            if normalize_for_matching(old) != normalize_for_matching(new)
        ))
        if changed:
            count += len(changed)
            changes.append(f"{label}: " + " | ".join(f"{old} -> {new}" for old, new in changed[:4]))
    return count, "; ".join(changes)


def _shared_skeleton_words(left: str, right: str) -> int:
    left_tokens, right_tokens = text_tokens(left), text_tokens(right)
    return sum(
        sum(token not in PLACEHOLDER_TOKENS for token in left_tokens[block.a:block.a + block.size])
        for block in SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).get_matching_blocks()
    )


def _relationship_context(left: ParsedRecord, right: ParsedRecord) -> tuple[str, str]:
    left_trials = {value.upper() for value in TRIAL_PATTERN.findall(left.abstract_text)}
    right_trials = {value.upper() for value in TRIAL_PATTERN.findall(right.abstract_text)}
    shared_trials = sorted(left_trials & right_trials)
    shared_authors = {
        normalize_for_matching(value) for value in left.authors
    } & {
        normalize_for_matching(value) for value in right.authors
    } - {""}
    shared_affiliations = {
        normalize_for_matching(value) for value in left.affiliations
    } & {
        normalize_for_matching(value) for value in right.affiliations
    } - {""}
    left_explicit = bool(EXPLICIT_RELATION_RE.search(left.abstract_text))
    right_explicit = bool(EXPLICIT_RELATION_RE.search(right.abstract_text))
    explicit_relation = left_explicit and right_explicit
    context: list[str] = []
    if shared_trials:
        context.append(f"shared trial ID: {', '.join(shared_trials)}")
    if explicit_relation:
        context.append("both abstracts explicitly declare a related study or cohort")
    elif left_explicit or right_explicit:
        context.append("one abstract mentions a related study without pair-level confirmation")
    if shared_authors:
        context.append(f"overlapping authors: {len(shared_authors)}")
    if shared_affiliations:
        context.append(f"shared affiliations: {len(shared_affiliations)}")
    if shared_trials or explicit_relation:
        strength = "expected"
    elif shared_authors or shared_affiliations:
        strength = "possible"
    else:
        strength = "none"
    return (
        "; ".join(context) or "no shared trial ID, explicit study link, authors, or affiliations found",
        strength,
    )


def determine_confidence(
    masked_similarity: float,
    original_similarity: float,
    substitution_count: int,
    high_value_section_similarity: float,
    *,
    masked_similarity_threshold: float = MASKED_SIMILARITY_THRESHOLD,
    original_support_threshold: float = ORIGINAL_SUPPORT_THRESHOLD,
    section_similarity_threshold: float = SECTION_SIMILARITY_THRESHOLD,
) -> str:
    if (
        masked_similarity >= max(0.95, masked_similarity_threshold)
        and original_similarity >= max(0.65, original_support_threshold)
        and substitution_count >= 2
    ):
        return "very_high"
    if (
        (
            masked_similarity >= masked_similarity_threshold
            or high_value_section_similarity >= section_similarity_threshold
        )
        and original_similarity >= original_support_threshold
        and (
            substitution_count >= 1
            or high_value_section_similarity >= section_similarity_threshold
        )
    ):
        return "high"
    return "medium"


def _severity(confidence: str, relation_strength: str, high_value_similarity: float) -> str:
    if relation_strength == "expected":
        return "low"
    if relation_strength == "possible":
        return "medium"
    if confidence in {"very_high", "high"} and high_value_similarity >= 0.90:
        return "high"
    return "medium"


def detect_entity_normalized_templates(
    records: list[ParsedRecord],
    *,
    masked_similarity_threshold: float = MASKED_SIMILARITY_THRESHOLD,
    original_support_threshold: float = ORIGINAL_SUPPORT_THRESHOLD,
    minimum_skeleton_words: int = MINIMUM_SKELETON_WORDS,
    maximum_placeholder_ratio: float = MAXIMUM_PLACEHOLDER_RATIO,
    minimum_substitutions: int = MINIMUM_SUBSTITUTIONS,
    section_similarity_threshold: float = SECTION_SIMILARITY_THRESHOLD,
) -> list[EntityNormalizedTemplateFinding]:
    representations = {record.record_id: _representation(record) for record in records}
    lookup = {record.record_id: record for record in records}
    skeletons = {record_id: item.skeleton for record_id, item in representations.items()}
    normalized = {record_id: item.normalized for record_id, item in representations.items()}
    candidates = _candidate_pairs(records, skeletons, normalized)
    candidates.update(_section_candidate_pairs(representations))

    findings: list[EntityNormalizedTemplateFinding] = []
    for left_id, right_id in sorted(candidates):
        left, right = representations[left_id], representations[right_id]
        if not left.normalized or not right.normalized:
            continue
        if left.original_normalized == right.original_normalized:
            continue
        if max(left.placeholder_ratio, right.placeholder_ratio) > maximum_placeholder_ratio:
            continue
        if _content_class(lookup[left_id], left.skeleton) in {"empty_or_unusable", "administrative_boilerplate"}:
            continue
        if _content_class(lookup[right_id], right.skeleton) in {"empty_or_unusable", "administrative_boilerplate"}:
            continue
        if min(left.meaningful_word_count, right.meaningful_word_count) < minimum_skeleton_words:
            continue

        masked_similarity = _similarity(left.skeleton, right.skeleton)
        original_similarity = _similarity(left.normalized, right.normalized)
        ngram_similarity = _ngram_similarity(left.skeleton, right.skeleton)
        scores = _section_scores(left, right)
        weighted_section_similarity, high_value_section_similarity = _section_evidence(scores)
        matched_sections = sorted(
            section for section, score in scores.items() if score >= section_similarity_threshold
        )
        matched_high_value = [
            section
            for section in matched_sections
            if any(label in normalize_for_matching(section) for label in HIGH_VALUE_SECTIONS)
        ]
        methods_only = bool(scores) and not matched_high_value and max(
            (
                score
                for section, score in scores.items()
                if "method" in normalize_for_matching(section)
            ),
            default=0.0,
        ) >= section_similarity_threshold
        substitution_count, substitutions = _substitutions(left, right)
        shared_word_count = _shared_skeleton_words(left.skeleton, right.skeleton)
        section_trigger = (
            len(matched_high_value) >= 2
            and high_value_section_similarity >= section_similarity_threshold
        )
        if (
            original_similarity < original_support_threshold
            or substitution_count < minimum_substitutions
            or shared_word_count < minimum_skeleton_words
            or methods_only
            or not (masked_similarity >= masked_similarity_threshold or section_trigger)
        ):
            continue

        confidence = determine_confidence(
            masked_similarity,
            original_similarity,
            substitution_count,
            high_value_section_similarity,
            masked_similarity_threshold=masked_similarity_threshold,
            original_support_threshold=original_support_threshold,
            section_similarity_threshold=section_similarity_threshold,
        )
        relationship, relationship_strength = _relationship_context(
            lookup[left_id], lookup[right_id]
        )
        severity = _severity(confidence, relationship_strength, high_value_section_similarity)
        match_type = (
            "exact_masked_skeleton"
            if masked_similarity == 1.0
            else "shared_high_value_sections"
            if section_trigger
            else "entity_value_substitution"
        )
        evidence = (
            f"The abstracts have {masked_similarity:.0%} masked-skeleton similarity and "
            f"{original_similarity:.0%} original-text similarity, with "
            f"{substitution_count} changed study-specific value(s)"
            + (f"; matched sections: {', '.join(matched_sections)}." if matched_sections else ".")
        )
        review_reason = (
            "The abstracts retain highly similar wording after study-specific values are "
            f"replaced. {relationship}. Review whether the submissions were independently prepared."
        )
        findings.append(
            EntityNormalizedTemplateFinding(
                pair_id=f"ENT-{len(findings) + 1:05d}",
                check_type="entity_normalized_template",
                check_triggered=True,
                evidence=evidence,
                severity=severity,
                confidence=confidence,
                matched_source_type="internal_abstract",
                matched_source_id=right_id,
                review_reason=review_reason,
                record_id=left_id,
                matched_record_id=right_id,
                source_file=lookup[left_id].source_file,
                matched_source_file=lookup[right_id].source_file,
                title=lookup[left_id].title,
                matched_title=lookup[right_id].title,
                match_type=match_type,
                masked_skeleton_similarity=round(masked_similarity, 3),
                original_text_similarity=round(original_similarity, 3),
                ngram_similarity=round(ngram_similarity, 3),
                weighted_section_similarity=round(weighted_section_similarity, 3),
                high_value_section_similarity=round(high_value_section_similarity, 3),
                matched_sections=matched_sections,
                variable_substitutions=substitutions,
                shared_skeleton_excerpt=_shared_excerpt([left.skeleton, right.skeleton]),
                relationship_context=relationship,
                review_status="candidate",
            )
        )
    return findings
