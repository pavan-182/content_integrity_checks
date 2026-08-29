# ASCO Template Detection — Session Handoff

Last updated: 2026-08-28

## Goal

Detect suspicious abstract-template reuse in ASCO XML abstracts using transparent, reviewer-auditable evidence. This is an editorial-review aid, not an automatic misconduct decision.

## Inputs

- `retracted_articles_asco-format.xml`: template-detection and molecular-enriched ASCO corpus.
- `Breast_Cancer_Metastatic_publication.xml`: real ASCO-format ingestion/masking validation corpus.
- `golden_data_template_detection_v3.csv`: current pair-level gold dataset.
- `abstract_template_detection_work_items.csv`: work-item tracker.
- `abstract_template_detection_new_and_partial_capabilities_detailed.md`: capability/status documentation.

## Work-item status

| ID | Work item | Status |
| --- | --- | --- |
| 1 | Reviewer-labelled ASCO evaluation baseline | Complete |
| 2 | Structured abstract ingestion and provenance | Complete |
| 3 | Normalization and typed entity extraction | Complete |
| 4 | Versioned reusable feature object | Complete |
| 5–8 | Title/body retrieval, pair evidence, and tiered scoring | Complete |
| 9 | Same-study and companion-analysis context | Complete |
| 10 | Transparent pair classification | Complete |
| 11 | Suspicious family clustering | Complete |
| 12 | Editorial scoring and priority | Complete |
| 13 | Enriched reporting schema | Complete |
| 14 | Rich entity substitution reporting | Complete |
| 15 | Research-signal validation | Complete; research signals remain disabled where they add no validated value |
| 16 | Optional research (stylometry, historical matching, LLM review) | Deferred |

## Final production decision and validation

- Exact-text and entity-normalized detectors are evidence generators. The advanced route/evidence/context/classification/priority/family chain is the single authoritative decision path for template flags, priorities, families, CSVs, and workbook sheets.
- The consolidated workbook's `Template Pairs`, `Template Clusters`, and `Abstract Summary` sheets now contain the authoritative enriched results; the raw merged detector output remains available as `template_detector_evidence.csv`.
- Generic sponsor, statistical, Methods, and common oncology phrases are excluded from rare-phrase evidence.
- Merged exact/entity evidence keeps the strongest confidence; duplicate detector evidence does not promote confidence.
- Shared authors or affiliations alone do not suppress template reuse. Related-work classifications require structured study context; exact body/Results reuse retains precedence.
- Unsupported registry notices remain visible for manual verification but do not contribute to integrity-risk scoring.
- The final labelled benchmark is 81 predicted pairs: 63 TP, 11 FP, 21 FN, 83 TN; precision 85.1%, recall 75.0%, F1 79.7% (178 automatic labels; 2 manual labels excluded).
- Canonical 519-record run: `outputs/canonical_real_asco_20260811`. Its `run_metadata.jsonl` records the clean commit SHA, corpus SHA-256, complete config, and timestamp.
- That run contains 2,382 canonical routed pairs: 2,369 insufficient candidates and 13 unique findings (9 possible template reuse, 4 possible related work). The reviewer CSV/workbook contain 26 directional rows sharing 13 stable pair IDs; priorities are 8 High, 1 Medium, and 4 Low.
- One verified 3-member family is reported. Two-abstract pair findings do not receive family IDs. All 519 abstracts remain in `Abstract Summary`.
- Full regression suite: 206 tests passed.

### Work Item 1

- Gold v3 has 180 pair labels, including 2 manual-review labels.
- Excluding manual-review labels: 84 TP, 25 FP, 0 FN, 69 TN.
- Precision 77.1%, recall 100%, F1 87.0%.
- The 25 false positives remain labelled negatives for future calibration.

### Work Item 2

- Parser extracts title, abstract sections, authors, affiliations, trial IDs, and provenance offsets.
- Real breast ASCO XML: 113 records, 443 structured sections, and 556 offset-valid trace blocks.

### Work Item 3

Implemented in `content_integrity/entity_extraction.py`:

- Hybrid extraction: deterministic patterns plus conservative contextual patterns; no trained NER dependency.
- Types: gene, protein, miRNA, lncRNA, drug, disease, biomarker, trial ID, date, percentage, p-value, number, pathway, cell line, assay, endpoint, registry, population, treatment class, URL, and email.
- Each entity retains source text, normalized value, type, offsets, section, sentence index, extraction method, confidence, and `asco-hybrid-v1` vocabulary version.
- Uncertain mentions should remain unchanged rather than receiving a guessed type.
- The entity-template detector now calls the shared masker. It treats proteins as genes internally only to preserve existing calibrated detector behaviour; exported masks retain `<PROTEIN>`.

Validation outputs:

- `outputs/breast_cancer_masked_sections.csv`
- `outputs/breast_cancer_masking_validation.csv`
- Breast XML result: 443/443 sections passed span, non-overlap, and masked-text reconstruction checks.

Semantic evaluation:

- `scripts/evaluate_masked_entity_rules.py` measured the deterministic extractor against the provided local/regional masked-reference set and its source XML.
- Result: 244 records, 9,041 exact span-and-type matches, 69.1% precision, 63.0% recall, and 65.9% F1; 33 reference entities could not be aligned after XML normalisation.
- `outputs/local_regional_entity_metrics.csv` contains per-type metrics.

SciSpaCy experiment:

- `en_ner_bionlp13cg_md` loads successfully but is not enabled by default.
- With `ASCO_SCISPACY_MODEL=en_ner_bionlp13cg_md`, the same 244-record benchmark produced 9,044 TP, 7,042 FP, and 5,304 FN: 56.2% precision, 63.0% recall, and 59.4% F1.
- The deterministic baseline remains better at 69.1% precision, 63.0% recall, and 65.9% F1. SciSpaCy added only 3 TP while adding 2,995 FP, so it is retained only as an optional experiment.
- `outputs/local_regional_entity_metrics_scispacy.csv` records the model run. The full real-ASCO SciSpaCy batch was not promoted to production because inference was substantially slower and the controlled benchmark was worse.

### Work Item 4

Implemented in `content_integrity/template_features.py`:

- `TemplateFeatures` is a versioned, JSON-serializable record feature object.
- It preserves original, normalised, and masked title, abstract, and section text; typed entities and spans; trial IDs; structured-abstract state; and a stable source hash.
- `scripts/export_template_features.py` writes one object per XML record as JSONL.
- `outputs/local_regional_template_features.jsonl` contains 244 exported feature objects from the supplied local/regional XML.

### Work Item 9

- `content_integrity.study_context.compare_study_context` compares trial IDs, registries/databases, study dates, sample sizes, populations, treatments, endpoints, authors, affiliations, and explicit companion-analysis wording for routed pairs.
- Context remains non-scoring and different endpoints produce a likely-companion interpretation only when study context independently aligns.
- `outputs/local_regional_study_context.csv` contains 1,154 candidate-pair comparisons: 288 possible related-study contexts and 866 with no structured context. A shared sample size alone is reported but cannot create related-study context. No pair had enough aligned context for a likely same-study or companion classification.

### Work Item 10

- `content_integrity.pair_classification.classify_pairs` applies versioned, reproducible rules for possible template reuse, companion analysis, related duplicate, and insufficient evidence.
- Primary evidence is mandatory. Exact full-body or Results reuse takes precedence over companion context and becomes a possible related duplicate when related-study context exists.
- `outputs/local_regional_pair_classifications.csv` contains 1,154 classifications. All are `insufficient_evidence` because Work Items 7–8 found no primary evidence under the intentionally strict gates.

### Work Item 11

- `content_integrity.family_clustering.cluster_suspicious_families` creates graph edges only for `possible_template_reuse` pairs with review score at least 0.75; components smaller than three abstracts are not families.
- Each component selects an evidence-weighted representative; members without a direct eligible edge to it are marked as outliers. Companion and insufficient-evidence pairs remain excluded from family edges.
- `outputs/local_regional_suspicious_families.csv` contains only its schema header because the supplied corpus has no eligible classified edges.

### Work Item 12

- `content_integrity.editorial_scoring.assign_editorial_priority` maps the existing audited primary-evidence bands to High (at least 0.85), Medium (at least 0.75), and Low (0.65 title-template band); no primary evidence is `None`.
- Likely companion analyses are capped at Low. Study context and family size do not increase a score or priority.
- `outputs/local_regional_editorial_priorities.csv` contains 1,154 `None` priorities because no candidate pair has primary evidence. The bands are deliberately conservative and should be re-fit when labels cover this score schema.

### Work Item 13

- `content_integrity.enriched_reporting.build_enriched_reports` produces versioned pair, family, and abstract rows by joining retrieval routes, evidence, context, class, priority, and family status.
- `outputs/local_regional_enriched_pairs.csv`, `outputs/local_regional_enriched_families.csv`, and `outputs/local_regional_enriched_abstracts.csv` contain 1,154 pair rows, 0 family rows, and 244 abstract rows respectively.

### Work Item 14

- `content_integrity.entity_substitutions.collect_entity_substitutions` reports shared and side-specific typed entities, conservative same-type substitution candidates, and up to two supporting source sentences per side.
- `outputs/local_regional_entity_substitutions.csv` contains 1,154 candidate-pair rows. These descriptive fields never alter classification or editorial priority.

### Work Item 15

- `content_integrity.signal_validation` validates molecular-axis, assay-workflow, and endpoint-bundle candidate signatures against the reviewer-labelled retracted-paper corpus. It does not modify production retrieval, scoring, or classification.
- `outputs/work_item_15_signal_validation.csv` evaluated 178 automatic gold labels (2 manual labels excluded): molecular-axis retrieved 12 positives but added no positives beyond current routes and one labelled negative, so it is rejected for topic noise. Assay workflow retrieved 3 positives and no labelled negatives but no incremental positives, so it remains disabled. Endpoint bundles yielded no candidate pairs.
- Semantic embedding retrieval remains deferred; no model or dependency was added.
- Scientific-recipe signatures were not independently implemented; section-aware similarity, typed entities, assays, endpoints, and Results/Conclusions weighting remain the available foundation.

### Work Item 16 and documentation

- Stylometry, historical suspicious-family matching, gated LLM ambiguity review, offline vocabulary discovery, and generated reviewer summaries remain deferred.
- `template_detection_low_level.html` was updated to show the implemented routing, risk exclusions, 4C outcome, and current validation metrics.

## Session log — 2026-08-28: production-readiness Phase 1

A separate track from work items 1–16. Those cover *what the detector does*; this one covers
what stands between the detector and a production deployment. The full gap analysis lives in
`Template Detection — Architecture Analysis & Production-Readiness Gaps.md` at the repo root;
this section records only what changed in the codebase.

### Two pre-existing failures found and fixed

The repository had no CI, and both of its quality gates were red when this session started —
which is the cost of that gap made concrete.

1. **`scripts/run_eval.py` exited 1** with `template double-count errors: ['syn90003', 'syn90005']`.
   The pipeline was correct; the harness was not. `pipeline.py` builds `total_finding_count`
   from `llm_risk_priority` (active findings only), while `run_eval.py` reconciled it against
   `llm_review_priority` (all findings, including `pending`). Those two legitimately diverge on
   every un-validated run, which is the default. Fixed at the root rather than in the harness:
   `llm_risk_priority` is now published in `abstract_summary_rows`
   (`content_integrity/pipeline.py`), and `run_eval.py` reconciles against it. The harness no
   longer re-derives a pipeline invariant, which is how the drift arose.
2. **`tests/test_reporting_reconciliation.py::test_authorship_checks_come_from_frontend_json`
   failed.** It asserted on the wording of `outputs/frontend_run/final_json.json` — a tracked
   file regenerated by real runs, whose comment format changed in commit `b2f02e5` from
   `"submitted N times"` to `"has appeared in following DOIs: …"`. The test now builds its own
   authorship fixture and passes it via `authorship_json_path`, so it no longer depends on
   mutable repo state.

### Added

- **`.github/workflows/ci.yml`** — runs `pytest tests/` then `python scripts/run_eval.py` on
  push to `master` and on pull requests. Both gates are green. `pytest` is installed
  explicitly because `requirements.txt` holds runtime dependencies only and there is no
  `pyproject.toml` yet.
- **Per-stage progress logging** in `content_integrity/pipeline.py` — a `_stage` context
  manager plus timing inside `_run_detector`, emitting one timed line per stage. Per-record
  `_run_detector` calls are deliberately not logged at info level, or a 6,000-record run would
  emit thousands of lines. New `--quiet` flag; `logging.basicConfig` is called only from
  `main()`, so importing the pipeline as a library still configures nothing. Timings are also
  attached structurally via `extra={"stage": ..., "seconds": ...}` so tooling can collect them
  without parsing rendered messages.
- **`scripts/benchmark_scale.py`** — builds a synthetic corpus of a requested size from real
  records, runs the real pipeline, and writes per-stage wall clock, peak RSS and the run's own
  counters to `tests/fixtures/scale_baseline.json`. Corpus size is counted in *records*, not
  files: one ASCO XML file can carry hundreds of records.
- **`tests/fixtures/eval_baseline.json`** — `run_eval.py` now gates on stored floors for scores
  and ceilings for error counts instead of asserting perfection, with `--update-baseline` to
  move the floor deliberately. Current numbers are all 1.000/0, so behaviour is unchanged
  today, but the gate survives the corpus growing a case the detectors legitimately miss.
- **`golden_data_template_detection_v3.manifest.json`** — pins the corpus that the gold labels
  were written against, by the checksum `pipeline._input_manifest_checksum` already computes.
  `scripts/evaluate_template_detection.py --corpus-dir` verifies it and refuses to score a
  mismatched corpus. This deliberately replaces an earlier proposal to commit the 190
  `phase0_asco_xml_output_v3` XML files: that corpus is third-party publication content, CI
  already runs from the tracked `tests/fixtures/eval_corpus`, and
  `evaluate_template_detection.py` never reads XML at all. The only real gap was that nothing
  recorded *which* corpus produced the published metrics.

### Measured: the documented runtime does not reproduce

`scripts/benchmark_scale.py --records 519` over the same `real_asco_files` corpus the delivery
docs report on:

| Stage | Seconds | Share |
| --- | ---: | ---: |
| `shared_preprocessing` (feature extraction) | 241.2 | 48% |
| `enriched_reports` | 131.3 | 26% |
| `entity_normalized_template` | 44.3 | 9% |
| `exact_text_reuse` | 33.4 | 7% |
| numerical / design / trial | 33.9 | 7% |
| parse + write outputs | 7.9 | 2% |
| **Total** | **498.6 s (8 min 19 s)** | peak RSS 263 MB |

`TEMPLATE_DETECTION_PLANNED_VS_DELIVERED.md` records **1 minute 52 seconds** for this corpus.
The measured figure is roughly 4.5× that. It is the same corpus and the same code path, not a
different one — the candidate-pair count comes out at 2,382, matching the delivery doc exactly
— and two runs agreed within 1.4%. **The published timing should be treated as incorrect
rather than as a performance target.**

The profile also quantifies two known inefficiencies: feature extraction and enriched
reporting together account for 74% of runtime, and enriched reporting scores all 2,382
candidate pairs while only 18 survive the priority gate, so approximately 99% of that 131
seconds is discarded work. Straight-line extrapolation to 6,000 records is about 1.6 hours.

Not yet run: the full 6,000-record benchmark (`--records 6000`), which is the acceptance test
the delivery docs call for. `tests/fixtures/scale_baseline.json` is hardware-specific and is a
local reference, not a CI gate. It was measured with NER disabled
(`entity_model_inference_count: 0`), so any future model-backed entity extraction needs its
own baseline.

## Next required action

Core work items 1–15 are complete. Work Item 16 remains optional/deferred research work.

On the production-readiness track, Phase 1 (make it measurable) is complete. Phase 2 is the
two correctness cliffs named in the gap analysis: approximate blocking silently dropping
candidate buckets above the size cap, and the naive sentence splitter still present in
`content_integrity/template_features.py` despite audit item OE-01 being marked resolved.

## Useful commands

```bash
python3 scripts/export_masked_entities.py \
  --input-xml Breast_Cancer_Metastatic_publication.xml \
  --output-csv outputs/breast_cancer_masked_sections.csv \
  --validation-csv outputs/breast_cancer_masking_validation.csv

python3 scripts/create_entity_annotation_sample.py \
  --input-xml Breast_Cancer_Metastatic_publication.xml \
  --input-xml retracted_articles_asco-format.xml \
  --output-csv outputs/entity_annotation_sample_100.csv \
  --limit 100

python3 -m unittest tests.test_entity_extraction tests.test_entity_normalized_template tests.test_pipeline
```

Both quality gates, which CI now runs on every push and pull request:

```bash
python -m pytest tests/
python scripts/run_eval.py
```

The latest full run passed 284 tests plus 122 subtests, and `run_eval.py` exits 0. The pipeline
tests intentionally log mocked validator failures while still passing.

Performance baseline (regenerate deliberately; hardware-specific):

```bash
python scripts/benchmark_scale.py --records 519          # ~8 minutes, current baseline
python scripts/benchmark_scale.py --records 6000         # ~1.6 hours, the acceptance test
```

Gold-corpus verification, for anyone holding `phase0_asco_xml_output_v3`:

```bash
python scripts/evaluate_template_detection.py \
  --gold-csv golden_data_template_detection_v3.csv \
  --predictions-csv <predictions.csv> \
  --corpus-dir phase0_asco_xml_output_v3
```
