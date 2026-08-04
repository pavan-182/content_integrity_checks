from __future__ import annotations

from .exact_text_reuse import detect_exact_text_reuse
from .entity_normalized_template import detect_entity_normalized_templates
from ..template_detection import cluster_templates  # deprecated compatibility export
from .llm_trace import (
    LLMRule,
    built_in_llm_rules,
    candidate_to_finding,
    detect_llm_trace,
    detect_llm_trace_candidates,
)
from .nonsense_candidate import NonsenseCandidateDetector
from .tortured_phrase import (
    TorturedRule,
    build_tortured_rule_index,
    detect_tortured_phrases,
    load_tortured_rules,
)

__all__ = [
    "LLMRule",
    "NonsenseCandidateDetector",
    "TorturedRule",
    "built_in_llm_rules",
    "candidate_to_finding",
    "build_tortured_rule_index",
    "cluster_templates",
    "detect_exact_text_reuse",
    "detect_entity_normalized_templates",
    "detect_llm_trace",
    "detect_llm_trace_candidates",
    "detect_tortured_phrases",
    "load_tortured_rules",
]
