from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any

from ..models import ParsedRecord
from ..template_matching_common import TRIAL_PATTERN, _candidate_pairs, _sentence_split, _similarity
from ..utils import normalize_for_matching, normalize_label, normalize_whitespace, text_tokens


MIN_SENTENCE_WORDS = 10
MIN_SHARED_BLOCK_WORDS = 30
MAX_SENTENCE_DOCUMENT_FREQUENCY = 10
MEDIUM_COVERAGE = 0.15
HIGH_COVERAGE = 0.30
# ponytail: one shared sentence needs stronger coverage; calibrate on labelled editorial data.
MIN_SINGLE_SENTENCE_COVERAGE = 0.40
MIN_RARE_PHRASE_WORDS = 10
MAX_RARE_PHRASE_DOCUMENT_FREQUENCY = 2
MIN_RARE_PHRASE_SENTENCE_SIMILARITY = 0.57
GENERIC_SENTENCES = {
    normalize_for_matching(text)
    for text in (
        "Further studies are needed.",
        "The results were statistically significant.",
        "Patients provided informed consent.",
    )
}
GENERIC_SENTENCE_RE = re.compile(
    r"^(?:"
    r"research sponsors?\b|"
    r"(?:clinical )?trial (?:registration|information)\b|"
    r"(?:this (?:study|work) (?:was )?)?funded by\b|"
    r"(?:conflicts? of interest|disclosures?)\b|"
    r"(?:the )?data cut(?:off| off) (?:date )?(?:was|is)\b|"
    r"(?:all )?patients? (?:provided|gave) (?:written )?informed consent\b"
    r")",
    re.IGNORECASE,
)
GENERIC_RARE_PHRASE_RE = re.compile(
    r"(?:"
    r"(?:recurrence free survival rfs )?(?:was|were) estimated using the kaplan meier method and|"
    r"estimated using kaplan meier methods and compared (?:with|using) (?:the )?log rank tests?|"
    r"kaplan meier methods and compared using the log rank test|"
    r"(?:multivariable )?logistic regression (?:analyses were|was) used to (?:identify factors associated with|estimate adjusted odds ratios)|"
    r"a two sided p value 0 05 was considered statistically significant|"
    r"(?:hormone|estrogen) receptor positive human epidermal growth factor receptor 2 negative|"
    r"absence of residual invasive disease in the breast and axillary lymph nodes|"
    r"data were abstracted from the electronic medical record outcomes were summarized using descriptive statistics|"
    r"breast cancer is one of the most common cancers among women|"
    r"is a leading cause of cancer related mortality in the united states|"
    r"remain limited we conducted a retrospective cohort study using the trinetx"
    r")",
    re.IGNORECASE,
)
RELATED_ANALYSIS_RE = re.compile(
    r"\b(?:subgroup|secondary|interim|final|follow[- ]up|post hoc)\s+analys(?:is|es)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ExactTextReuseFinding:
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
    matched_sentence_count: int
    shared_text_coverage: float
    matched_sections: list[str]
    relationship_context: str
    review_status: str
    matched_text_blocks: list[str]
    record_matched_sentences: list[str]
    matched_record_sentences: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _record_text(record: ParsedRecord) -> str:
    return record.abstract_text or record.title or record.raw_text


def _sections(record: ParsedRecord) -> dict[str, str]:
    sections: dict[str, list[str]] = defaultdict(list)
    for item in record.abstract_sections:
        label = normalize_label(item.get("section", "")) or "Abstract"
        text = normalize_whitespace(item.get("text", ""))
        if text:
            sections[label].append(text)
    if not sections and _record_text(record):
        sections["Abstract"].append(_record_text(record))
    return {label: " ".join(parts) for label, parts in sections.items()}


def _is_generic_sentence(sentence: str) -> bool:
    normalized = normalize_for_matching(sentence)
    scientific_match = GENERIC_RARE_PHRASE_RE.search(normalized)
    return (
        normalized in GENERIC_SENTENCES
        or bool(GENERIC_SENTENCE_RE.match(normalized))
        or (
            scientific_match is not None
            and len(text_tokens(normalized)) <= len(text_tokens(scientific_match.group(0))) + 4
        )
    )


def _non_generic_text(text: str) -> str:
    return " ".join(sentence for sentence in _sentence_split(text) if not _is_generic_sentence(sentence))


def _sentences(record: ParsedRecord) -> dict[str, str]:
    output: dict[str, str] = {}
    for text in _sections(record).values():
        for sentence in _sentence_split(text):
            normalized = normalize_for_matching(sentence)
            if normalized:
                output.setdefault(normalized, sentence)
    return output


def _ngrams(text: str, size: int) -> set[tuple[str, ...]]:
    tokens = text_tokens(text)
    return {tuple(tokens[index:index + size]) for index in range(len(tokens) - size + 1)}


def _best_sentence_similarity(left: str, right: str) -> float:
    return max(
        (
            _similarity(normalize_for_matching(left_sentence), normalize_for_matching(right_sentence))
            for left_sentence in _sentence_split(left)
            for right_sentence in _sentence_split(right)
        ),
        default=0.0,
    )


def _shared_coverage(
    left: str,
    right: str,
    shared_sentences: set[str],
    min_shared_block_words: int,
) -> tuple[float, list[str]]:
    left_tokens = text_tokens(normalize_for_matching(left))
    right_tokens = text_tokens(normalize_for_matching(right))
    denominator = min(len(left_tokens), len(right_tokens))
    if not denominator:
        return 0.0, []
    blocks = [
        block
        for block in SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).get_matching_blocks()
        if block.size >= min_shared_block_words
    ]
    contiguous = sum(block.size for block in blocks)
    reordered = sum(len(text_tokens(sentence)) for sentence in shared_sentences)
    coverage = min(1.0, max(contiguous, reordered) / denominator)
    return coverage, [" ".join(left_tokens[block.a:block.a + block.size]) for block in blocks]


def _relationship_context(left: ParsedRecord, right: ParsedRecord) -> tuple[str, str]:
    left_trials = {value.upper() for value in TRIAL_PATTERN.findall(_record_text(left))}
    right_trials = {value.upper() for value in TRIAL_PATTERN.findall(_record_text(right))}
    shared_trials = sorted(left_trials & right_trials)
    shared_authors = sorted(
        set(map(normalize_for_matching, left.authors)) & set(map(normalize_for_matching, right.authors))
        - {""}
    )
    shared_affiliations = sorted(
        set(map(normalize_for_matching, left.affiliations))
        & set(map(normalize_for_matching, right.affiliations))
        - {""}
    )
    contexts: list[str] = []
    if shared_trials:
        contexts.append(f"shared trial ID: {', '.join(shared_trials)}")
    if shared_authors:
        contexts.append(f"overlapping authors: {len(shared_authors)}")
    if shared_affiliations:
        contexts.append(f"shared affiliations: {len(shared_affiliations)}")
    if RELATED_ANALYSIS_RE.search(_record_text(left)) or RELATED_ANALYSIS_RE.search(_record_text(right)):
        contexts.append("declared related analysis")
    relationship = "; ".join(contexts) or "no shared trial, authors, affiliations, or declared analysis found"
    if shared_trials:
        return relationship, "expected"
    if shared_authors or shared_affiliations:
        return relationship, "possible"
    return relationship, "none"


def _determine_confidence(match_type: str, matched_sentence_count: int, shared_text_coverage: float) -> str:
    if match_type in {"exact_full_abstract", "exact_results_section", "exact_methods_section"}:
        return "very_high"
    if matched_sentence_count >= 3 or shared_text_coverage >= HIGH_COVERAGE:
        return "high"
    if matched_sentence_count >= 2 or shared_text_coverage >= MEDIUM_COVERAGE:
        return "medium"
    return "low"


def _determine_severity(confidence: str, relation_strength: str) -> str:
    if relation_strength == "expected":
        return "low"
    if relation_strength == "possible":
        return "medium" if confidence in {"very_high", "high"} else "low"
    return "high" if confidence in {"very_high", "high"} else "medium"


def _build_evidence(
    match_type: str,
    matched_sentence_count: int,
    shared_text_coverage: float,
    matched_sections: list[str],
) -> str:
    parts = [
        match_type.replace("_", " "),
        f"{matched_sentence_count} uncommon sentence(s) matched",
        f"{shared_text_coverage:.0%} shared-text coverage",
    ]
    if matched_sections:
        parts.append(f"matched sections: {', '.join(matched_sections)}")
    return "; ".join(parts) + "."


def detect_exact_text_reuse(
    records: list[ParsedRecord],
    *,
    min_sentence_words: int = MIN_SENTENCE_WORDS,
    min_shared_sentences: int = 2,
    min_shared_coverage: float = MEDIUM_COVERAGE,
    min_shared_block_words: int = MIN_SHARED_BLOCK_WORDS,
    max_sentence_frequency: int = MAX_SENTENCE_DOCUMENT_FREQUENCY,
) -> list[ExactTextReuseFinding]:
    high_coverage = max(HIGH_COVERAGE, min_shared_coverage)
    lookup = {record.record_id: record for record in records}
    reuse_texts = {record.record_id: _non_generic_text(_record_text(record)) for record in records}
    normalized = {record_id: normalize_for_matching(text) for record_id, text in reuse_texts.items()}
    phrase_ngrams = {
        record_id: _ngrams(text, MIN_RARE_PHRASE_WORDS)
        for record_id, text in normalized.items()
    }
    phrase_frequency = Counter(
        ngram for record_ngrams in phrase_ngrams.values() for ngram in record_ngrams
    )
    sentence_maps = {record.record_id: _sentences(record) for record in records}
    sentence_documents: dict[str, set[str]] = defaultdict(set)
    for record_id, sentences in sentence_maps.items():
        for sentence in sentences:
            sentence_documents[sentence].add(record_id)
    sentence_frequency = Counter({sentence: len(ids) for sentence, ids in sentence_documents.items()})

    candidates = _candidate_pairs(records, normalized, normalized)
    for sentence, member_ids in sentence_documents.items():
        if (
            len(text_tokens(sentence)) >= min_sentence_words
            and 2 <= sentence_frequency[sentence] <= max_sentence_frequency
            and not _is_generic_sentence(sentence)
        ):
            candidates.update(combinations(sorted(member_ids), 2))

    findings: list[ExactTextReuseFinding] = []
    for left_id, right_id in sorted(candidates):
        left = lookup[left_id]
        right = lookup[right_id]
        uncommon = {
            sentence
            for sentence in sentence_maps[left_id].keys() & sentence_maps[right_id].keys()
            if len(text_tokens(sentence)) >= min_sentence_words
            and 2 <= sentence_frequency[sentence] <= max_sentence_frequency
            and not _is_generic_sentence(sentence)
        }
        left_sections = _sections(left)
        right_sections = _sections(right)
        exact_sections = sorted(
            section
            for section in left_sections.keys() & right_sections.keys()
            if len(text_tokens(_non_generic_text(left_sections[section]))) >= min_sentence_words
            and normalize_for_matching(_non_generic_text(left_sections[section]))
            == normalize_for_matching(_non_generic_text(right_sections[section]))
        )
        coverage, matched_blocks = _shared_coverage(
            reuse_texts[left_id], reuse_texts[right_id], uncommon, min_shared_block_words
        )
        _, phrase_blocks = _shared_coverage(
            reuse_texts[left_id], reuse_texts[right_id], set(), MIN_RARE_PHRASE_WORDS
        )
        phrase_blocks = [block for block in phrase_blocks if not GENERIC_RARE_PHRASE_RE.search(block)]
        phrase_coverage = sum(len(text_tokens(block)) for block in phrase_blocks) / max(
            1,
            min(len(text_tokens(normalized[left_id])), len(text_tokens(normalized[right_id]))),
        )
        longest_phrase = max((len(text_tokens(block)) for block in phrase_blocks), default=0)
        phrase_block_ngrams = {ngram for block in phrase_blocks for ngram in _ngrams(block, MIN_RARE_PHRASE_WORDS)}
        has_rare_phrase = any(
            phrase_frequency[ngram] <= MAX_RARE_PHRASE_DOCUMENT_FREQUENCY
            for ngram in phrase_ngrams[left_id] & phrase_ngrams[right_id] & phrase_block_ngrams
        )
        rare_phrase = has_rare_phrase and (
            longest_phrase > MIN_RARE_PHRASE_WORDS
            or (
                longest_phrase == MIN_RARE_PHRASE_WORDS
                and _best_sentence_similarity(reuse_texts[left_id], reuse_texts[right_id])
                >= MIN_RARE_PHRASE_SENTENCE_SIMILARITY
            )
        )
        exact_full = bool(normalized[left_id] and normalized[left_id] == normalized[right_id])
        section_labels = {normalize_for_matching(section) for section in exact_sections}

        if exact_full:
            match_type = "exact_full_abstract"
        elif any("result" in section for section in section_labels):
            match_type = "exact_results_section"
        elif any("method" in section for section in section_labels):
            match_type = "exact_methods_section"
        elif len(uncommon) >= min_shared_sentences:
            match_type = "multiple_uncommon_sentences"
        elif rare_phrase:
            match_type = "rare_exact_phrase"
            coverage, matched_blocks = phrase_coverage, phrase_blocks
        elif coverage >= high_coverage:
            match_type = "substantial_shared_text"
        elif coverage >= min_shared_coverage:
            match_type = "partial_or_reordered_reuse"
        else:
            continue
        if (
            len(uncommon) == 1
            and not exact_full
            and not exact_sections
            and not rare_phrase
            and coverage < MIN_SINGLE_SENTENCE_COVERAGE
        ):
            continue

        relationship, relation_strength = _relationship_context(left, right)
        confidence = _determine_confidence(match_type, len(uncommon), coverage)
        if match_type == "rare_exact_phrase":
            confidence = "high" if longest_phrase >= 12 else "medium"
        severity = _determine_severity(confidence, relation_strength)
        evidence = _build_evidence(match_type, len(uncommon), coverage, exact_sections)
        reason = (
            f"{match_type.replace('_', ' ')}; {len(uncommon)} uncommon sentence(s), "
            f"{coverage:.0%} shared-text coverage; {relationship}."
        )
        ordered_sentences = sorted(uncommon)
        findings.append(
            ExactTextReuseFinding(
                pair_id=f"ETR-{len(findings) + 1:05d}",
                check_type="exact_text_reuse",
                check_triggered=True,
                evidence=evidence,
                severity=severity,
                confidence=confidence,
                matched_source_type="internal_abstract",
                matched_source_id=right_id,
                review_reason=reason,
                record_id=left_id,
                matched_record_id=right_id,
                source_file=left.source_file,
                matched_source_file=right.source_file,
                title=left.title,
                matched_title=right.title,
                match_type=match_type,
                matched_sentence_count=len(uncommon),
                shared_text_coverage=round(coverage, 3),
                matched_sections=exact_sections,
                relationship_context=relationship,
                review_status="candidate",
                matched_text_blocks=matched_blocks,
                record_matched_sentences=[sentence_maps[left_id][sentence] for sentence in ordered_sentences],
                matched_record_sentences=[sentence_maps[right_id][sentence] for sentence in ordered_sentences],
            )
        )
    return findings
