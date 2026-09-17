# Batch Operations Runbook

This runbook covers running the content-integrity pipeline on large batches, recovering from interruptions, and verifying outputs.
Measured capacity and the readiness conclusion are in [Phase 2 reliability and capacity](PHASE2_RELIABILITY_CAPACITY.md).

## Evidence Rules

Every readiness claim must name its evidence.
Authorized real abstracts show behaviour on the currently available real inputs only.
Synthetic datasets from `scripts/generate_load_dataset.py` show orchestration, runtime, memory, and failure handling at volume, never detector accuracy.
The GPT-OSS test double (`scripts/fake_gpt_oss_gateway.py`) shows control flow under repeatable responses and failures, never real model latency or capacity.
Only a staged live gateway test measures real model-service behaviour, and only at the volume tested.

The pipeline produces signals for editorial review.
It does not accept, reject, or score abstracts, and customer manuscripts must not be used for model training or fine-tuning.

## Commands

Run one batch against the configured gateway:

```bash
python scripts/run_pipeline.py --input-dir /approved/abstracts --output-dir /runs/batch-2026-09
```

Run deterministically without any gateway:

```bash
python scripts/run_pipeline.py --offline --input-dir /approved/abstracts --output-dir /runs/batch-offline
```

Measure the available real abstracts (label them explicitly as authorized real data):

```bash
python scripts/benchmark_scale.py --dataset real_asco_files --evidence real --runs 1 \
  --model-mode offline --output outputs/benchmarks/real-offline
python scripts/benchmark_scale.py --dataset real_asco_files --evidence real --runs 1 \
  --model-mode fake-gateway --output outputs/benchmarks/real-test-double
```

Generate and run the 6,000-record synthetic workload three times against the test double:

```bash
python scripts/generate_load_dataset.py --profile scale --output-dir outputs/load_datasets/scale
python scripts/benchmark_scale.py --dataset outputs/load_datasets/scale --runs 3 \
  --model-mode fake-gateway --output outputs/benchmarks/scale-6000
```

Other profiles are `ci`, `medium`, `high_similarity`, `long_abstract`, `malformed`, and `mixed`.
Generated datasets and benchmark outputs belong under `outputs/` (ignored by Git) or another location outside the repository.
The same generator arguments reproduce identical bytes; each dataset's `manifest.json` records the generator version, seed, parameters, file hashes, and expected input counts.
The manual `Synthetic load test` GitHub workflow runs the same commands; pull-request CI runs only the fast tests.

## Outputs And Their Contract

A successful run writes four files to the output directory.

| File | Contents |
|---|---|
| `content_integrity_results.json` | DOI-keyed report; one entry per retained record |
| `Editor_Triage_Workbook.xlsx` | Editor workbook; All Abstracts and Check Detail have one row per retained record |
| `run_metrics.json` | Run ID, host, configuration, per-stage wall/CPU/peak memory, throughput, gateway telemetry |
| `run_summary.json` | Record status totals, reconciliation checks, failures, skipped inputs, template counts, output hashes |

Every input record ends with exactly one status.

| Status | Meaning |
|---|---|
| `completed` | Every check ran and nothing requires editor review |
| `completed_with_findings` | Every check ran and editor review is required |
| `failed` | At least one check is incomplete for this record; see its `failures` |
| `skipped` | An exact duplicate of another input record in the same source; listed in `run_summary.json` only |

`completed + completed_with_findings + failed + skipped` always equals the number of input records.
A failure entry gives the stage, error category, a safe message, the retry count, and whether a retry can succeed.
Status is operational, not a scientific verdict.

Reports are written into a staging directory, read back, and reconciled before they replace previous output.
Reconciliation checks the status totals, record identity and counts in both reports, per-record agreement of status and active finding counts, and template pair links.
If reconciliation fails, the previous reports stay in place, the staging directory is kept for inspection, and the CLI exits with code 3.
`run_summary.json` is renamed last and records the SHA-256 of the other three files; if the hashes do not match the files, the promotion was interrupted and the run must be repeated.

Records whose DOI is shared with another retained record are keyed by abstract ID in the JSON and fail with category `duplicate_identifier` for identity review.

## Failure Categories

| Category | Retried automatically | Meaning |
|---|---|---|
| `rate_limit` | yes | Gateway returned 429; all requests pause for the Retry-After or backoff interval |
| `server_error` | yes | Gateway returned 5xx |
| `timeout` | yes | Connect, read, or whole-response deadline exceeded |
| `connection` | yes | Connection refused, reset, or closed without a response |
| `invalid_response` | yes | Gateway reply was not a usable completion envelope, or model content stayed invalid after retry and split |
| `circuit_open` | no (fails fast) | Recent requests kept failing; the client paused traffic |
| `authentication` | no | Gateway rejected the credential; the client stops calling for the rest of the run |
| `invalid_request` | no | Other 4xx response |
| `configuration` | no | Gateway settings missing or TLS certificate verification failed |
| `invalid_input` | no | XML could not be parsed or the record has no usable title or abstract text |
| `duplicate_identifier` | no | DOI shared by more than one retained record |
| `validation_failed` | yes | Optional model validation produced no usable verdict for a finding |
| `registry_lookup_failed` | yes | ClinicalTrials.gov lookup failed while `--verify-trials` was enabled; without it, uncached IDs are simply not looked up |
| `processing_error` | no | A detector raised on this record; other records still receive the check |

Log lines contain run IDs, record IDs, stage names, categories, and timings only.
Issue messages in the reports are truncated to one line and never include credentials.

## Gateway Settings

| Setting | Default | Where |
|---|---:|---|
| Requests in flight across all model stages | 4 | `--llm-max-concurrency` |
| Connect and TLS handshake timeout | 10 s | `CONTEXT_VALIDATOR_DEFAULT_CONNECT_TIMEOUT_SECONDS` |
| Socket read timeout | 120 s | `CONTEXT_VALIDATOR_DEFAULT_TIMEOUT_SECONDS` |
| Whole-response deadline per attempt | 300 s | `CONTEXT_VALIDATOR_DEFAULT_REQUEST_DEADLINE_SECONDS` |
| Attempts per request | 3 | `CONTEXT_VALIDATOR_DEFAULT_MAX_ATTEMPTS` |
| Backoff base and cap (full jitter) | 1 s, 30 s | `CONTEXT_VALIDATOR_DEFAULT_BACKOFF_SECONDS`, `..._MAX_BACKOFF_SECONDS` |
| Circuit opens after consecutive exhausted requests | 5 | `CONTEXT_VALIDATOR_DEFAULT_CIRCUIT_FAILURE_THRESHOLD` |
| Circuit cooldown | 60 s | `CONTEXT_VALIDATOR_DEFAULT_CIRCUIT_COOLDOWN_SECONDS` |

The numeric defaults live in `content_integrity/thresholds.py`.
Set `--llm-max-concurrency` no higher than the concurrency the gateway owner has approved; the GPT-OSS deployment is rate-limited and has a 12,000-token context window.
The deployed IntelliHub gateway is used with `INTELLIHUB_VERIFY_SSL=false`; `INTELLIHUB_CA_BUNDLE` is the safer alternative when a CA bundle is available.
Gateway responses are cached under `<output-dir>/.gpt_oss_cache`, keyed by gateway URL, credential scope, model, prompt version, and request, so repeated or resumed runs do not repeat completed calls.

## Restart And Recovery

The pipeline checkpoints after record checks, template features, template pair detection, and enriched template reports.
Checkpoints live in `<output-dir>/.checkpoint` and are removed after reports are promoted.

After an interruption (crash, kill, out-of-memory, write failure, or reconciliation failure):

1. Fix the cause if one is known, such as free disk space or restore gateway access.
2. Rerun the identical command with the same input directory, dictionary, options, and code.
3. The run resumes after the last completed stage and logs `checkpoint: resuming after ...`; `run_metrics.json` lists the restored stages and earlier run IDs.
4. Confirm `run_summary.json` reports `"status": "succeeded"` and `"reconciled": true`.

A checkpoint is reused only when code, inputs, parsed records, dictionary, options, and gateway identity are unchanged.
Otherwise the run stops with exit code 2 and names what differs; restore the original setup or rerun with `--discard-checkpoint` to start again.
Resuming never re-runs completed stages, so records that failed with a retryable category keep that result; rerun with `--discard-checkpoint` to retry them, and cached responses keep completed model calls free.
Use one output directory per concurrent run, and only resume from checkpoints this pipeline wrote into a directory you control.

## Known Operational Limits

Template candidate generation caps approximate-match buckets at 50 records, and exact-match buckets beyond that size are linked as a star rather than every pair.
Pair comparisons and report rows therefore grow with the number of similar records inside each bucket, not with the square of the batch size.
All pipeline state for a batch is held in memory; measured peaks were 270 MB for the real 519 abstracts and 1,034 MB for 3,000 synthetic records (see the Phase 2 report).
Template comparison runs on one CPU core; the 3,000-record synthetic run took 16.5 minutes with the test double.
No deployment CPU or memory limit has been agreed yet; allow at least 4 GiB RAM for a 6,000-record batch until one is measured and agreed.
Resumed runs report gateway telemetry for their own invocation only.
