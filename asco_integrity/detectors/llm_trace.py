from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..models import Finding, ParsedRecord
from ..utils import evidence_snippet

# Kept as an import-compatible marker; global context discounting was removed.
AI_CONTEXT_TERMS: set[str] = set()


@dataclass(slots=True)
class LLMRule:
    rule_id: str
    category: str
    pattern: str
    severity: str
    signal_strength: float
    tier: str
    compiled: re.Pattern[str] = field(repr=False)

    @property
    def confidence(self) -> float:  # compatibility for existing consumers
        return self.signal_strength

    def to_dict(self) -> dict[str, object]:
        return {
            "detector_type": "llm_response_trace",
            "rule_id": self.rule_id,
            "category": self.category,
            "pattern": self.pattern,
            "severity": self.severity,
            "signal_strength": self.signal_strength,
            "source": "built-in",
        }


def built_in_llm_rules() -> list[LLMRule]:
    definitions = [
        ("LLM-001", "ai_self_identification", r"\bas an ai language model\b", "high", .99, "A"),
        ("LLM-002", "ai_self_identification", r"\bi am an ai model\b", "high", .98, "A"),
        ("LLM-003", "knowledge_disclaimer", r"\bas of my last knowledge (?:update|cutoff)\b", "high", .98, "A"),
        ("LLM-004", "knowledge_disclaimer", r"\bmy knowledge cutoff\b", "high", .98, "A"),
        ("LLM-005", "capability_refusal", r"\bi (?:do not|don't) have (?:browsing|access to current|access to real[- ]time)\b", "high", .95, "A"),
        ("LLM-006", "capability_refusal", r"\bi cannot access real[-\s]?time (?:data|information)\b", "high", .95, "A"),
        ("LLM-007", "capability_refusal", r"\bi cannot provide (?:medical advice|patient-specific recommendations|real[-\s]?time information)\b", "medium", .88, "B"),
        ("LLM-008", "response_preamble", r"\bcertainly,?\s+here is\b", "medium", .90, "B"),
        ("LLM-009", "response_preamble", r"\bhere is the revised\b", "medium", .90, "B"),
        ("LLM-010", "response_preamble", r"\bbelow is the rewritten\b", "medium", .90, "B"),
        ("LLM-011", "response_preamble", r"\bsure,?\s+here(?:'s| is)\b", "medium", .88, "B"),
        ("LLM-012", "response_preamble", r"\bcertainly!\s+below is\b", "medium", .90, "B"),
        ("LLM-013", "response_closing", r"\bplease let me know if you would like me to\b", "medium", .88, "B"),
        ("LLM-014", "response_closing", r"\bthe final answer is provided above\b", "medium", .88, "B"),
        ("LLM-015", "response_disclosure", r"\bthis response can be adapted to the requested\b", "medium", .88, "B"),
        ("LLM-016", "response_disclosure", r"\bi have rewritten the text to improve clarity and academic tone\b", "medium", .90, "B"),
        ("LLM-017", "prompt_leakage", r"\brewrite this abstract\b", "medium", .90, "A"),
        ("LLM-018", "prompt_leakage", r"\bimprove grammar\b", "medium", .90, "A"),
        ("LLM-019", "prompt_leakage", r"\bsummarize the following\b", "medium", .90, "A"),
        ("LLM-020", "prompt_leakage", r"\bdo not highlight negatives\b", "medium", .90, "A"),
        ("LLM-021", "prompt_leakage", r"\bpositive review only\b", "medium", .90, "A"),
        ("LLM-022", "interface_residue", r"\bregenerate response\b", "high", .95, "A"),
        ("LLM-023", "interface_residue", r"\bcopy response\b", "medium", .80, "B"),
        ("LLM-024", "interface_residue", r"\bnew chat\b", "low", .55, "B"),
    ]
    return [
        LLMRule(rule_id, category, pattern, severity, strength, tier, re.compile(pattern, re.I | re.U))
        for rule_id, category, pattern, severity, strength, tier in definitions
    ]


def _quoted(text: str, start: int) -> bool:
    return text[:start].count('"') % 2 == 1 or text[:start].count("“") > text[:start].count("”")


def _context(text: str, start: int, end: int) -> str:
    left = max(0, text.rfind(".", 0, start) + 1)
    right = text.find(".", end)
    return text[left : len(text) if right < 0 else right + 1].strip()


def detect_llm_trace(record: ParsedRecord, rules: Iterable[LLMRule]) -> list[Finding]:
    candidates: list[tuple[LLMRule, str, str, re.Match[str]]] = []
    for field_name, field_text in (("title", record.title), ("abstract_text", record.abstract_text)):
        if not field_text:
            continue
        for rule in rules:
            for match in rule.compiled.finditer(field_text):
                # A quoted example is evidence for review, not standalone residue.
                if _quoted(field_text, match.start()):
                    continue
                candidates.append((rule, field_name, field_text, match))

    groups: list[list[tuple[LLMRule, str, str, re.Match[str]]]] = []
    for candidate in sorted(candidates, key=lambda item: (item[1], item[3].start(), -item[0].signal_strength)):
        rule, field_name, field_text, match = candidate
        group = next(
            (group for group in groups if group[0][1] == field_name and _match_overlaps(group, match)),
            None,
        )
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)

    findings: list[Finding] = []
    for group in groups:
        strongest, field_name, field_text, _ = max(group, key=lambda item: item[0].signal_strength)
        start = min(match.start() for _, _, _, match in group)
        end = max(match.end() for _, _, _, match in group)
        findings.append(
            Finding(
                finding_id="",
                record_id=record.record_id,
                source_file=record.source_file,
                detector_type="llm_response_trace",
                category=strongest.category,
                matched_text=field_text[start:end],
                evidence_snippet=_context(field_text, start, end) or evidence_snippet(field_text, start, end),
                section_or_field=field_name,
                severity=strongest.severity,
                confidence=strongest.signal_strength,
                rule_id=";".join(dict.fromkeys(rule.rule_id for rule, *_ in group)),
            )
        )
    return findings


def _match_overlaps(group: list[tuple[LLMRule, str, str, re.Match[str]]], match: re.Match[str]) -> bool:
    return any(match.start() < other.end() and other.start() < match.end() for _, _, _, other in group)
