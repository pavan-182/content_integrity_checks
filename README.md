# Content Integrity Checks

Scientific-abstract screening for human review: explicit LLM response residue, known tortured phrases, and text or template reuse.
Numerical, study-design, and trial-reference checks provide corroborating evidence.
Phase 1 stabilizes processing and reproducibility; it does not add an AI-authorship classifier, AI probability, or automated publication judgment.
Existing risk-labelled report fields are legacy heuristic review priorities, not calibrated probabilities or misconduct determinations.
Authorship Integrity is outside this work; the pipeline no longer automatically imports a local authorship report.

## Installation

Supported baseline: Python 3.12 and Node 22 (at least 22.12; CI uses 22.22.2), with npm 10.
Run commands from the repository root unless stated otherwise.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
```

Production-only installations use `requirements.txt`.
Both files pin direct and transitive dependencies; Torch and Transformers are not required.
When updating dependencies, test the complete pinned set and update the relevant requirements together.
The frontend lockfile records the resolved dependency graph; use `npm ci` for verification.

## Offline Example

```bash
python scripts/run_pipeline.py --offline \
  --input-dir tests/fixtures/eval_corpus \
  --output-dir outputs/synthetic
```

This processes the checked-in synthetic corpus without credentials or network access.
`--offline` skips entity-model configuration and rejects network-enabled options.
For your own approved XML, supply its directory with `--input-dir`; the portable default is `metadata_files`.
Missing or empty input directories fail before reports are written.
The default dictionary is `🤷_tortured.csv`; override it with `--tortured-dictionary PATH`.

## Gateway Configuration

`.env.example` contains placeholders only.
Create a private `.env` using the names below, or provide shell environment variables.
Environment values override the file; `INTELLIHUB_ENV_FILE` selects another file.
Configuration is read when constructing a client and never copied into the process environment.

| Variable | Contract |
|---|---|
| `INTELLIHUB_API_KEY` | Gateway credential; legacy `api_key` remains accepted |
| `INTELLIHUB_BASE_URL` | Absolute HTTP(S) base URL, such as `https://gateway.example.invalid/v1`; no embedded credentials/query/fragment |
| `INTELLIHUB_MODEL` | Deployment model name |
| `INTELLIHUB_MODEL_ID` | Optional provenance label; defaults to the model name's last path component |
| `INTELLIHUB_VERIFY_SSL` | `true` by default; use a CA bundle for private certificates |
| `INTELLIHUB_CA_BUNDLE` | Optional certificate bundle path |

Without `--offline`, entity extraction uses the configured GPT-OSS gateway, even when validation is disabled.
Missing configuration is reported as an operational failure and deterministic checks continue.
Only send approved data to a configured deployment.

```bash
python scripts/run_pipeline.py --input-dir metadata_files --output-dir outputs/review \
  --detect-llm-semantic --validate-llm
```

`--detect-llm-semantic` enables semantic residue discovery; `--validate-llm` validates eligible findings.
`--verify-trials` enables ClinicalTrials.gov lookups; otherwise trial checks use local logic and available cache entries.
`--llm-max-concurrency` bounds GPT-OSS requests in flight across every model stage (default 4).
The existing `--detect-nonsense-candidates` option is experimental and excluded from canonical findings; Phase 1 does not improve that detector.

## Outputs And Isolation

Each output directory receives `content_integrity_results.json`, `Editor_Triage_Workbook.xlsx`, `run_metrics.json`, and `run_summary.json`.
Reports are local generated artifacts and must not be committed.
The JSON is keyed by normalized DOI, falling back to abstract ID; records sharing a DOI are keyed by abstract ID and fail with a `duplicate_identifier` issue instead of stopping the batch.
Each submission has `title`, `abstract_id`, and a `checks` array.
The `content_integrity_summary` supporting record includes `processing_status`, `parse_status`, `source_file`, and diagnostic `operational_issues`.
`processing_status` is `failed` if any reported stage failed, otherwise `successful`; success describes execution, not scientific validity.
`record_status` refines it as `completed`, `completed_with_findings`, or `failed`, and `failures` lists each failed stage with its error category, retry count, and whether a retry can succeed.
Exact duplicate inputs are `skipped` and listed in `run_summary.json`; every input record receives one status.
Missing optional metadata can yield `parsed_with_warnings` with successful processing.
The workbook's `All Abstracts` sheet includes the same processing and record status, and `Check Detail` carries failure messages.
Reports are staged, read back, and reconciled before replacing earlier output; a reconciliation failure keeps the previous reports.
An interrupted run resumes from its checkpoint when rerun with the same inputs and options; see the [operations runbook](docs/OPERATIONS.md).
Template pairs retain their sub-checks; record-level contradiction and trial evidence appears once under template supporting data.

Use a distinct output directory for each simultaneous run; report filenames are fixed and an existing report is replaced only after the new run reconciles.
Each pipeline invocation owns an entity extractor, bounded memory cache, client, and metrics.
Library callers can pass `run_pipeline(config, llm_client=client)` with a client owned exclusively by that run.
`EntityExtractor(client)` can also be passed explicitly to masking and feature extraction helpers; helpers without an extractor use deterministic rules.
The run's `.gpt_oss_cache` hashes gateway URL, credential scope, endpoint, full prompt/version, model, input, temperature, and token budget.
Cached responses contain source-derived content; keep cache directories private and out of Git.
Atomic cache writes allow compatible clients to share a cache directory, but separate output directories remain required.
Timestamps, paths, timings, Git state, and live model responses can differ between runs; deterministic findings should remain stable.

## Verification

```bash
python -m compileall -q content_integrity scripts tests frontend
python -m ruff check content_integrity scripts tests frontend
python -m pytest tests/ -q
python scripts/run_eval.py
cd frontend
npm ci
npm test
npm run build
```

Tests mock transport, block live sockets, and isolate gateway settings, including class-level test setup.
The synthetic evaluation runs offline by default and gates template pair, family, and abstract flags against `tests/fixtures/eval_baseline.json`.
It does not establish production precision, general AI detection, or scale readiness.
See [evaluation scope](docs/EVALUATION_PLAN.md) and the [Phase 1 handoff](docs/PHASE1_STABILIZATION.md).

## Frontend

After building, start the server from `frontend/` with an approved input directory:

```bash
ASCO_PIPELINE_INPUT=/absolute/path/to/approved/xmls python server.py
```

The server listens on port 8000 (`PORT` overrides it), serves `dist/`, and writes to `outputs/frontend_run`.
Its Run Pipeline action invokes the regular gateway-enabled pipeline; use the CLI offline example for network-free execution.
For development, `npm run dev` starts the API and Vite together; Vite proxies API requests to port 8000.
Do not run two servers writing the same output directory.

## Deferred Work

Phase 2 reliability and capacity work is recorded in [Phase 2 reliability and capacity](docs/PHASE2_RELIABILITY_CAPACITY.md); live gateway quotas remain to be measured with approval.
Phase 3 will evaluate unknown tortured phrases and broader detector reliability on reviewed data.
Neither production load claims nor new detection judgments are part of Phase 1.
Tracked-data cleanup requires owner approval; see the [data inventory](docs/DATA_HYGIENE.md).
