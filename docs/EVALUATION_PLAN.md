# Synthetic Evaluation

`python scripts/run_eval.py` runs the pipeline offline on labelled XML under `tests/fixtures/eval_corpus`.
It reports precision and recall for template pair findings, family membership, family pairwise relationships, and abstract template flags.
It also checks family merges/splits, cluster flags, and finding-count reconciliation against `tests/fixtures/eval_baseline.json`.
The current script does not sweep thresholds or measure every detector's precision and recall.
Do not regenerate the baseline merely to make a failing gate pass.

The opt-in `--detect-nonsense-candidates` evaluation requires configured IntelliHub access.
Mocked planted cases are covered by `tests/test_nonsense_candidate.py`; that experimental detector remains excluded from canonical findings.
Entity extraction can be evaluated from reviewed annotations with `scripts/evaluate_entity_extraction.py`, or deterministic masking references with `scripts/evaluate_masked_entity_rules.py`.
`scripts/evaluate_template_detection.py` requires explicit gold/prediction files and the matching corpus manifest; those external datasets are not needed for the CI baseline.
The obsolete PubMedBERT evaluator was removed because production uses explicit GPT-OSS extraction and deterministic rules.

Synthetic success does not estimate production prevalence, validate semantic generalization or GPT-OSS accuracy, establish throughput for 6,000 records, or replace review of representative labelled abstracts.
Scale evidence belongs to Phase 2 and comprehensive detector evaluations to Phase 3.
