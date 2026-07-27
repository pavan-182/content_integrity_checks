"""Shared deterministic matching primitives.

The imports from ``template_detection`` are kept here as a compatibility bridge
while callers migrate away from the legacy detector module.
"""

from .template_detection import (
    BOILERPLATE_RE,
    DATE_PATTERNS,
    DRUG_SUFFIX_PATTERN,
    EMAIL_PATTERN,
    GENE_PATTERN,
    NUMBER_PATTERN,
    PERCENT_PATTERN,
    PLACEHOLDER_TOKEN_RE,
    PLACEHOLDER_TOKENS,
    PVAL_PATTERN,
    SECTION_WEIGHTS,
    TRIAL_PATTERN,
    URL_PATTERN,
    _candidate_pairs,
    _content_class,
    _sentence_split,
    _shared_excerpt,
    _similarity,
)

__all__ = [name for name in globals() if not name.startswith("__")]
