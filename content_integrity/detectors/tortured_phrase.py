from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from ..models import Finding, ParsedRecord
from ..utils import (
    evidence_snippet,
    normalize_for_matching,
    normalize_whitespace,
    safe_int,
    section_for_match,
    strip_outer_quotes,
)


_TOKEN_SPAN_RE = re.compile(r"[^\W_]+", re.UNICODE)
# A period only ends a sentence when whitespace follows, so "5.2" and "e.g." stay inside one window.
_SENTENCE_BREAK_RE = re.compile(r"[.!?](?=\s|$)")


@dataclass(frozen=True, slots=True)
class PhraseMatcher:
    """A dictionary phrase. `proximity` is the Lucene-style ~N slop; 0 means the tokens must be adjacent."""

    phrase: str
    tokens: tuple[str, ...]
    proximity: int
    compiled: re.Pattern[str] = field(repr=False)

    def spans(self, text: str) -> list[tuple[int, int]]:
        if self.proximity and len(self.tokens) > 1:
            return _proximity_spans(text, self.tokens, self.proximity)
        return [(match.start(), match.end()) for match in self.compiled.finditer(text)]

    def present(self, text: str) -> bool:
        return bool(self.spans(text))


def build_phrase_matcher(phrase: str, proximity: int = 0) -> PhraseMatcher:
    return PhraseMatcher(
        phrase=phrase,
        tokens=tuple(match.group().lower() for match in re.finditer(r"[^\W_]+\*?", phrase, re.UNICODE)),
        proximity=proximity,
        compiled=_compile_phrase_regex(phrase),
    )


@dataclass(slots=True)
class TorturedRule:
    rule_id: str
    raw_query: str
    matched_phrase: str
    expected_term: str
    retrieved_papers: int | None
    severity: str
    # Heuristic rule strength, not a calibrated probability. See rule_strength.
    confidence: float
    matcher: PhraseMatcher = field(repr=False)
    required_groups: tuple[tuple[PhraseMatcher, ...], ...] = field(repr=False, default_factory=tuple)
    excluded: tuple[PhraseMatcher, ...] = field(repr=False, default_factory=tuple)
    token_pattern: tuple[str, ...] = field(repr=False, default_factory=tuple)
    index_key: str = ""
    dictionary_source: str = ""
    dictionary_version: str = ""

    @property
    def proximity(self) -> int:
        return self.matcher.proximity

    def to_dict(self) -> dict[str, object]:
        return {
            "detector_type": "tortured_phrase",
            "rule_id": self.rule_id,
            "category": "tortured_phrase",
            "pattern": self.raw_query,
            "matched_phrase": self.matched_phrase,
            "expected_term": self.expected_term,
            "severity": self.severity,
            "confidence": self.confidence,
            "confidence_basis": "heuristic_rule_strength",
            "rule_strength": self.severity,
            "proximity": self.proximity,
            "retrieved_papers": self.retrieved_papers,
            "source": self.dictionary_source,
            "dictionary_version": self.dictionary_version,
        }


def _compile_phrase_regex(phrase: str) -> re.Pattern[str]:
    tokens = list(re.finditer(r"[^\W_]+\*?", phrase.lower(), re.UNICODE))
    if not tokens:
        escaped = re.escape(normalize_whitespace(phrase))
        return re.compile(escaped, re.IGNORECASE | re.UNICODE)
    parts = [
        re.escape(token.group()[:-1]) + r"[^\W_]*" if token.group().endswith("*") else re.escape(token.group())
        for token in tokens
    ]
    pattern = r"(?<!\w)" + parts[0]
    for left, right, part in zip(tokens, tokens[1:], parts[1:]):
        separator = phrase[left.end() : right.start()]
        if any(mark in separator for mark in ".!?"):
            separator_pattern = re.escape(separator).replace(r"\ ", r"\s+")
        else:
            separator_pattern = r"[^\w.!?\r\n]+"
        pattern += separator_pattern + part
    pattern += r"(?!\w)"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower(), re.UNICODE)


def _matches_token(pattern: str, token: str) -> bool:
    if pattern.endswith("*"):
        return token.startswith(pattern[:-1])
    return token == pattern


def _proximity_spans(text: str, tokens: tuple[str, ...], proximity: int) -> list[tuple[int, int]]:
    """Lucene `"a b"~N`: every phrase token inside one N-slop window, any order, one sentence."""
    positions = [
        (match.group().lower(), match.start(), match.end())
        for match in _TOKEN_SPAN_RE.finditer(text)
    ]
    span_limit = proximity + len(tokens) - 1
    spans: list[tuple[int, int]] = []
    for start in range(len(positions)):
        window = range(start, min(len(positions), start + span_limit + 1))
        used: list[int] = []
        for pattern in tokens:
            index = next(
                (item for item in window if item not in used and _matches_token(pattern, positions[item][0])),
                None,
            )
            if index is None:
                break
            used.append(index)
        if len(used) != len(tokens):
            continue
        first, last = min(used), max(used)
        if _SENTENCE_BREAK_RE.search(text, positions[first][2], positions[last][1]):
            continue
        span = (positions[first][1], positions[last][2])
        if spans and span[0] < spans[-1][1]:
            continue
        spans.append(span)
    return spans


def _split_proximity(value: str) -> tuple[str, int]:
    cleaned = normalize_whitespace(value)
    match = re.search(r"~(\d+)\s*$", cleaned)
    if not match:
        return strip_outer_quotes(cleaned), 0
    return strip_outer_quotes(cleaned[: match.start()]), int(match.group(1))


def _query_parts(
    raw_query: str,
) -> tuple[str, int, tuple[tuple[tuple[str, int], ...], ...], tuple[tuple[str, int], ...]]:
    text = normalize_whitespace(raw_query)
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    first = re.search(r'"([^"]+)"', text)
    if first:
        matched_phrase = strip_outer_quotes(first.group(1))
        tail = text[first.end() :].strip()
        proximity_match = re.match(r"~(\d+)", tail)
        proximity = int(proximity_match.group(1)) if proximity_match else 0
        if proximity_match:
            tail = tail[proximity_match.end() :].strip()
        required_groups = []
        for group in re.findall(r"\bAND\b\s*(\([^)]*\)|\"[^\"]+\"|.*?)(?=\s+\b(?:AND|NOT)\b|$)", tail, re.I):
            alternatives = tuple(
                _split_proximity(item) for item in re.findall(r'"[^"]+"(?:~\d+)?', group)
            )
            if not alternatives:
                cleaned, group_proximity = _split_proximity(re.sub(r"[()]", " ", group))
                alternatives = ((cleaned, group_proximity),) if cleaned else ()
            if alternatives:
                required_groups.append(alternatives)
        excluded = []
        for item in re.findall(r"\bNOT\b\s*(\"[^\"]+\"(?:~\d+)?|[^()]*?)(?=\s+\b(?:AND|NOT)\b|$)", tail, re.I):
            cleaned, item_proximity = _split_proximity(item)
            if cleaned:
                excluded.append((cleaned, item_proximity))
        return matched_phrase, proximity, tuple(required_groups), tuple(excluded)
    cleaned = re.sub(r"\b(?:AND|NOT|OR)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[~()]", " ", cleaned)
    cleaned = normalize_whitespace(cleaned)
    return cleaned, 0, (), ()


def load_tortured_rules(csv_path: str | Path, dictionary_version: str = "") -> list[TorturedRule]:
    path = Path(csv_path)
    # Version the dictionary by content so an edited CSV can never silently pass as the previous one.
    content_version = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}"
    version = f"{dictionary_version}+{content_version}" if dictionary_version else content_version
    rules: list[TorturedRule] = []
    high_severity_seed = {
        "artificial cleverness",
        "mechanical learning",
        "profound learning",
        "nervous network",
        "bosom peril",
        "counterfeit consciousness",
    }
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_query = normalize_whitespace(row.get("Fingerprint - Tortured Phrase", ""))
            if not raw_query:
                continue
            matched_phrase, proximity, required_groups, excluded = _query_parts(raw_query)
            expected_term = normalize_whitespace(row.get("Expected Text", ""))
            retrieved_papers = safe_int(row.get("Nb Retrieved Papers"))
            token_pattern = tuple(_tokens(matched_phrase))
            if not token_pattern:
                continue
            if len(token_pattern) < 2 and not (required_groups or excluded):
                continue
            if proximity and len(token_pattern) == 1:
                import warnings
                warnings.warn(
                    f"Rule {raw_query!r} specifies ~{proximity} proximity on a single token "
                    "(unsupported; proximity matching requires 2+ tokens). Operator will be ignored.",
                    stacklevel=2,
                )
            severity = "high" if normalize_for_matching(matched_phrase) in high_severity_seed else "medium"
            confidence = 0.98 if severity == "high" else 0.87
            identity = f"{normalize_for_matching(raw_query)}\0{normalize_for_matching(expected_term)}"
            rule_id = f"TP-{hashlib.sha256(identity.encode()).hexdigest()[:12].upper()}"
            rules.append(
                TorturedRule(
                    rule_id=rule_id,
                    raw_query=raw_query,
                    matched_phrase=matched_phrase,
                    expected_term=expected_term,
                    retrieved_papers=retrieved_papers,
                    severity=severity,
                    confidence=confidence,
                    matcher=build_phrase_matcher(matched_phrase, proximity),
                    required_groups=tuple(
                        tuple(build_phrase_matcher(phrase, slop) for phrase, slop in group)
                        for group in required_groups
                    ),
                    excluded=tuple(build_phrase_matcher(phrase, slop) for phrase, slop in excluded),
                    token_pattern=token_pattern,
                    index_key=_index_key(token_pattern, proximity),
                    dictionary_source=path.name,
                    dictionary_version=version,
                )
            )
    return rules


def _index_key(token_pattern: tuple[str, ...], proximity: int) -> str:
    """Adjacent phrases key on their leading bigram; proximity phrases must key on a single token,
    because their tokens are not adjacent in the text the retrieval index is built from."""
    if proximity or len(token_pattern) < 2:
        return max(token_pattern, key=len)
    return " ".join(token_pattern[:2])


def build_tortured_rule_index(rules: Iterable[TorturedRule]) -> dict[str, list[TorturedRule]]:
    index: dict[str, list[TorturedRule]] = {}
    for rule in rules:
        if not rule.token_pattern:
            continue
        index.setdefault(rule.index_key, []).append(rule)
    return index


def _candidate_tortured_rules(field_text: str, indexed_rules: dict[str, list[TorturedRule]]) -> list[TorturedRule]:
    tokens = _tokens(field_text)
    candidates: list[TorturedRule] = []
    if not tokens:
        return candidates
    keys: set[str] = set(tokens)
    if len(tokens) >= 2:
        keys.update(" ".join(tokens[index : index + 2]) for index in range(len(tokens) - 1))
    for key in keys:
        for rule in indexed_rules.get(key, []):
            candidates.append(rule)
    return candidates


def detect_tortured_phrases(
    record: ParsedRecord,
    rules: Iterable[TorturedRule],
    rule_index: dict[str, list[TorturedRule]] | None = None,
) -> list[Finding]:
    index = rule_index if rule_index is not None else build_tortured_rule_index(rules)
    findings: list[Finding] = []
    seen_matches: set[tuple[str, str, str, str, int, int]] = set()
    for field_name, field_text in (("title", record.title), ("abstract_text", record.abstract_text)):
        if not field_text:
            continue
        for rule in _candidate_tortured_rules(field_text, index):
            if any(not any(matcher.present(field_text) for matcher in group) for group in rule.required_groups):
                continue
            if any(matcher.present(field_text) for matcher in rule.excluded):
                continue
            for start, end in rule.matcher.spans(field_text):
                matched_text = field_text[start:end]
                section = section_for_match(record, field_name, matched_text)
                match_key = (
                    record.record_id,
                    section,
                    rule.rule_id,
                    normalize_for_matching(matched_text),
                    start,
                    end,
                )
                if match_key in seen_matches:
                    continue
                seen_matches.add(match_key)
                findings.append(
                    Finding(
                        finding_id="",
                        record_id=record.record_id,
                        source_file=record.source_file,
                        detector_type="tortured_phrase",
                        category="tortured_phrase",
                        matched_text=matched_text,
                        evidence_snippet=evidence_snippet(field_text, start, end),
                        section_or_field=section,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        rule_id=rule.rule_id,
                        check_type="tortured_phrase",
                        expected_term=rule.expected_term,
                    )
                )
    return findings
