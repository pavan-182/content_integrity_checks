from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


CATALOGUE_PATH = Path(__file__).with_name("llm_response_trace_rules.yaml")
REQUIRED_FIELDS = {
    "rule_id",
    "category",
    "signal_level",
    "severity",
    "priority_eligible",
    "requires_validation",
    "regex_patterns",
    "semantic_description",
    "positive_examples",
    "negative_examples",
    "version",
}
ALLOWED_CATEGORIES = {
    "ai_self_identification",
    "knowledge_disclaimer",
    "capability_disclaimer",
    "response_preamble",
    "response_closing",
    "response_disclosure",
    "prompt_leakage",
    "conversation_residue",
    "interface_residue",
    "formatting_residue",
    "novel_response_residue",
}
ALLOWED_SIGNAL_LEVELS = {"strong", "contextual", "supporting"}
ALLOWED_SEVERITIES = {"low", "medium", "high"}


@dataclass(frozen=True, slots=True)
class LLMTraceRule:
    rule_id: str
    category: str
    signal_level: str
    severity: str
    priority_eligible: bool
    requires_validation: bool
    regex_patterns: tuple[str, ...]
    semantic_description: str
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]
    version: int
    confidence: float
    compiled_patterns: tuple[re.Pattern[str], ...] = field(repr=False)

    @property
    def pattern(self) -> str:
        return self.regex_patterns[0]

    @property
    def compiled(self) -> re.Pattern[str]:
        return self.compiled_patterns[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "detector_type": "llm_response_trace",
            "rule_id": self.rule_id,
            "category": self.category,
            "pattern": " | ".join(self.regex_patterns),
            "severity": self.severity,
            "confidence": self.confidence,
            "source": "llm_response_trace_rules.yaml",
        }


def _string_list(item: dict[str, Any], name: str, rule_id: str, *, nonempty: bool = False) -> tuple[str, ...]:
    value = item[name]
    if not isinstance(value, list) or any(not isinstance(entry, str) for entry in value):
        raise ValueError(f"{rule_id}: {name} must be a list of strings")
    values = tuple(entry for entry in value if entry.strip())
    if nonempty and not values:
        raise ValueError(f"{rule_id}: {name} must not be empty")
    return values


def load_llm_trace_rules(path: str | Path = CATALOGUE_PATH) -> list[LLMTraceRule]:
    resolved = Path(path)
    try:
        payload = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Could not load LLM response-trace catalogue: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"rules"} or not isinstance(payload["rules"], list):
        raise ValueError("LLM response-trace catalogue must contain only a rules list")

    rules: list[LLMTraceRule] = []
    seen: set[str] = set()
    for item in payload["rules"]:
        if not isinstance(item, dict):
            raise ValueError("Each LLM response-trace rule must be an object")
        missing = REQUIRED_FIELDS - set(item)
        if missing:
            raise ValueError(f"LLM response-trace rule is missing: {', '.join(sorted(missing))}")
        rule_id = str(item["rule_id"]).strip()
        if not rule_id or rule_id in seen:
            raise ValueError(f"Duplicate or empty LLM response-trace rule ID: {rule_id!r}")
        seen.add(rule_id)
        category = str(item["category"]).strip()
        signal_level = str(item["signal_level"]).strip()
        severity = str(item["severity"]).strip()
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(f"{rule_id}: invalid category {category!r}")
        if signal_level not in ALLOWED_SIGNAL_LEVELS:
            raise ValueError(f"{rule_id}: invalid signal level {signal_level!r}")
        if severity not in ALLOWED_SEVERITIES:
            raise ValueError(f"{rule_id}: invalid severity {severity!r}")
        if not isinstance(item["priority_eligible"], bool) or not isinstance(item["requires_validation"], bool):
            raise ValueError(f"{rule_id}: priority_eligible and requires_validation must be booleans")
        description = str(item["semantic_description"]).strip()
        if not description:
            raise ValueError(f"{rule_id}: semantic_description must not be empty")
        patterns = _string_list(item, "regex_patterns", rule_id, nonempty=True)
        try:
            compiled = tuple(re.compile(pattern, re.IGNORECASE | re.UNICODE) for pattern in patterns)
        except re.error as exc:
            raise ValueError(f"{rule_id}: invalid regex: {exc}") from exc
        version = item["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"{rule_id}: version must be a positive integer")
        confidence = item.get(
            "confidence",
            {"strong": 0.98, "contextual": 0.85, "supporting": 0.55}[signal_level],
        )
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError(f"{rule_id}: confidence must be between 0 and 1")
        rules.append(
            LLMTraceRule(
                rule_id=rule_id,
                category=category,
                signal_level=signal_level,
                severity=severity,
                priority_eligible=item["priority_eligible"],
                requires_validation=item["requires_validation"],
                regex_patterns=patterns,
                semantic_description=description,
                positive_examples=_string_list(item, "positive_examples", rule_id),
                negative_examples=_string_list(item, "negative_examples", rule_id),
                version=version,
                confidence=float(confidence),
                compiled_patterns=compiled,
            )
        )
    return rules


def catalogue_metadata(path: str | Path = CATALOGUE_PATH) -> tuple[int, str]:
    data = Path(path).read_bytes()
    rules = load_llm_trace_rules(path)
    return max((rule.version for rule in rules), default=0), hashlib.sha256(data).hexdigest()
