from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..models import Finding, ParsedRecord
from ..utils import evidence_snippet


@dataclass(slots=True)
class LLMRule:
    rule_id: str
    category: str
    pattern: str
    severity: str
    confidence: float
    compiled: re.Pattern[str] = field(repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "detector_type": "llm_response_trace",
            "rule_id": self.rule_id,
            "category": self.category,
            "pattern": self.pattern,
            "severity": self.severity,
            "confidence": self.confidence,
            "source": "built-in",
        }


def built_in_llm_rules() -> list[LLMRule]:
    definitions = [
        ("LLM-001", "ai_self_identification", r"\bas an ai language model\b", "high", .99),
        ("LLM-002", "ai_self_identification", r"\bi am an ai model\b", "high", .98),
        ("LLM-003", "knowledge_disclaimer", r"\bas of my last knowledge (?:update|cutoff)\b", "high", .98),
        ("LLM-004", "knowledge_disclaimer", r"\bmy knowledge cutoff\b", "high", .98),
        ("LLM-005", "capability_refusal", r"\bi (?:do not|don't) have (?:browsing|access to current|access to real[- ]time)\b", "high", .95),
        ("LLM-006", "capability_refusal", r"\bi cannot access real[-\s]?time (?:data|information)\b", "high", .95),
        ("LLM-007", "capability_refusal", r"\bi cannot provide (?:medical advice|patient-specific recommendations|real[-\s]?time information)\b", "medium", .88),
        ("LLM-008", "response_preamble", r"\bcertainly,?\s+here is\b", "medium", .90),
        ("LLM-009", "response_preamble", r"\bhere is the revised\b", "medium", .90),
        ("LLM-010", "response_preamble", r"\bbelow is the rewritten\b", "medium", .90),
        ("LLM-011", "response_preamble", r"\bsure,?\s+here(?:'s| is)\b", "medium", .88),
        ("LLM-012", "response_preamble", r"\bcertainly!\s+below is\b", "medium", .90),
        ("LLM-013", "response_closing", r"\bplease let me know if you would like me to\b", "medium", .88),
        ("LLM-014", "response_closing", r"\bthe final answer is provided above\b", "medium", .88),
        ("LLM-015", "response_disclosure", r"\bthis response can be adapted to the requested\b", "medium", .88),
        ("LLM-016", "response_disclosure", r"\bi have rewritten the text to improve clarity and academic tone\b", "medium", .90),
        ("LLM-017", "prompt_leakage", r"\brewrite this abstract\b", "medium", .90),
        ("LLM-018", "prompt_leakage", r"\bimprove grammar\b", "medium", .90),
        ("LLM-019", "prompt_leakage", r"\bsummarize the following\b", "medium", .90),
        ("LLM-020", "prompt_leakage", r"\bdo not highlight negatives\b", "medium", .90),
        ("LLM-021", "prompt_leakage", r"\bpositive review only\b", "medium", .90),
        ("LLM-022", "interface_residue", r"\bregenerate response\b", "high", .95),
        ("LLM-023", "interface_residue", r"\bcopy response\b", "medium", .80),
        ("LLM-024", "interface_residue", r"\bnew chat\b", "low", .55),
        ("LLM-025", "stock_framing", r"\b(?:it is|it's) (?:important|essential) to note(?: that)?\b", "low", .60),
        ("LLM-026", "conversation_residue", r"\bthe user (?:asks?|asked|requests?|requested|wants?|wanted|provided|wrote|said)\b", "low", .70),
        ("LLM-027", "markdown_residue", r"(?<!\S)#{3,}\s+\S", "low", .55),
        ("LLM-028", "markdown_residue", r"(?<!\S)---+(?!\S)", "low", .55),
    ]
    return [
        LLMRule(rule_id, category, pattern, severity, confidence, re.compile(pattern, re.I | re.U))
        for rule_id, category, pattern, severity, confidence in definitions
    ]


def _quoted(text: str, start: int) -> bool:
    return text[:start].count('"') % 2 == 1 or text[:start].count("“") > text[:start].count("”")


def detect_llm_trace(record: ParsedRecord, rules: Iterable[LLMRule]) -> list[Finding]:
    findings: list[Finding] = []
    for field_name, field_text in (("title", record.title), ("abstract_text", record.abstract_text)):
        if not field_text:
            continue
        for rule in rules:
            for match in rule.compiled.finditer(field_text):
                # A quoted example is evidence for review, not standalone residue.
                if _quoted(field_text, match.start()):
                    continue
                findings.append(
                    Finding(
                        finding_id="",
                        record_id=record.record_id,
                        source_file=record.source_file,
                        detector_type="llm_response_trace",
                        category=rule.category,
                        matched_text=match.group(0),
                        evidence_snippet=evidence_snippet(field_text, match.start(), match.end()),
                        section_or_field=field_name,
                        severity=rule.severity,
                        confidence=rule.confidence,
                        rule_id=rule.rule_id,
                    )
                )
    return findings
