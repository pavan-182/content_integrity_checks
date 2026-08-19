"""Single place to tune every numeric threshold used across content_integrity.

Each constant here is a straight relocation of a value that used to be hardcoded in its
source module (grouped below by source module, in the original module's declaration order).
Values are unchanged. Every source module re-imports its constant(s) under the original bare
name via `as`, so nothing inside those modules needed to change beyond the import line.

Several bare names collide across modules with *different* values (e.g. DEFAULT_TIMEOUT_SECONDS
is 10.0 for the trial-registry client and 120.0 for the GPT-OSS client), so names here are
prefixed by source module to keep this file collision-free.

Versioning: traced via the git commit sha already stamped into run metadata (see pipeline.py's
run_metadata_rows) - no separate version/checksum scheme needed for code-defined constants.
"""

from __future__ import annotations

# -- pair_evidence.py --
PAIR_EVIDENCE_STRONG_MASKED_BODY = 0.88
PAIR_EVIDENCE_ORIGINAL_SUPPORT = 0.55
PAIR_EVIDENCE_MIN_SHARED_BLOCK_WORDS = 30

# -- evidence_scoring.py --
EVIDENCE_SCORING_PRIMARY_WEIGHTS = {
    "exact_original_body": 1.0,
    "exact_results_section": 1.0,
    "substantial_shared_original_block": 0.85,
    "strong_masked_body_with_original_support": 0.75,
    "entity_normalized_template": 0.75,
    "distinctive_shared_text": 0.85,
    "masked_title_template_with_original_support": 0.65,
}
EVIDENCE_SCORING_SECTION_WEIGHTS = {"result": 0.20, "conclusion": 0.20, "background": 0.10, "method": 0.05}
EVIDENCE_SCORING_SUPPORTING_SECTION_THRESHOLD = 0.75

# -- editorial_scoring.py --
# ponytail: bands reuse audited primary-evidence weights; re-fit when labelled pairs include this score schema.
EDITORIAL_SCORING_HIGH_THRESHOLD = 0.85
EDITORIAL_SCORING_MEDIUM_THRESHOLD = 0.75

# -- title_templates.py --
TITLE_TEMPLATES_MIN_TITLE_WORDS = 4
TITLE_TEMPLATES_MAX_BUCKET_SIZE = 50

# -- family_clustering.py --
FAMILY_CLUSTERING_MIN_EDGE_SCORE = 0.75

# -- template_matching_common.py --
TEMPLATE_MATCHING_COMMON_NGRAM_SIZE = 5
TEMPLATE_MATCHING_COMMON_MIN_SHARED_NGRAMS = 3
TEMPLATE_MATCHING_COMMON_MAX_NGRAM_BUCKET = 50
TEMPLATE_MATCHING_COMMON_MAX_APPROXIMATE_BUCKET = 50
# ponytail: provisional transparent weights; replace after labelled ASCO calibration.
TEMPLATE_MATCHING_COMMON_SECTION_WEIGHTS = {"background": 0.1, "methods": 0.2, "results": 0.4, "conclusions": 0.3}

# -- detectors/numerical_contradiction.py --
NUMERICAL_CONTRADICTION_PERCENTAGE_TOLERANCE = 1.0

# -- detectors/nonsense_candidate.py --
NONSENSE_CANDIDATE_MIN_SENTENCE_TOKENS = 8
NONSENSE_CANDIDATE_DEFAULT_SENTENCES_PER_BATCH = 10
NONSENSE_CANDIDATE_DEFAULT_MAX_CONCURRENT_BATCHES = 4
NONSENSE_CANDIDATE_MODEL_RESPONSE_ATTEMPTS = 2

# -- detectors/entity_normalized_template.py --
# ponytail: uncalibrated ASCO V1 thresholds; replace after labelled-corpus calibration.
ENTITY_NORMALIZED_TEMPLATE_MASKED_SIMILARITY_THRESHOLD = 0.88
ENTITY_NORMALIZED_TEMPLATE_ORIGINAL_SUPPORT_THRESHOLD = 0.55
ENTITY_NORMALIZED_TEMPLATE_MINIMUM_SKELETON_WORDS = 30
ENTITY_NORMALIZED_TEMPLATE_MAXIMUM_PLACEHOLDER_RATIO = 0.35
ENTITY_NORMALIZED_TEMPLATE_MINIMUM_SUBSTITUTIONS = 1
ENTITY_NORMALIZED_TEMPLATE_SECTION_SIMILARITY_THRESHOLD = 0.88
ENTITY_NORMALIZED_TEMPLATE_LOCAL_MASKED_SIMILARITY_THRESHOLD = 0.75
ENTITY_NORMALIZED_TEMPLATE_LOCAL_ORIGINAL_SUPPORT_THRESHOLD = 0.60
ENTITY_NORMALIZED_TEMPLATE_LOCAL_SEQUENCE_SIMILARITY_THRESHOLD = 0.63
ENTITY_NORMALIZED_TEMPLATE_MODERATE_LOCAL_MASKED_THRESHOLD = 0.63
ENTITY_NORMALIZED_TEMPLATE_MODERATE_LOCAL_ORIGINAL_THRESHOLD = 0.54
ENTITY_NORMALIZED_TEMPLATE_MODERATE_GLOBAL_MASKED_THRESHOLD = 0.27
ENTITY_NORMALIZED_TEMPLATE_MODERATE_SHARED_SKELETON_WORDS = 60
ENTITY_NORMALIZED_TEMPLATE_MINIMUM_GLOBAL_ORIGINAL_FOR_LOCAL = 0.20
ENTITY_NORMALIZED_TEMPLATE_RARE_TITLE_TOKEN_DOCUMENT_FREQUENCY = 3
# ponytail: calibrated on the 93-pair ASCO baseline; re-fit when that baseline changes.

# -- detectors/unverifiable_trial.py --
UNVERIFIABLE_TRIAL_DEFAULT_TIMEOUT_SECONDS = 10.0
UNVERIFIABLE_TRIAL_DEFAULT_MAX_RETRIES = 2
UNVERIFIABLE_TRIAL_DEFAULT_MAX_CONCURRENT_LOOKUPS = 4

# -- detectors/exact_text_reuse.py --
EXACT_TEXT_REUSE_MIN_SENTENCE_WORDS = 10
EXACT_TEXT_REUSE_MIN_SHARED_BLOCK_WORDS = 30
EXACT_TEXT_REUSE_MAX_SENTENCE_DOCUMENT_FREQUENCY = 10
EXACT_TEXT_REUSE_MEDIUM_COVERAGE = 0.15
EXACT_TEXT_REUSE_HIGH_COVERAGE = 0.30
# ponytail: one shared sentence needs stronger coverage; calibrate on labelled editorial data.
EXACT_TEXT_REUSE_MIN_SINGLE_SENTENCE_COVERAGE = 0.40
EXACT_TEXT_REUSE_MIN_RARE_PHRASE_WORDS = 10
EXACT_TEXT_REUSE_MAX_RARE_PHRASE_DOCUMENT_FREQUENCY = 2
EXACT_TEXT_REUSE_MIN_RARE_PHRASE_SENTENCE_SIMILARITY = 0.57

# -- validators/context_validator.py --
CONTEXT_VALIDATOR_DEFAULT_TIMEOUT_SECONDS = 120.0
CONTEXT_VALIDATOR_DEFAULT_MAX_ATTEMPTS = 3
CONTEXT_VALIDATOR_DEFAULT_BACKOFF_SECONDS = 1.0
# The validator model is reasoning-heavy, so leave enough budget for the final JSON answer.
CONTEXT_VALIDATOR_VALIDATION_MAX_TOKENS = 2048

# -- signal_validation.py --
SIGNAL_VALIDATION_MAX_SIGNATURE_BUCKET = 20

# -- detectors/llm_trace_semantic.py --
LLM_TRACE_SEMANTIC_DEFAULT_INPUT_TOKEN_BUDGET = 7000
LLM_TRACE_SEMANTIC_MAX_INPUT_TOKEN_BUDGET = 8000
LLM_TRACE_SEMANTIC_DEFAULT_MAX_OUTPUT_TOKENS = 8192
LLM_TRACE_SEMANTIC_MAX_OUTPUT_TOKENS = 8192
LLM_TRACE_SEMANTIC_DEFAULT_MAX_RECORDS_PER_BATCH = 8
LLM_TRACE_SEMANTIC_DEFAULT_MAX_CONCURRENT_BATCHES = 4
LLM_TRACE_SEMANTIC_MODEL_RESPONSE_ATTEMPTS = 2

# -- rules/__init__.py --
RULES_SIGNAL_LEVEL_DEFAULT_CONFIDENCE = {"strong": 0.98, "contextual": 0.85, "supporting": 0.55}
