from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from hashlib import blake2b
from itertools import combinations
from typing import Any

from ..entity_extraction import mask_text
from ..models import ParsedRecord
from ..template_matching_common import (
    PLACEHOLDER_TOKEN_RE,
    PLACEHOLDER_TOKENS,
    SECTION_WEIGHTS,
    TRIAL_PATTERN,
    _candidate_pairs,
    _content_class,
    _sentence_split,
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
LOCAL_MASKED_SIMILARITY_THRESHOLD = 0.75
LOCAL_ORIGINAL_SUPPORT_THRESHOLD = 0.60
LOCAL_SEQUENCE_SIMILARITY_THRESHOLD = 0.63
MODERATE_LOCAL_MASKED_THRESHOLD = 0.63
MODERATE_LOCAL_ORIGINAL_THRESHOLD = 0.54
MODERATE_GLOBAL_MASKED_THRESHOLD = 0.27
MODERATE_SHARED_SKELETON_WORDS = 60
MINIMUM_GLOBAL_ORIGINAL_FOR_LOCAL = 0.20
RARE_TITLE_TOKEN_DOCUMENT_FREQUENCY = 3
# ponytail: calibrated on the 93-pair ASCO baseline; re-fit when that baseline changes.

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
    masked, entities = mask_text(text)
    # Keep the calibrated detector's historical protein/gene equivalence while
    # the exported representation retains the richer protein type.
    masked = masked.replace("<PROTEIN>", "<GENE>")
    masked = re.sub(r"<GENE>\s*-\s*(?=[a-z])", "<GENE> ", masked)
    captured: dict[str, list[str]] = defaultdict(list)
    for entity in entities:
        label = "gene" if entity.entity_type == "protein" else entity.entity_type
        captured[label].append(entity.text.rstrip("-") if label == "gene" else entity.text)
    return masked, {label: tuple(values) for label, values in captured.items()}


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


def _title_candidate_pairs(records: list[ParsedRecord]) -> set[tuple[str, str]]:
    exact: dict[str, list[str]] = defaultdict(list)
    shingles: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for record in records:
        masked = _mask_and_capture(record.title)[0]
        if not masked:
            continue
        tokens = text_tokens(masked)
        exact[blake2b(masked.encode(), digest_size=16).hexdigest()].append(record.record_id)
        for index in range(len(tokens) - 3):
            shingles[tuple(tokens[index:index + 4])].append(record.record_id)

    pairs = {
        pair
        for members in exact.values()
        if 2 <= len(members) <= 50
        for pair in combinations(sorted(set(members)), 2)
    }
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for members in shingles.values():
        unique = sorted(set(members))
        if 2 <= len(unique) <= 50:
            for pair in combinations(unique, 2):
                shared[pair] += 1
    pairs.update(pair for pair, count in shared.items() if count >= 2)
    return pairs


def _rare_title_tokens(records: list[ParsedRecord]) -> dict[str, set[str]]:
    titles = {
        record.record_id: set(text_tokens(normalize_for_matching(record.title)))
        for record in records
    }
    frequency = Counter(token for tokens in titles.values() for token in tokens)
    return {
        record_id: {token for token in tokens if frequency[token] <= RARE_TITLE_TOKEN_DOCUMENT_FREQUENCY}
        for record_id, tokens in titles.items()
    }


def _rare_title_candidate_pairs(records: list[ParsedRecord]) -> set[tuple[str, str]]:
    tokens = _rare_title_tokens(records)
    return {
        pair
        for pair in combinations(sorted(tokens), 2)
        if len(tokens[pair[0]] & tokens[pair[1]]) >= 2
    }


def _assay_signature(text: str) -> set[str]:
    normalized = normalize_for_matching(text)
    patterns = {
        "western_blot": ("western blot",),
        "mtt": ("mtt",),
        "colony_formation": ("colony formation", "cell colonies"),
        "transwell": ("transwell",),
        "chip": ("chromatin immunoprecipitation", "chip"),
        "rt_qpcr": ("rt qpcr", "qrt pcr", "quantitative pcr"),
    }
    return {name for name, values in patterns.items() if any(value in normalized for value in values)}


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


def _local_windows(representation: _Representation, size: int) -> list[tuple[str, str]]:
    sentences = _sentence_split(_non_generic_text(representation.original))
    return [
        (
            normalize_for_matching(" ".join(sentences[index:index + size])),
            _mask_and_capture(" ".join(sentences[index:index + size]))[0],
        )
        for index in range(len(sentences) - size + 1)
    ]


@lru_cache(maxsize=4096)
def _token_counts(text: str) -> Counter[str]:
    return Counter(text_tokens(text))


def _could_reach_similarity(left: str, right: str, threshold: float) -> bool:
    left_counts, right_counts = _token_counts(left), _token_counts(right)
    total = sum(left_counts.values()) + sum(right_counts.values())
    return bool(total) and 2 * sum((left_counts & right_counts).values()) / total >= threshold


def _local_window_similarity(
    left_windows: list[tuple[str, str]],
    right_windows: list[tuple[str, str]],
) -> tuple[float, float]:
    best = (0.0, 0.0)
    for left_original, left_masked in left_windows:
        for right_original, right_masked in right_windows:
            if not _could_reach_similarity(left_masked, right_masked, MODERATE_LOCAL_MASKED_THRESHOLD):
                continue
            masked = _similarity(left_masked, right_masked)
            if masked > best[0]:
                best = masked, _similarity(left_original, right_original)
    return best


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
    rare_title_tokens: dict[str, set[str]] | None = None,
) -> list[EntityNormalizedTemplateFinding]:
    representations = {record.record_id: _representation(record) for record in records}
    local_windows = {
        record_id: {size: _local_windows(representation, size) for size in (1, 2)}
        for record_id, representation in representations.items()
    }
    lookup = {record.record_id: record for record in records}
    skeletons = {record_id: item.skeleton for record_id, item in representations.items()}
    normalized = {record_id: item.normalized for record_id, item in representations.items()}
    candidates = _candidate_pairs(records, skeletons, normalized)
    candidates.update(_section_candidate_pairs(representations))
    candidates.update(_title_candidate_pairs(records))
    candidates.update(_rare_title_candidate_pairs(records))
    title_token_map = rare_title_tokens or _rare_title_tokens(records)

    findings: list[EntityNormalizedTemplateFinding] = []
    for left_id, right_id in sorted(candidates):
        left, right = representations[left_id], representations[right_id]
        title_tokens = title_token_map[left_id] & title_token_map[right_id]
        shared_assays = _assay_signature(left.original) & _assay_signature(right.original)
        assay_trigger = len(title_tokens) >= 2 and len(shared_assays) >= 5
        short_title_trigger = (
            min(left.meaningful_word_count, right.meaningful_word_count) < minimum_skeleton_words
            and len(title_tokens) >= 2
            and any(any(character.isalpha() for character in token) and any(character.isdigit() for character in token) for token in title_tokens)
            and any(token.isdigit() for token in title_tokens)
        )
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
        if (
            min(left.meaningful_word_count, right.meaningful_word_count) < minimum_skeleton_words
            and not short_title_trigger
        ):
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
        if (
            substitution_count < minimum_substitutions
            or shared_word_count < minimum_skeleton_words
        ) and not (assay_trigger or short_title_trigger):
            continue
        local_masked, local_original = _local_window_similarity(
            local_windows[left_id][1], local_windows[right_id][1]
        )
        sequence_masked, sequence_original = _local_window_similarity(
            local_windows[left_id][2], local_windows[right_id][2]
        )
        section_trigger = (
            len(matched_high_value) >= 2
            and high_value_section_similarity >= section_similarity_threshold
        )
        global_trigger = (
            original_similarity >= original_support_threshold
            and not methods_only
            and (masked_similarity >= masked_similarity_threshold or section_trigger)
        )
        local_trigger = (
            (local_masked >= LOCAL_MASKED_SIMILARITY_THRESHOLD
             and local_original >= LOCAL_ORIGINAL_SUPPORT_THRESHOLD)
            or (sequence_masked >= LOCAL_SEQUENCE_SIMILARITY_THRESHOLD
                and sequence_original >= LOCAL_ORIGINAL_SUPPORT_THRESHOLD)
            or (local_masked >= MODERATE_LOCAL_MASKED_THRESHOLD
                and local_original >= MODERATE_LOCAL_ORIGINAL_THRESHOLD
                and masked_similarity >= MODERATE_GLOBAL_MASKED_THRESHOLD
                and shared_word_count >= MODERATE_SHARED_SKELETON_WORDS)
        ) and original_similarity >= MINIMUM_GLOBAL_ORIGINAL_FOR_LOCAL
        if not (global_trigger or local_trigger or assay_trigger or short_title_trigger):
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
        if local_trigger and confidence == "medium" and local_masked >= 0.85:
            confidence = "high"
        if assay_trigger:
            confidence = "high"
        relationship, relationship_strength = _relationship_context(
            lookup[left_id], lookup[right_id]
        )
        severity = _severity(confidence, relationship_strength, high_value_section_similarity)
        match_type = (
            "exact_masked_skeleton"
            if masked_similarity == 1.0
            else "shared_high_value_sections"
            if section_trigger
            else "local_entity_substitution"
            if local_trigger
            else "shared_assay_protocol"
            if assay_trigger
            else "title_related_duplicate"
            if short_title_trigger
            else "entity_value_substitution"
        )
        evidence = (
            f"The abstracts have {masked_similarity:.0%} masked-skeleton similarity and "
            f"{original_similarity:.0%} original-text similarity, with "
            f"{substitution_count} changed study-specific value(s); the strongest local "
            f"match has {local_masked:.0%} masked and {local_original:.0%} original similarity"
            + (f"; shared assay signature: {', '.join(sorted(shared_assays))}" if assay_trigger else "")
            + (f"; shared rare title tokens: {', '.join(sorted(title_tokens))}" if short_title_trigger else "")
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
