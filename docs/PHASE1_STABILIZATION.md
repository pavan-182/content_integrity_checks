# Phase 1 Stabilization

## Baseline And Scope

Baseline: branch `master`, commit `f5d93b14a72feb4e77ff64a42c831014ec689d0c`.
The initial worktree contained user edits only in `AGENTS.md` and `CLAUDE.md`; those edits were preserved.
Environment: Ubuntu 24.04.4 LTS/Linux x86-64, kernel `7.0.0-31-generic`, Python 3.12.3, Node 22.22.2, npm 10.9.7.
No push, pull request, publication, tracked-data deletion, or remote write was performed.
GitHub run `34936183256` was confirmed failed at Unit tests, with Detection regression skipped.
A passing remote run for this local change requires an authorized commit/push and remains outside the verified local evidence.

## Reproduced Problems

| Lead | Evidence and classification |
|---|---|
| Global entity client/counter | Production defect: a rejecting pipeline fake affected later feature extraction; isolated baseline reproduced 15 failures |
| Implicit cache-test URL | Test defect plus missing constructor validation: four tests built a relative `/chat/completions` URL |
| Cache identity | Production defect: cache key included endpoint/payload but omitted gateway URL and credential scope |
| Import-time configuration | Production/test defect: importing the validator loaded `.env` into `os.environ`; model defaults were captured at import |
| PubMedBERT test | Test defect: patch targeted the removed `_pubmedbert_pipeline` symbol |
| Frontend report test | Test defect: ENOENT for ignored `outputs/test_real/content_integrity_results.json` |
| Sentence segmentation | Additional production race: forced overlapping PySBD calls returned an empty sentence list for one input |
| Preprocessing fallback | Production defect: one entity failure discarded all model features and reporting did not mark template coverage incomplete |
| Empty input | Production defect: CLI reached `_inventory_rows` and raised ZeroDivisionError instead of identifying invalid input |
| Finding order | Production determinism defect: unordered tortured-rule index keys changed JSON evidence order across Python hash seeds; sorting the keys preserves detections |
| Dependencies/configuration | Reproducibility defects: broad Python ranges, mandatory unused model stack, frontend `latest`, missing developer requirements and `.env.example` |

The credential-isolated baseline command was:

```bash
env INTELLIHUB_ENV_FILE=/dev/null INTELLIHUB_API_KEY= INTELLIHUB_BASE_URL= INTELLIHUB_MODEL= api_key= \
  .venv/bin/python -m pytest tests/ -q
```

Result: 269 passed, 15 failed, 122 subtests passed.
Compilation passed and `scripts/run_eval.py` passed its existing template baseline with precision/recall 1.000 and zero reconciliation errors.
Frontend report tests reproduced the missing file; production build passed.
The ambient environment was unsuitable as a baseline because import-time `.env` loading changed behavior.
Initial clean installation was blocked by sandbox DNS; approved network access was used only for package installation.
The obsolete requirements began downloading a 554.6 MB Torch wheel plus its dependency stack; that install was stopped after runtime reference searches confirmed it was unnecessary.
The final isolated environment contains only the pinned runtime and development requirements.

## Architecture And Behavior

`EntityExtractor` owns one run's client, bounded 512-entry memory cache, lock, and inference-attempt counter.
Extraction dependencies pass explicitly through `build_template_features`, record extraction, typed extraction, and masking.
There is no global client setter or global inference counter.
The lock serializes entity inference within a run; separate runs do not share it.
PySBD is instantiated per split call because its implementation stores `original_text` on the instance.
Preprocessing failures attach diagnostics to the affected record and retain deterministic features there while preserving other records' model features.

Gateway cache identity includes base URL, hashed credential scope, endpoint, model, full messages, prompt version, temperature, and token budget.
Every GPT-OSS call site includes its prompt version in the system message, so a version change invalidates cached responses.
Writes remain atomic; malformed JSON, invalid response envelopes, and expired entries are cache misses.
Truncated responses are not cached and are surfaced to chunk splitting without wasting transport retries.
Gateway telemetry updates are protected across detector threads.
Configuration files are parsed per client without environment mutation, and model provenance comes from that client.

The normal gateway-enabled path is preserved; `--offline` adds an explicit deterministic path and powers the synthetic evaluation.
Conflicting offline/network options fail before processing.
Standalone masking without an explicit extractor uses rules only, as a fresh process did previously.
Library clients must be exclusively owned by their run, and simultaneous runs must use different output directories.
Shared compatible disk cache directories are supported; fixed report filenames are not a multi-writer output store.

JSON summary supporting data now includes execution status, parse status, source file, and operational diagnostics.
The workbook projects the same `successful`/`failed` processing status.
Failed processing is independent of scientific evidence and legacy review-priority labels.
The internal canonical schema version is 1.1; the DOI-keyed integration format retains its existing checks and adds diagnostic fields.
An authorship report is no longer auto-loaded; the explicit legacy workbook argument remains supported but outside this phase.
No overall AI probability, new detector, risk formula, or final automated decision was added.

## Removed Code And Evidence

| Removed | Reference and execution evidence |
|---|---|
| `set_entity_llm_client`, module client/cache/counter | All production callers were pipeline setup and masked-entity export; both now pass a run extractor |
| PubMedBERT evaluator and its `_merge` test | Evaluator was the only `transformers` importer; no runtime callers or documented ongoing owner/use case; production merge coverage already exercises deterministic precedence |
| Mandatory Torch/Transformers | No remaining production imports; clean install, tests, and synthetic execution use only five runtime packages |
| `RULE_BY_ID` | Reference search found only its definition; semantic detection uses the catalogue directly |
| `NON_ALNUM_RE` | Reference search found only its definition; matching uses other explicit normalization patterns |
| `write_jsonl` | Reference search found only its definition; main pipeline writes canonical JSON and workbook |
| `_pair_finding_rows` | Only caller was a CSV-header test; production uses `directional_finding_rows` from enriched reports; the header test now supplies a minimal row directly |
| Shared PySBD instance | All uses routed through `split_sentences`; replaced there after forced-concurrency reproduction |

`ENTITY_PROMPT_VERSION` was retained and now participates in requests/cache identity and run metadata.
The model-independent entity and template evaluators remain supported for explicit reviewed input files.
Tracked reports and datasets were left untouched; see [data hygiene](DATA_HYGIENE.md).

## Verification Commands

```bash
python -m pip install -r requirements-dev.txt
python -m pip check
python -m compileall -q content_integrity scripts tests frontend
python -m ruff check content_integrity scripts tests frontend
python -m pytest tests/ -q
python scripts/run_eval.py
python scripts/run_pipeline.py --offline --input-dir tests/fixtures/eval_corpus --output-dir /tmp/asco-phase1-example
cd frontend
npm ci
npm test
npm run build
```

The repository's Ruff gate selects correctness rules `E9`, `F63`, `F7`, and `F82` and does not inherit personal style configuration.
Historical style findings are not a Phase 1 formatting rewrite; the undefined `Finding` annotation was fixed.
Regression coverage includes sequential and concurrent pipelines, per-run entity caching, forced sentence-split interleaving, chunk truncation, URL/model/prompt/input/credential cache isolation, corruption, expiration, atomic concurrent writes, configuration isolation, mixed-success JSON/workbook reconciliation, and identical CLI output across three Python hash seeds.
Tests disable live sockets and isolate environment state before class-level setup.
Final verification on 2026-09-15 used `/tmp/asco-phase1-venv`, created without system site packages, and `/tmp/asco-phase1-clean`, exported from tracked files plus the new Phase 1 source files.
The export excluded local `.env`, the existing virtual environment, ignored pipeline outputs, caches, and node_modules; tracked data was preserved.
Python tests and evaluation also ran under `env -i` with only the executable PATH supplied.

| Check | Result |
|---|---|
| `python -m pip check` | No broken requirements |
| `python -m compileall -q content_integrity scripts tests frontend` | Exit 0 |
| `python -m ruff check content_integrity scripts tests frontend` | All checks passed |
| `python -m pytest tests/ -q` in clean export | 313 passed, 122 subtests passed, 69.28 seconds |
| Same suite with reversed test-file order | 313 passed, 122 subtests passed, 55.21 seconds |
| `python scripts/run_eval.py` in clean export | Exit 0; all four template precision/recall pairs 1.000; zero merge, split, flag, or counting errors |
| Offline CLI, synthetic corpus, hash seeds 1 and 42 | Identical canonical JSON for 43 records; 43 successful statuses; 43 matching workbook rows |
| Mixed-success synthetic pipeline regression | Three inputs accounted for: one successful, one injected entity failure, one malformed XML; JSON/workbook statuses reconciled |
| `npm ci` | Passed from locked dependencies; clean export also passed using the populated offline npm cache |
| `npm test` | All three JavaScript test files and the Python server test passed |
| `npm run build` | Vite production build passed |
| `git diff --check` | Exit 0 |
| Tracked reports, datasets, and evaluation baseline diff | Unchanged |

The reverse-order command was:

```bash
python -c 'import pytest; from pathlib import Path; raise SystemExit(pytest.main([*[str(p) for p in sorted(Path("tests").glob("test_*.py"), reverse=True)], "-q"]))'
```

The 43-record CLI runs used `--offline --input-dir tests/fixtures/eval_corpus`, distinct `/tmp/asco-e2e-first` and `/tmp/asco-e2e-second` output directories, and `PYTHONHASHSEED=1` / `PYTHONHASHSEED=42` respectively.
These are correctness checks, not a throughput benchmark.

## Changed Files

| Area | Files |
|---|---|
| Run isolation and output contracts | `content_integrity/entity_extraction.py`, `template_features.py`, `pipeline.py`, `utils.py`, `reporting.py` |
| Gateway configuration/cache/provenance | `content_integrity/validators/context_validator.py`, `llm_trace_validator.py` |
| Prompt versions and stable rule ordering | `content_integrity/detectors/design_contradiction.py`, `llm_trace_semantic.py`, `nonsense_candidate.py`, `tortured_phrase.py` |
| Supported scripts | `scripts/export_masked_entities.py`, `scripts/run_eval.py`; obsolete `scripts/evaluate_pubmedbert_entities.py` removed |
| Regression tests | `tests/conftest.py`, `test_run_isolation.py`, `test_context_validator.py`, `test_entity_extraction.py`, `test_template_features.py`, `test_pipeline.py`, `test_reporting_reconciliation.py`, `test_llm_response_trace_pipeline.py`, `test_nonsense_candidate.py`, `test_template_architecture.py`; obsolete `test_evaluate_pubmedbert_entities.py` removed |
| Frontend | `frontend/package.json`, `package-lock.json`, `server.py`, `src/report.test.js`, `src/fixtures/content_integrity_results.json` |
| Setup and CI | `.env.example`, `requirements.txt`, `requirements-dev.txt`, `ruff.toml`, `.github/workflows/ci.yml` |
| Documentation | `README.md`, `TEMPLATE_DETECTION_PLANNED_VS_DELIVERED.md`, `docs/EVALUATION_PLAN.md`, `pipeline_workflow.md`, `DATA_HYGIENE.md`, `PHASE1_STABILIZATION.md` |

Existing user changes in `AGENTS.md` and `CLAUDE.md` are not part of this implementation.

## Remaining Limits And Estimates

Gateway accuracy, deployed model availability, and live service quotas were not tested with credentials or manuscripts.
The synthetic template baseline is unchanged and is not evidence of reliable 6,000-record processing.
Duplicate normalized DOIs still fail output integration explicitly; ingestion duplicate counts are recorded, but batch identity policy for a production corpus needs Phase 2 review.
Reports are rewritten on rerun, and output-directory isolation is required for concurrent runs.
Existing experimental nonsense candidates remain excluded from canonical findings and operational summaries; their research evaluation is not expanded here.
Legacy research/design documents retain historical measurements and diagrams; README and the maintained pipeline workflow define current operation.
No licensing or inclusion approval was inferred for tracked source/report data.

The original Phase 1 estimate of 1-2 engineering weeks remains appropriate for review, deployment validation, and CI acceptance.
The additional sentence-segmentation race and configuration side effects increased correctness scope, while removal of the unused model stack simplified setup.
Phase 2 estimate: 5-10 engineering days after an approved 6,000-record corpus, representative labels, target hardware, and gateway quotas are available.
That covers stepped load tests, memory/latency measurements, record and workbook reconciliation, interruption/retry behavior, and tuning driven by measured bottlenecks; waiting for external access is additional.
Phase 3 remains separate: unknown tortured-phrase detection and comprehensive detector evaluation.

## Primary References

- [pip repeatable installations](https://pip.pypa.io/en/stable/topics/repeatable-installs/) supports pinning direct and transitive dependencies.
- [npm ci](https://docs.npmjs.com/cli/v11/commands/npm-ci/) defines lockfile-based clean installation.
- [Python functools](https://docs.python.org/3/library/functools.html) documents bounded caching and duplicate concurrent calls; the extractor lock prevents duplicate work within a run.
- [PySBD source](https://github.com/nipunsadvilkar/pySBD/blob/master/pysbd/segmenter.py) shows mutable `original_text`; installed 0.3.4 source was inspected as well.
- [Inspected GitHub Actions run](https://github.com/pavan-182/content_integrity_checks/actions/runs/34936183256) failed during baseline Python tests.
