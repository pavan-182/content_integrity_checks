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


@dataclass(slots=True)
class TorturedRule:
    rule_id: str
    raw_query: str
    matched_phrase: str
    expected_term: str
    retrieved_papers: int | None
    severity: str
    confidence: float
    compiled: re.Pattern[str] = field(repr=False)
    required_groups: tuple[tuple[re.Pattern[str], ...], ...] = field(repr=False, default_factory=tuple)
    excluded: tuple[re.Pattern[str], ...] = field(repr=False, default_factory=tuple)
    token_pattern: tuple[str, ...] = field(repr=False, default_factory=tuple)
    index_key: str = ""

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
            "retrieved_papers": self.retrieved_papers,
            "source": "🤷_tortured.csv",
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


def _query_parts(raw_query: str) -> tuple[str, tuple[tuple[str, ...], ...], tuple[str, ...]]:
    text = normalize_whitespace(raw_query)
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    first = re.search(r'"([^"]+)"', text)
    if first:
        matched_phrase = strip_outer_quotes(first.group(1))
        tail = re.sub(r"^~\d+", "", text[first.end() :].strip())
        required_groups = []
        for group in re.findall(r"\bAND\b\s*(\([^)]*\)|\"[^\"]+\"|.*?)(?=\s+\b(?:AND|NOT)\b|$)", tail, re.I):
            alternatives = tuple(strip_outer_quotes(item) for item in re.findall(r'"([^"]+)"', group))
            if not alternatives:
                cleaned = normalize_whitespace(re.sub(r"[()]", " ", group))
                alternatives = (cleaned,) if cleaned else ()
            if alternatives:
                required_groups.append(alternatives)
        excluded = []
        for item in re.findall(r"\bNOT\b\s*(\"[^\"]+\"|[^()]*?)(?=\s+\b(?:AND|NOT)\b|$)", tail, re.I):
            cleaned = strip_outer_quotes(item)
            if cleaned:
                excluded.append(cleaned)
        return matched_phrase, tuple(required_groups), tuple(excluded)
    cleaned = re.sub(r"\b(?:AND|NOT|OR)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[~()]", " ", cleaned)
    cleaned = normalize_whitespace(cleaned)
    return cleaned, (), ()


def load_tortured_rules(csv_path: str | Path) -> list[TorturedRule]:
    path = Path(csv_path)
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
            matched_phrase, required_groups, excluded = _query_parts(raw_query)
            expected_term = normalize_whitespace(row.get("Expected Text", ""))
            retrieved_papers = safe_int(row.get("Nb Retrieved Papers"))
            token_pattern = tuple(_tokens(matched_phrase))
            if not token_pattern:
                continue
            if len(token_pattern) < 2 and not (required_groups or excluded):
                continue
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
                    compiled=_compile_phrase_regex(matched_phrase),
                    required_groups=tuple(
                        tuple(_compile_phrase_regex(phrase) for phrase in group) for group in required_groups
                    ),
                    excluded=tuple(_compile_phrase_regex(phrase) for phrase in excluded),
                    token_pattern=token_pattern,
                    index_key=" ".join(token_pattern[:2]) if len(token_pattern) >= 2 else token_pattern[0],
                )
            )
    return rules


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
    seen: set[str] = set()
    if not tokens:
        return candidates
    keys: set[str] = set(tokens)
    if len(tokens) >= 2:
        keys.update(" ".join(tokens[index : index + 2]) for index in range(len(tokens) - 1))
    for key in keys:
        for rule in indexed_rules.get(key, []):
            if rule.rule_id in seen:
                continue
            seen.add(rule.rule_id)
            candidates.append(rule)
    return candidates


def detect_tortured_phrases(
    record: ParsedRecord,
    rules: Iterable[TorturedRule],
    rule_index: dict[str, list[TorturedRule]] | None = None,
) -> list[Finding]:
    index = rule_index if rule_index is not None else build_tortured_rule_index(rules)
    findings: list[Finding] = []
    for field_name, field_text in (("title", record.title), ("abstract_text", record.abstract_text)):
        if not field_text:
            continue
        for rule in _candidate_tortured_rules(field_text, index):
            if any(not any(pattern.search(field_text) for pattern in group) for group in rule.required_groups):
                continue
            if any(pattern.search(field_text) for pattern in rule.excluded):
                continue
            for match in rule.compiled.finditer(field_text):
                findings.append(
                    Finding(
                        finding_id="",
                        record_id=record.record_id,
                        source_file=record.source_file,
                        detector_type="tortured_phrase",
                        category="tortured_phrase",
                        matched_text=match.group(0),
                        evidence_snippet=evidence_snippet(field_text, match.start(), match.end()),
                        section_or_field=section_for_match(record, field_name, match.group(0)),
                        severity=rule.severity,
                        confidence=rule.confidence,
                        rule_id=rule.rule_id,
                        check_type="tortured_phrase",
                        expected_term=rule.expected_term,
                    )
                )
    return findings
