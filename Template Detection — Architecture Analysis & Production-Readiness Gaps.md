# Template Detection — Architecture Analysis & Production-Readiness Gaps

**Status as of 2026-08-28:** Phase 1 complete — CI, per-stage logging, a stored eval baseline,
a scale benchmark, and gold-corpus pinning are all in place, and both quality gates are green
(they were red before). Phases 2–7 are not started. Section 2's gap inventory still stands
except where marked; §4 marks the completed items. The per-session change record is in
`SESSION_CONTEXT.md`.

## Context

The template detection subsystem is the strongest and most-invested part of the ASCO
content-integrity pipeline: it finds abstracts built from a shared writing skeleton
(paper-mill / mass-produced submissions) and hands editors a ranked review queue.

The detection *logic* is genuinely good — precision-first gating, tiered evidence, no
confidence promotion on correlated signals, versioned artifacts, 284 tests. The problem
is everything around it. The repo self-describes as a POC (`README.md:1`), has never run
at its stated 6,000-abstract target, and at the time of this analysis had no CI, no
persistence, no calibration on labelled ASCO data, and one measured performance cliff plus
one silent recall cliff that both get *worse* exactly as the corpus grows. (CI landed in
Phase 1; the rest stand.)

This document maps the real architecture, names the gaps that block production, and
proposes a remediation order. It is grounded in the code as it stands on `master`
(`bf48b01`), not in the design docs — several of which are stale or mutually inconsistent.

---

## 1. The architecture as actually built

```
discover_xml_files ──► parse_xml_records ──► dedupe_records
  (xml_parser:726)      (xml_parser:674)     (utils:162)
                                                  │
                                                  ▼
                              build_template_features  ◄── entity_extraction
                              (template_features:121)      (rule regex today; NER backends
                                                            off by default — see §3)
                                     │  ONE TemplateFeatures per record
                                     │  (title/abstract/sections × original|normalized|masked)
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
  detect_exact_text_reuse   detect_entity_normalized_    build_enriched_reports
  (exact_text_reuse:266)      templates (ent:458)          (enriched_reporting:98)
        │                            │                            │
        │  _candidate_pairs #1       │  _candidate_pairs #2       │  _candidate_pairs #3
        │  (over normalized text)    │  (over masked skeletons)   │  (via candidate_routes:49)
        │                            │                            │
        └──────────┬─────────────────┘                            ▼
                   ▼                                    collect_pair_evidence
        merge_pair_findings ──► cluster_template_findings   → score_pair_evidence
        (template_clustering:87)      (:176)                 → compare_study_context
                   │                                         → classify_pairs
                   ▼                                         → assign_editorial_priority
            ✗ DEAD END ✗                                     → build_suspicious_families
      reaches only PipelineResult                                    │
      + scripts/run_eval.py                                          ▼
                                                        directional_finding_rows
                                                                     │
                                              content_integrity_results.json
                                              Editor_Triage_Workbook.xlsx
```

**The single most important structural fact:** there are two parallel template tracks.
The legacy one (`template_clustering.merge_pair_findings` → `cluster_template_findings`,
~265 lines) is computed on every run and consumed by *nothing* that reaches a user —
`pipeline.py:1078-1081` passes empty lists where its output would go, and the enriched
track overrides every field. Only the enriched track ships.

---

## 2. Gaps blocking production

### A. Detection quality

**A1 — Approximate blocking silently drops large families. The recall cliff.**
`template_matching_common.py:191-195`:
```python
if block_key.startswith(("shape:", "prefix:")):
    if len(members) <= MAX_APPROXIMATE_BUCKET:      # 50
        pairs.update(combinations(sorted(members), 2))
else:
    pairs.update(_bounded_block_pairs(members))     # star fallback above the cap
```
Exact-hash blocks degrade to a star above the cap (the OE-04 fix, `c9f675a`). Approximate
blocks emit **nothing at all**. The 5-gram route has the same shape (`:199`,
`2 <= len(members) <= MAX_NGRAM_BUCKET`). So a 60-member template family sharing an entity
shape *and* sharing shingles is invisible on both approximate routes — detection degrades
to zero precisely as the fraud scales up, which inverts the product's purpose. The cap
conflates two different things: "this signal is boilerplate" and "this family is big."
Root cause is the missing IDF the code already flags: `# ponytail: frequency cap
downweights boilerplate; replace with learned IDF after validation` (`:197`).

**A2 — Two sentence splitters; the wrong one sits in the shared feature layer.**
`utils.split_sentences:30` uses pysbd, and `tests/test_utils.py` proves it survives
`Fig.`, `vs.`, and decimals. But `template_features.py:85` — the shared representation
*both* detectors read — uses the naive `(?<=[.!?])\s+` that the audit ledger (OE-01) claims
was eliminated repo-wide. Consequence: `FeatureSentence` boundaries fracture on
abbreviations, so `exact_text_reuse`'s ≥10-word sentence gate and generic-sentence filter
(`exact_text_reuse.py:282-289`) operate on fragments and drop real evidence.
Also still open: `detectors/tortured_phrase.py:24`, `detectors/llm_trace_context.py:126`.

**A3 — Three incompatible similarity functions compared against shared thresholds.**
Bidirectional token (`template_matching_common:120`, runs SequenceMatcher twice),
unidirectional token (`pair_evidence:19`, `evidence_scoring:36`, `enriched_reporting:63`),
and character-level (`title_templates:75`). `0.88` is a threshold in both
`ENTITY_NORMALIZED_TEMPLATE_MASKED_SIMILARITY_THRESHOLD` and
`PAIR_EVIDENCE_STRONG_MASKED_BODY` — the same number against two different metrics.

**A4 — Thresholds are self-declared uncalibrated, and one is provably unseparable.**
`thresholds.py:66` marks all 15 entity-template values uncalibrated; `:53` marks the
section weights provisional; `:37` and `:95` likewise. `generic_workflow_gate_calibration.txt`
records the empirical result: positives span overlap [3,12], false positives [4,9] — the
FP range sits *inside* the positive range, so **no cut point separates them**. That is the
mechanism behind the acknowledged "medium entity-substitution candidates contain oncology
genre noise" (`TEMPLATE_DETECTION_PLANNED_VS_DELIVERED.md:15`).

**A5 — No labelled ASCO ground truth, and the gold set is not reproducible.**
`golden_data_template_detection_v3.csv` holds 180 pairs — over *retracted papers*, not
ASCO submissions. Its source corpus `phase0_asco_xml_output_v3/` is 192 files on disk,
**0 tracked** (`.gitignore:7` excludes `*.xml`). The headline 85.1% P / 75.0% R / 79.7% F1
cannot be reproduced from a clone. The 519-record real-ASCO run has zero reviewer labels;
the docs say so plainly (`:205`).

**A6 — One bad record silently downgrades masking for the whole corpus.**
`pipeline.py:1056-1057`: if any record raises during feature extraction, *all* features are
rebuilt with `use_model=False`. The report records a generic `OperationalIssue` but nothing
distinguishes "ran degraded corpus-wide" from "ran fine."

**A7 — Boilerplate suppression is a hand-written phrase list fitted to this corpus.**
`exact_text_reuse.py:44-59` hardcodes 11 literal ASCO-corpus sentences. No IDF, no
document-frequency stoplist, no fallback. It will not transfer to a different year or
disease area.

**A8 — Batch-only comparison makes the actual threat model undetectable.**
Every run globs one directory and holds everything in memory; there is no database, no
reference corpus, no cross-run state (`docs/pipeline_workflow.md:268`). Paper-mill families
span years and venues. The system can only ever see one batch.

### B. Scale and performance

**B1 — Feature extraction is the bottleneck, and nobody measured it.**
Measured on this machine over the exact 519-record `real_asco_files` corpus (median 377
abstract tokens, NER disabled — the default state):

| stage | measured | extrapolated to 6,000 |
|---|---|---|
| `build_template_features` | **443–495 ms/record**, flat at n=25/50/100 | **≈ 45–50 min** |
| `_candidate_pairs` (blocking) | 0.3–0.5 s / 400 recs | seconds |

Scaling is linear in records, so the extrapolation is safe.

Blocking — the thing OE-04 and Phase 4 have been optimising — is *not* the problem. Feature
extraction is, by three orders of magnitude, and it is embarrassingly parallel and entirely
single-threaded.

**Confirmed by a full-pipeline run** (`scripts/benchmark_scale.py`, Phase 1 item 2) over the
same 519-record `real_asco_files` corpus the docs report on:

| stage | seconds | share |
|---|---:|---:|
| `shared_preprocessing` (feature extraction) | 239.8 | **47%** |
| `enriched_reports` | 124.5 | **25%** |
| `entity_normalized_template` | 41.7 | 8% |
| `exact_text_reuse` | 35.2 | 7% |
| design / numerical / trial | 46.3 | 9% |
| parse + write | 9.1 | 2% |
| **total** | **505.6 s (8 min 26 s)** | peak RSS 264 MB |

Two things fall out. First, **the documented "1 minute 52 seconds"
(`PLANNED_VS_DELIVERED:205`) does not reproduce** — this run is ~4.5× slower on the same
corpus. It is the same corpus and code path, not a different one: the candidate-pair count
comes out at 2,382, matching the doc exactly. The published timing should be treated as
wrong, not as a target. Second, the two stages this analysis called out as waste (B1 and B5)
are together **72% of runtime**, and B5 is quantified: 2,382 candidate pairs are scored and
only 18 survive, so ~99% of that 124 s is discarded.

Straight-line extrapolation to 6,000 records is ~1.6 hours.

**B2 — Candidate blocking is computed three times per run**, over three different text
representations, with no sharing: `exact_text_reuse.py:308`, `entity_normalized_template.py:483`,
`candidate_routes.py:49`. Title shingles are built twice at two different sizes (4-gram at
`ent:212`, trigram at `title_templates:35`).

**B3 — `_similarity` runs SequenceMatcher forward *and* reverse and averages**
(`template_matching_common:126-128`) — 2× cost for a marginal asymmetry correction.

**B4 — `difflib` is the only similarity engine.** No `rapidfuzz`, no MinHash/LSH, no
embeddings. `rapidfuzz` is a near-drop-in for the same Ratcliff-Obershelp family at roughly
an order of magnitude faster.

**B5 — The most expensive per-pair work is done for pairs that are then thrown away.**
`enriched_reporting.py:139` calls `_aligned_text` for **every** classification, and
`_aligned_text` (`:66-76`) runs a SequenceMatcher over ~2× sentence-count windows on both
sides. `directional_finding_rows:87` then drops every pair with `review_priority == "None"`
— which on the 519-record run was 2,369 of 2,382 pairs. ~99% of that compute is discarded.

**B6 — `TemplateFeatures.source_hash` is computed and never used.**
`template_features.py:153` already produces a content hash per record. Nothing caches on it.
Every run re-parses, re-extracts, re-masks everything.

**B7 — Whole corpus resident in memory**, no streaming or chunking. The
`shared_ngram_counts` Counter (`template_matching_common:196`) is the peak-memory risk.
`_token_counts`' `lru_cache(maxsize=4096)` (`ent:351`) is process-global, never cleared, and
thrashes past a few hundred records.

**B8 — The 6,000-record run has never happened.** It is listed as not-delivered
(`PLANNED_VS_DELIVERED:234`), as the next required step (`:250`), and as "your most
important engineering acceptance test" (`whole_content_integrity_improvisation_plan.md:994`).

### C. Operations

**C1 — No CI.** No `.github/`, no lint, no typecheck. 284 tests exist and `scripts/run_eval.py`
already returns a nonzero exit code on regression (`:164`) — neither runs on change. Both were
in fact *failing* when this analysis was written, which is the cost of the gap made concrete:
one stale test coupled to a regenerated data file, and `run_eval.py` reconciling
`total_finding_count` against `llm_review_priority` when the pipeline builds it from
`llm_risk_priority` (`pipeline.py:511`). Fixed in Phase 1.

**C2 — No packaging.** No `pyproject.toml`/`setup.py`. `requirements.txt` is six
lower-bound lines missing `pytest`, `spacy` (used at `entity_extraction.py:86`), and the HTTP
client. `frontend/package.json` pins four deps to `"latest"`.

**C3 — A developer's absolute path is the shipped CLI default:**
`pipeline.py:1278` and `:1306` both default `--input-dir` to
`/home/pavankrishna/Projets/ASCO/real_asco_files`. `--authorship-json` defaults to a path
inside `outputs/`.

**C4 — Effectively no logging.** One module imports `logging` (`validators/context_validator.py:5`);
the pipeline's total stdout is one `print` of output paths at the end (`pipeline.py:1353`).
A ~50-minute run emits nothing until it finishes. There is no per-stage timing — only LLM
latency is measured. The counterweight is genuinely good: `_run_detector` (`:790`) converts
detector failures into structured `OperationalIssue` rows, and `run_metadata_rows` (`:1140-1229`)
stamps ~90 provenance fields including git SHA, worktree-dirty, and input manifest hash.
The provenance model is right; it just has no runtime signal.

**C5 — No config surface for template detection.** `PipelineConfig` has nine fields, none
template-related. Both detectors accept thresholds as keyword arguments, but `pipeline.py:1060`
and `:1066` pass only `features=`, so nothing is reachable from the CLI. Tuning requires a
code edit. Contrast `rules/llm_response_trace_rules.yaml`, which *is* externalised,
schema-validated, versioned and checksummed — that pattern exists in the repo and template
detection simply doesn't use it.

**C6 — Threshold drift has already started.** `scripts/detect_exact_text_reuse.py:58-62`
re-hardcodes `10, 2, 0.15, 30, 10`, duplicating constants that `thresholds.py:90-93` owns.
Its sibling `detect_entity_normalized_templates.py` imports them correctly.

**C7 — Record IDs are not stable across runs, so reviewer decisions cannot persist.**
`utils.dedupe_records:190-198` rewrites colliding IDs to `{id}__{file_stem}`, and
`docs/pipeline_workflow.md:282` states finding and cluster IDs "are stable only within the
ordering and contents of a run." Outputs overwrite in place. There is no way to record
"an editor already dismissed this pair" and have it survive the next run — which is the
whole point of an editorial triage tool. `enriched_reporting.py:192` emits an
`editor_label: "not_reviewed"` column that nothing ever reads back.

**C8 — Two live-shaped API keys sit in plaintext in `.env`** (one active, one commented).
`.env` is gitignored and confirmed never committed, so this is a workstation-hygiene issue
rather than a leak — but both should be rotated and moved to a secret store.

**C9 — `frontend/server.py` must never be exposed.** It binds `0.0.0.0` (`:99`) with no
authentication on any endpoint, and `POST /api/run-pipeline` (`:75`) lets any caller trigger
a full pipeline subprocess synchronously, serialised behind one in-process lock (`:78`). No
TLS, no rate limiting, no job queue. As a local demo harness this is the correct amount of
code; as a service it is not one.

**C10 — No regression baseline.** Neither evaluator writes metrics to a versioned file, so
there is nothing to diff against. `run_eval.py` asserts *perfection* rather than
"no worse than baseline," which will break the moment the corpus grows.

### D. Maintainability

- **D1** — The dead legacy track (§1): ~265 lines of `template_clustering.py` plus dead
  branches at `pipeline.py:427-475`, computed every run, consumed by nothing user-facing.
- **D2** — Duplicated implementations: `utils.normalize_label:144` vs
  `xml_parser._canonical_label:284` (byte-identical); verdict constant sets in
  `signal_validation.py:20-28` and `scripts/evaluate_template_detection.py:10-28`;
  min-section-tokens 15 (`common:181`) vs 5 (`ent:186`).
- **D3** — 28 ad-hoc scripts including 11 near-identical `export_*.py`, with no documented
  composition order.
- **D4** — The doc set contradicts itself on run counts, test counts, the output-file
  contract, and whether AI-generated-text detection is in scope (`docs/prd.md:64` says out,
  `docs/problemstatement.md:43` says in). `docs/template_detection_limitations.md:16` still
  claims no High/Medium/Low severity is emitted; the pipeline emits it.

---

## 3. Entity extraction: move NER to GPT-OSS

**Decision:** replace the local NER backends (SciSpaCy / PubMedBERT) with the GPT-OSS model
already used by the validation layers, called through the *same* shared client and the same
call discipline.

### 3.1 Why the seam already exists

`entity_extraction.extract_record_entities(title, abstract, *, use_model=True)` (`:275`) is
already a backend-selection point: `:288` picks PubMedBERT if `ASCO_PUBMEDBERT_MODEL` is set,
else SciSpaCy if `ASCO_SCISPACY_MODEL` is set, else rule-only regex. GPT-OSS becomes a third
backend behind that same seam. Nothing above it changes — `build_template_features` still gets
one entity list per record, still calls the model exactly once per record, and the
`entity_model_inference_count` regression guard (`tests/test_template_features.py:50`) keeps
working unchanged.

Crucially, the **merge policy already exists too**: `_merge_model_entities:221-247` lets model
spans fill only the gaps around deterministic spans, with
`always_keep = {url, email, trial_id, date, pvalue, percent, number}`. Keep that contract
exactly. The regexes are already perfect at URLs, emails, dates, p-values, percentages,
numbers and trial IDs — **never spend a token asking the model for those.** Ask only for the
biomedical types the regexes are weak at: `gene, protein, drug, disease, cell_line, biomarker,
pathway, mirna, lncrna, assay, endpoint, population, treatment_class`.

### 3.2 Working inside a 12k window with a rate limit

The model is rate-limited with a **12,000-token context window**. Two consequences that the
existing detector constants do *not* encode — `LLM_TRACE_SEMANTIC_DEFAULT_INPUT_TOKEN_BUDGET`
is 7000 and `..._DEFAULT_MAX_OUTPUT_TOKENS` is 8192, which sum to far more than 12k and must
not be copied verbatim:

**(a) For NER, output is the binding constraint, not input.** The trace detector usually
returns an empty list; an NER pass returns tens of entities per record. Budget the batch on
*expected output*: with ~377 median abstract tokens and ~25–40 entities per abstract,
allow roughly 500 output tokens per record. A safe split of the 12k window is **~4,000 input /
~6,000 output**, leaving headroom — which packs to about **4–6 records per call**. Add
`ENTITY_LLM_*` constants to `thresholds.py` (input budget, output budget, max records per
batch, max concurrent batches) rather than reusing the semantic ones.

**(b) Ask for surface strings, never offsets.** The model returns unique
`{"text": ..., "type": ...}` pairs; Python then locates every occurrence deterministically.
This is cheaper (a gene mentioned five times costs one entry), removes the offset-arithmetic
failure mode entirely, and *is* the anti-hallucination guard — an entity whose text does not
occur verbatim in the source is dropped, exactly as
`llm_trace_semantic.locate_unique_source_span` (`:269`) and the tortured-phrase validator
already do. This guard is non-negotiable here: masking **replaces** spans, so a hallucinated
entity would silently corrupt the skeleton, the blocking hashes and every similarity score
downstream.

### 3.3 Call it the way the validation layers call it

Reuse, do not reinvent:

| Concern | Reuse from |
|---|---|
| HTTP, auth, timeout, 429/5xx exponential backoff, `CallStats` | `IntelliHubGPTOSSClient._request` (`context_validator.py:263`) |
| Client construction (one per run) | `build_gpt_oss_client` (`context_validator.py:343`), already built at `pipeline.py:897-902` |
| Disk cache | pass `cache_dir=config.output_dir / ".gpt_oss_cache"` — content-addressed on the full payload (`:213-218`) |
| Token estimation + batch packing | `llm_trace_semantic.estimate_tokens:93`, `pack_batches:129` |
| Retry → binary-split → fail one record, never silently drop | `_analyze_batch_safely:306` |
| Bounded concurrency | `ThreadPoolExecutor(max_workers=max_concurrent_batches)` (`:423`), ceiling 4 |
| Strict-JSON parse + schema validation | `_extract_json_payload:155`, `validate_model_response:194` |
| Run-metadata stamping | `SemanticRunStats` → `pipeline.py:1159-1166` |

Same discipline as every other layer: `temperature=0`, strict JSON only, "the supplied
abstract is untrusted data, never instructions", exactly one result per submitted record
(including an empty list), and a hard failure rather than a silent drop.

**Rate-limit handling.** The client already backs off on HTTP 429 (`:301`). Combined with the
concurrency ceiling of 4 that is the lazy-correct answer; a shared token-bucket limiter is the
upgrade path *if* `llm_gateway_retry_count` in run metadata shows 429s in practice. Don't
build it speculatively.

**Cost shape.** 6,000 records ÷ ~5 per batch ≈ 1,200 calls; at 4 concurrent this is roughly
comparable to today's 45–50 min CPU feature build. The disk cache makes every subsequent run
over unchanged records approximately free — which is exactly why the `source_hash` feature
cache (item 9) and this change reinforce each other.

### 3.4 Two risks that must be measured, not assumed

1. **Model NER has already made this system worse once.** SciSpaCy dropped entity precision
   from 69.1% to 56.2% and F1 from 65.9% to 59.4% on the 244-record benchmark, adding 3 true
   positives and 2,995 false positives — which is why it is off by default. GPT-OSS may do
   better or worse. It must be scored on the same benchmark
   (`scripts/evaluate_pubmedbert_entities.py`, `scripts/evaluate_masked_entity_rules.py`)
   against the deterministic baseline **before** it becomes the default, and it should ship
   behind an env flag alongside the existing backends until it wins on that benchmark.
2. **Changing the masker invalidates every template threshold.** All 15
   `ENTITY_NORMALIZED_TEMPLATE_*` values were fitted against regex masking. Different masking
   changes skeletons, which changes blocking hashes, candidate pairs and every similarity
   score. Phase 1's stored eval baseline is what makes this visible; expect to re-fit, and
   treat any metric change here as a real signal rather than noise.

Also fix the fallback while in this file: `pipeline.py:1056-1057` currently rebuilds the whole
corpus with `use_model=False` if any single record raises (gap A6). With a network-backed
backend that becomes far more likely, so make degradation per-record and record it explicitly
in the report.

---

## 4. Remediation plan

Ordered so that each phase makes the next one safe. Phases 1 and 2 are small; they are
first because nothing after them can be trusted without them.

### Phase 1 — Make it measurable — **COMPLETE (2026-08-28)**

Both gates were red on arrival; fixing them was a prerequisite for item 1. `run_eval.py`
reconciled `total_finding_count` against `llm_review_priority` while the pipeline builds it
from `llm_risk_priority` (`pipeline.py:511`), and a reconciliation test asserted on the wording
of the tracked file `outputs/frontend_run/final_json.json`, regenerated in `b2f02e5`. Both are
fixed; see `SESSION_CONTEXT.md` for detail.

1. ✅ **CI workflow** — `.github/workflows/ci.yml` runs `pytest tests/` then
   `python scripts/run_eval.py` on push to `master` and on pull requests. Both green:
   284 tests + 122 subtests, `run_eval.py` exit 0.
2. ✅ **Scale benchmark** — `scripts/benchmark_scale.py` builds a synthetic corpus of a
   requested size, runs the real pipeline, and writes per-stage wall clock, peak RSS and run
   counters to `tests/fixtures/scale_baseline.json`. Baseline recorded at 519 records; the
   6,000-record acceptance run (`--records 6000`, ~1.6 h) has not been executed.
3. ✅ **Per-stage timing + progress logging** — `_stage` context manager plus timing in
   `_run_detector`, one timed line per stage, `--quiet` to suppress, `logging.basicConfig`
   only in `main()`. Timings also exposed via `extra=` for machine collection.
4. ✅ **Store eval metrics as a baseline** — `tests/fixtures/eval_baseline.json` holds floors
   for scores and ceilings for error counts; `run_eval.py --update-baseline` regenerates it.
   Behaviour is unchanged today (all 1.000/0) but the gate now survives corpus growth.
5. ✅ **Pin the gold corpus by manifest checksum** — *replaces the original "commit the eval
   corpus"*, which was wrong: `evaluate_template_detection.py` never reads XML (it scores a
   predictions CSV against the gold CSV), CI already runs from the tracked
   `tests/fixtures/eval_corpus`, and the 190 files are third-party publication content that
   should not be redistributed through this repo. The real gap is only that nothing records
   *which* corpus produced the published metrics.
   `golden_data_template_detection_v3.manifest.json` now pins it, and
   `evaluate_template_detection.py --corpus-dir` refuses to score a mismatched corpus.

### Phase 2 — Close the two correctness cliffs (small, changes results — hence after Phase 1)

6. **A1, the recall cliff** — make approximate blocks degrade like exact ones instead of
   vanishing: apply `_bounded_block_pairs`' star fallback to the `shape:`/`prefix:` and
   5-gram routes in `template_matching_common._candidate_pairs`, and replace the raw
   bucket-size cap with a document-frequency (IDF) weight so "common boilerplate" and
   "large family" stop sharing one knob. → verify: a synthetic 60-member family fixture is
   detected; add it to `tests/fixtures/eval_corpus`.
7. **A2, the sentence splitter** — delete `template_features._sentence_spans` and derive
   spans from `utils.split_sentences`; do the same at `detectors/tortured_phrase.py:24`.
   This finishes OE-01, whose own success criterion ("a repo-wide search shows a single
   sentence-splitter implementation") is still unmet. → verify: repo-wide grep for
   `[.!?]` in a splitter context returns nothing; eval metrics do not regress.

### Phase 3 — GPT-OSS NER (design in §3; needs Phase 1's baseline to judge it)

8. **`content_integrity/entity_extraction_llm.py`** — a new module holding the NER prompt,
   `pack_batches`-style budgeting against the 12k window (~4k in / ~6k out, 4–6 records per
   call), strict-JSON schema validation, and verbatim-occurrence resolution of every returned
   surface string. Mirror `llm_trace_semantic.py`'s structure; reuse its `estimate_tokens`
   and `_analyze_batch_safely` retry→split→fail shape rather than re-writing them.
   → verify: unit tests for budget packing, for a hallucinated entity being dropped, and for
   a malformed batch splitting rather than failing the corpus.
9. **Wire it in as a third backend** behind
   `entity_extraction.extract_record_entities:275/288`, selected by an `ASCO_ENTITY_LLM_MODEL`
   env var, reusing the run's existing `IntelliHubGPTOSSClient` (`pipeline.py:897`) with
   `cache_dir` set. Keep `_merge_model_entities`' gap-fill contract and its `always_keep` set
   unchanged. Add `ENTITY_LLM_*` constants to `thresholds.py`.
   → verify: `entity_model_inference_count` stays at one call per record; a second run over
   unchanged input makes zero gateway requests (cache hit).
10. **Score it before trusting it** — run `scripts/evaluate_pubmedbert_entities.py` /
    `evaluate_masked_entity_rules.py` on the 244-record masked benchmark and compare against
    the deterministic baseline (69.1% P / 63.0% R / 65.9% F1). Then re-run the Phase 1
    template eval baseline, since changing the masker changes skeletons, blocking hashes and
    every similarity score. → verify: entity F1 beats the deterministic baseline *and* the
    template eval does not regress — otherwise it stays opt-in and the thresholds get re-fit
    before it becomes the default.
11. **Fix the corpus-wide degradation fallback** (gap A6) — `pipeline.py:1056-1057` must
    degrade per record, not rebuild every feature with `use_model=False`, and must record
    which records were degraded. A network-backed backend makes this failure path routine.
    → verify: a test where one record's LLM call fails leaves the other records model-masked.

### Phase 4 — Performance (only meaningful once Phase 1 can measure it)

12. **Cache features on `source_hash`** — the hash already exists at
    `template_features.py:153` and is unused. A content-addressed on-disk cache makes
    re-runs and incremental batches cheap. With Phase 3 in place this is what keeps repeat
    runs off the rate-limited gateway entirely, and it is the prerequisite for A8.
13. **Parallelise feature extraction** — the local part is pure per-record work (measured
    443–495 ms/record; ~45–50 min at 6k on one core). Note that once Phase 3 lands, the
    per-record cost is dominated by a network call, so the right primitive is the bounded
    `ThreadPoolExecutor` the LLM detectors already use, not a process pool.
14. **Compute blocking once** — hoist a single `_candidate_pairs` pass into the pipeline and
    pass the result to both detectors and `candidate_routes`, replacing the three
    independent rebuilds.
15. **Defer `_aligned_text`** — move it out of the `build_enriched_reports` pair loop and
    run it only for pairs that survive the priority gate in `directional_finding_rows`.
    Drops ~99% of that stage's work.
16. **Swap `difflib` for `rapidfuzz`** behind the existing `_similarity` helpers, and drop
    the redundant reverse pass at `template_matching_common:126-128`.
    → verify each of 12–16 against the Phase 1 benchmark and eval baseline: faster, same metrics.

### Phase 5 — Turn a batch script into a system

17. **Stable record identity** — derive `record_id` from a content hash rather than
    positional disambiguation, so a pair means the same thing across runs.
18. **Persist findings and reviewer decisions** — a SQLite store keyed on stable pair IDs,
    so `editor_label` survives and re-runs can skip settled pairs. This is what makes the
    editorial loop real.
19. **Reference corpus** — with 12, 17 and 18 in place, comparing a new batch against prior
    years becomes an incremental blocking pass rather than a re-run, closing A8.

### Phase 6 — Ops hygiene (independent, can run alongside)

20. `pyproject.toml`, pinned deps, add `pytest` to requirements (and drop `torch`/
    `transformers` if Phase 3 retires the local NER backends).
21. Change the hardcoded `--input-dir` default to `None` and make it required.
22. Move thresholds behind a versioned, checksummed YAML — reusing the exact pattern in
    `content_integrity/rules/__init__.py:91-163` — and expose the detector kwargs through
    `PipelineConfig`.
23. Fix the drift at `scripts/detect_exact_text_reuse.py:58-62` by importing from
    `thresholds.py`.
24. Rotate both `.env` keys; move to a secret store. This matters more once entity
    extraction also depends on that key — a rotation now takes the whole pipeline down.
25. Either put `frontend/server.py` behind auth + a job queue, or mark it explicitly
    local-only and bind `127.0.0.1`.

### Phase 7 — Delete

26. Remove the dead legacy track (D1) once `run_eval.py` is repointed at the enriched
    track, plus the duplicate `normalize_label`/verdict-set implementations (D2).
27. Retire the SciSpaCy and PubMedBERT backends **only if** Phase 3 item 10 shows GPT-OSS
    wins on the entity benchmark. Until then all three stay behind their env flags.
28. Reconcile the doc set (D4) — at minimum `docs/template_detection_limitations.md:16`
    and the PRD/problem-statement scope contradiction.

---

## 5. Verification

- **Unit/behaviour:** `python -m pytest tests/` — 284 tests plus 122 subtests. Green as of
  Phase 1; it was red before (`test_authorship_checks_come_from_frontend_json` asserted on the
  wording of the tracked file `outputs/frontend_run/final_json.json`, regenerated in `b2f02e5`).
- **Detection regression:** `python scripts/run_eval.py` (exit code gates), and
  `python scripts/evaluate_template_detection.py` against `golden_data_template_detection_v3.csv`
  once its corpus is committed.
- **Entity quality:** `scripts/evaluate_pubmedbert_entities.py` and
  `scripts/evaluate_masked_entity_rules.py` on the 244-record masked benchmark — GPT-OSS NER
  must beat the deterministic 69.1% P / 63.0% R / 65.9% F1 baseline before it becomes the
  default (Phase 3 item 10).
- **Scale:** the Phase 1 benchmark at 6,000 records — runtime, peak RSS, candidate-pair
  count, model inference count, and (post-Phase 3) gateway request/retry/failure counts from
  `CallStats` — compared against the committed baseline after every Phase 3 and 4 change.
- **New coverage needed:** a >50-member template family fixture (proves A1 is fixed), an
  abbreviation-heavy abstract fixture (proves A2 is fixed), and a stubbed-client fixture
  returning a hallucinated entity (proves the verbatim-occurrence guard drops it).

---

## 6. Honest summary

The detection design is sound and unusually well documented about its own limits. What
stands between it and production is mostly not detector cleverness — it is that the system has
never been measured at its target scale, cannot persist a reviewer's decision, silently
loses recall on exactly the large families it exists to catch, and has no automation
guarding any of the 284 tests already written. Phases 1 and 2 are a few days of work and
close the two gaps that actually change outcomes.

The one place detector quality *is* the question is entity extraction, and that is what
Phase 3 addresses by moving NER onto GPT-OSS. It is a small diff — the backend seam and the
merge policy already exist — but it is the one change that alters what every threshold means,
so it is sequenced after the eval baseline exists and is gated on beating the deterministic
benchmark rather than assumed to be an improvement. The last time a model NER backend was
added, it made the system measurably worse.
