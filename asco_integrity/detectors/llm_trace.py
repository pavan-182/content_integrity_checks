from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from ..models import Finding, ParsedRecord
from ..utils import evidence_snippet, normalize_for_matching


AI_CONTEXT_TERMS = {
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "large language model",
    "language model",
    "llm",
    "chatgpt",
    "gpt",
    "generative ai",
    "prompt",
    "transformer model",
}


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


def _context_adjusted_confidence(base: float, text: str) -> float:
    normalized = normalize_for_matching(text)
    if not normalized:
        return base
    if any(term in normalized for term in AI_CONTEXT_TERMS):
        return max(0.1, round(base - 0.25, 3))
    return base


def _severity_for_confidence(base_severity: str, confidence: float) -> str:
    if base_severity == "high" and confidence < 0.85:
        return "medium"
    if base_severity == "medium" and confidence < 0.65:
        return "low"
    return base_severity


def built_in_llm_rules() -> list[LLMRule]:
    rules: list[LLMRule] = []
    definitions = [
        ("LLM-001", "ai_self_identification", r"\bas an ai language model\b", "high", 0.99),
        ("LLM-002", "ai_self_identification", r"\bi am an ai model\b", "high", 0.98),
        ("LLM-003", "response_preamble", r"\bcertainly,?\s+here is\b", "medium", 0.90),
        ("LLM-004", "response_preamble", r"\bhere is the revised\b", "medium", 0.90),
        ("LLM-005", "response_preamble", r"\bbelow is the rewritten\b", "medium", 0.90),
        ("LLM-006", "capability_refusal", r"\bi cannot access real[-\s]?time data\b", "medium", 0.88),
        ("LLM-007", "capability_refusal", r"\bi cannot provide\b", "medium", 0.85),
        ("LLM-008", "conversation_label", r"(?m)(?:^|[.\n\r]\s+)\s*user\s*:", "medium", 0.75),
        ("LLM-009", "conversation_label", r"(?m)(?:^|[.\n\r]\s+)\s*assistant\s*:", "medium", 0.75),
        ("LLM-010", "conversation_label", r"(?m)(?:^|[.\n\r]\s+)\s*system\s*:", "medium", 0.75),
        ("LLM-011", "conversation_label", r"(?m)(?:^|[.\n\r]\s+)\s*human\s*:", "medium", 0.75),
        ("LLM-012", "prompt_leakage", r"\brewrite this abstract\b", "medium", 0.90),
        ("LLM-013", "prompt_leakage", r"\bimprove grammar\b", "medium", 0.90),
        ("LLM-014", "prompt_leakage", r"\bsummarize the following\b", "medium", 0.90),
        ("LLM-015", "prompt_leakage", r"\bdo not highlight negatives\b", "medium", 0.90),
        ("LLM-016", "prompt_leakage", r"\bpositive review only\b", "medium", 0.90),
        ("LLM-017", "interface_residue", r"\bregenerate response\b", "low", 0.55),
        ("LLM-018", "interface_residue", r"\bcopy response\b", "low", 0.55),
        ("LLM-019", "interface_residue", r"\bnew chat\b", "low", 0.55),
        ("LLM-020", "markdown_residue", r"(?m)(?:^|\n)\s*(?:#{3,}|-{3,}|\*{3,})\s*(?:$|\n)", "low", 0.40),
    ]
    for rule_id, category, pattern, severity, confidence in definitions:
        rules.append(
            LLMRule(
                rule_id=rule_id,
                category=category,
                pattern=pattern,
                severity=severity,
                confidence=confidence,
                compiled=re.compile(pattern, re.IGNORECASE | re.UNICODE),
            )
        )
    return rules


def detect_llm_trace(record: ParsedRecord, rules: Iterable[LLMRule]) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str, int, int]] = set()
    for field_name, field_text in (("title", record.title), ("abstract_text", record.abstract_text)):
        if not field_text:
            continue
        for rule in rules:
            for match in rule.compiled.finditer(field_text):
                key = (rule.rule_id, field_name, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                confidence = _context_adjusted_confidence(rule.confidence, field_text)
                severity = _severity_for_confidence(rule.severity, confidence)
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
                        severity=severity,
                        confidence=confidence,
                        rule_id=rule.rule_id,
                    )
                )
    return findings
