from __future__ import annotations

import csv
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
    strip_outer_quotes,
    text_tokens,
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
    tokens = text_tokens(phrase)
    if not tokens:
        escaped = re.escape(normalize_whitespace(phrase))
        return re.compile(escaped, re.IGNORECASE | re.UNICODE)
    pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(token) for token in tokens) + r"(?!\w)"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


def _extract_first_quoted_phrase(raw_query: str) -> str:
    text = normalize_whitespace(raw_query)
    text = text.replace("“", '"').replace("”", '"').replace("’", "'")
    candidates = re.findall(r'"([^"]+)"', text)
    if candidates:
        for candidate in candidates:
            cleaned = strip_outer_quotes(candidate)
            if cleaned:
                return cleaned
    cleaned = re.sub(r"\b(?:AND|NOT|OR)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[~()]", " ", cleaned)
    cleaned = normalize_whitespace(cleaned)
    return cleaned


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
        for index, row in enumerate(reader, start=1):
            raw_query = normalize_whitespace(row.get("Fingerprint - Tortured Phrase", ""))
            if not raw_query:
                continue
            matched_phrase = _extract_first_quoted_phrase(raw_query)
            expected_term = normalize_whitespace(row.get("Expected Text", ""))
            retrieved_papers = safe_int(row.get("Nb Retrieved Papers"))
            token_pattern = tuple(text_tokens(matched_phrase))
            if not token_pattern:
                continue
            if len(token_pattern) < 2:
                continue
            severity = "high" if normalize_for_matching(matched_phrase) in high_severity_seed else "medium"
            confidence = 0.98 if severity == "high" else 0.87
            rule_id = f"TP-{index:05d}"
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
    tokens = text_tokens(field_text)
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
    seen: set[tuple[str, str, int, int]] = set()
    for field_name, field_text in (("title", record.title), ("abstract_text", record.abstract_text)):
        if not field_text:
            continue
        for rule in _candidate_tortured_rules(field_text, index):
            for match in rule.compiled.finditer(field_text):
                key = (rule.rule_id, field_name, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    Finding(
                        finding_id="",
                        record_id=record.record_id,
                        source_file=record.source_file,
                        detector_type="tortured_phrase",
                        category="tortured_phrase",
                        matched_text=match.group(0),
                        evidence_snippet=evidence_snippet(field_text, match.start(), match.end()),
                        section_or_field=field_name,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        rule_id=rule.rule_id,
                        expected_term=rule.expected_term,
                    )
                )
    return findings
