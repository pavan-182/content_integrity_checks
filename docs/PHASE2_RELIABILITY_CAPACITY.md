# Phase 2 Reliability And Capacity

## Scope And Evidence Categories

Phase 2 hardened batch processing for a planned 6,000-abstract batch and measured it.
It did not change detector thresholds, add detectors, or create any overall risk or AI score.
Every result below names its evidence category.

| Evidence | What it shows | What it does not show |
|---|---|---|
| 519 authorized real abstracts | Behaviour on the currently available real inputs | Behaviour across a full 6,000-abstract population |
| 6,000 synthetic abstracts | Orchestration, memory, runtime, and failure handling at target volume | Detector accuracy on real ASCO abstracts |
| GPT-OSS test double | Control flow under repeatable model responses and injected failures | Real GPT-OSS latency, throughput, or service capacity |
| Live GPT-OSS | Not run: no approval for credentials, volume, or cost | Anything about the live service |

## Baseline

| Item | Value |
|---|---|
| Branch and commit | `master` at `f5d93b14a72feb4e77ff64a42c831014ec689d0c`, with the uncommitted Phase 1 worktree |
| Host | Ubuntu 24.04 on Linux 7.0.0-31-generic, x86-64, 4 CPUs, 15 GiB RAM, 3 GiB swap |
| Python / Node | 3.12.3 in `/tmp/asco-phase1-venv` (pinned requirements only) / 22.22.2, npm 10.9.7 |
| GPT-OSS deployment | IntelliHub gateway, 12,000-token context, rate-limited, used with `INTELLIHUB_VERIFY_SSL=false`; approved quota and concurrency are not documented |
| Input and output storage | Local filesystem; reports, checkpoints, and caches under the chosen output directory |
| Cache | Content-addressed gateway response cache under `<output>/.gpt_oss_cache`; trial registry cache under `<output>/.trial_registry_cache` |

Before any change the Phase 1 verification passed: `pip check`, compilation, Ruff, 313 Python tests and 122 subtests, `scripts/run_eval.py` at precision and recall 1.000, and 12 frontend tests plus the production build.
No live gateway call, push, pull request, tracked-data deletion, or dataset publication was made in Phase 2.

## Workload Characterization (Real, Aggregate Only)

| Measure | 519 authorized abstracts |
|---|---|
| Files | 2 bundled JATS XML files (1.97 MB and 2.13 MB); 2 `article` roots and 517 `sub-article` records |
| Parse results | 519 parsed, 0 warnings, 0 failures, 0 duplicate record IDs, 0 duplicate DOIs, 519 with DOI |
| Sections | 480 with four sections, 25 with three, 7 with five, 3 with two, 4 with one; 518 structured |
| Abstract length | 1,050 to 2,988 characters, median 2,522, p95 2,899; 149 to 546 words, median 377 |
| Trial identifiers | 22 records cite at least one NCT identifier |
| Template candidates (offline) | 2,382 candidate pairs (4.6 per record, 1.8% of all 134,421 pairs); 18 prioritized pairs; 1 family |
| Candidate growth | 0.7, 1.2, 2.5, 2.7, 4.6 candidate pairs per record at 50, 100, 200, 300, 519 records |
| GPT-OSS requests | Not measured live; see the test-double results |

The candidate-pair share fell only slowly as the batch grew, so pair comparisons, not per-record work, dominate at scale.
Extrapolating the measured growth, a real 6,000-abstract batch is estimated at roughly 150,000 to 300,000 candidate pairs; this is an estimate, not a measurement.

## Test Datasets

`scripts/generate_load_dataset.py` (generator `asco-synthetic-load-v4`) writes seeded, reproducible datasets in the real bundle shape with a manifest of generator version, seed, parameters, per-file SHA-256, and expected counts.
All text comes from the grammar in the script; no manuscript text is copied, and datasets are written under ignored `outputs/`.

| Profile | Records | Purpose |
|---|---:|---|
| `ci` | 60 | Fast tests (generated in temporary directories) |
| `medium` | 1,000 | Stepped benchmark |
| `scale` | 6,000 (and 3,000 via `--records`) | Target volume |
| `high_similarity` | 1,500 | 90% of records in template families of 30 to 120 members |
| `long_abstract` | 300 | Abstracts of 6,000 to 14,000 characters |
| `malformed` | 13 inputs | Truncated, bad encoding, empty, non-XML, unexpected root, entity expansion, empty abstract, no text, ID collision, repeated sub-article |
| `mixed` | 1,013 inputs | Valid bundles interleaved with the malformed cases |

The first grammar produced 116 candidate pairs per record at 519 records, 25 times the real density, which would have overstated template cost.
After tracing the excess to fixed multiword phrases, repeated sentence boundaries, and five title templates, the final grammar produces about 2.5 times the real density at 519 records (6,000 routed pairs versus 2,382; shared masked five-gram pairs 2,131 versus 1,160; title pairs 1,466 versus 767).
The synthetic workload is therefore deliberately somewhat harsher than the real batch for template comparison.
Section structure (4 sections dominant), length (median 2,528 characters), and trial citation rate (about 4%) match the real aggregates.

## Confirmed Root Causes

| Problem | Evidence | Fix |
|---|---|---|
| Sentence segmentation dominated runtime | Profiling the real batch: `split_sentences` (PySBD) took 754 of 1,004 profiled seconds, called once per entity on text prefixes and again per detector | Memoized pure segmentation; entity sentence index computed from one full-text split |
| Entity sentence index pointed at the wrong sentence | For 808 synthetic entities the stored index selected a sentence containing the entity 77 times; index minus one matched 802 times | Index now names the containing sentence (3,108 of 3,108 correct); real JSON and workbook outputs were byte-identical apart from run metadata |
| XML comments, processing instructions, and unresolved entities crashed the batch | A `<!-- comment -->` inside a title raised `TypeError` out of `parse_xml_records`, ending the run | Non-element nodes have no element name and contribute no text; extraction failures become failed records |
| One record raising in a whole-batch detector removed that check for every record | Numerical, design, trial, template, and enriched stages processed the list in one call | Failing stages probe records individually, exclude only the records that reproduce the failure, and rerun on the rest |
| Records sharing a DOI terminated report assembly | Integration raised on a duplicate DOI key | Such records are keyed by abstract ID and fail with `duplicate_identifier` |
| Live entity extraction was strictly sequential | The extractor held its lock across every gateway call | The lock guards only cache and counter, concurrent identical requests share one call, and preprocessing uses the client's bounded concurrency |
| Batch splitting multiplied traffic against a failing gateway | Semantic and nonsense batches retried and binary-split on any `RuntimeError`, including exhausted transport failures | Transport failures fail the batch's records once; only unusable model content is retried and split |
| No separation of connect and read timeouts, no jitter, no Retry-After, no circuit | A black-holed gateway would take the full 120-second read timeout per attempt; an outage retried every request | Separate connect, read, and whole-response limits; full-jitter backoff; shared Retry-After cooldown; circuit breaker; auth failures disable the client |
| Per-record detectors slowed as the batch grew | The first 3,000-record benchmark spent 70 to 88 seconds each in numerical, design, and trial checks; an 8,192-entry segmentation cache held under 60% of the batch's 14,394 section texts, so each detector re-segmented most of them (design 83.0 s, trial 56.3 s versus 17.4 s and 3.7 s with a large cache, identical results) | Cache bound raised to 65,536 entries (about 10 MB for 6,000 records); the same 3,000-record synthetic benchmark fell from 1,925 s to 987 s with identical status and template counts |
| Checkpoint writes doubled state memory | Pickling to bytes before writing added 242 MB at the 3,000-record features checkpoint | Checkpoints stream to disk while hashing |
| Uncached trial IDs marked records failed when lookup was not requested | 23 of 519 real records were `failed` only because of `offline_cache_miss` without `--verify-trials` | With owner approval, unrequested registry lookups are no longer operational failures |
| Reports could be partially written or unreconciled | Reports were written in place with no read-back check | Staged writes, reconciliation from disk, and ordered promotion with hashes |
| No resume | Any interruption restarted all CPU work | Fingerprinted stage checkpoints |

## Changes Implemented

Gateway client (`content_integrity/validators/context_validator.py`, `thresholds.py`): typed `GatewayRequestError` categories, per-client in-flight limit with blocking backpressure, connect/read/deadline timeouts through a urllib handler that keeps proxy support and the unverified TLS context, full-jitter backoff with Retry-After, shared cooldown on 429 and 503, circuit breaker, fail-fast after authentication failure, cache hit/miss/write-failure counters, attempt and request latency percentiles, and token usage.

Pipeline (`content_integrity/pipeline.py`, `checkpoint.py`, `reconciliation.py`, `reporting.py`, `models.py`): run IDs in every log line, per-stage wall/CPU/peak-memory metrics, classified operational issues with retry counts and retryability, corpus-detector isolation, parallel preprocessing when a gateway is used, stage checkpoints with a fingerprint of code, inputs, parsed records, dictionary, options, and gateway identity, staged output with reconciliation and ordered promotion, `run_metrics.json`, `run_summary.json`, per-record `record_status`, `failures`, and `active_finding_count` in the JSON, and a `Record Status` workbook column.

Parsing and features (`xml_parser.py`, `utils.py`, `entity_extraction.py`): non-element node handling, per-record extraction isolation, memoized segmentation, correct sentence indexes, and a concurrency-safe extractor.

Tooling: `scripts/generate_load_dataset.py`, `scripts/fake_gpt_oss_gateway.py` (deterministic HTTP and HTTPS test double with seeded failure injection and concurrency quota), `scripts/benchmark_scale.py` (repeatable subprocess benchmark with OS-measured memory and CPU and cross-run consistency; it no longer copies real XML or overwrites the tracked baseline), and the manual `.github/workflows/load-test.yml`.

The output contract changed only additively: two new report files, new JSON summary fields, and one workbook column.
The existing `processing_status` values are unchanged except for the approved trial-lookup decision above.

## Tests Added

`tests/test_gateway_resilience.py` runs the real client against the test double over loopback: recovery from 429, 500, 503, timeout, reset, and malformed replies; categorized exhaustion within the attempt limit; no retry on 400 or 401 and client disablement after 401; server-observed concurrency never above the limit; Retry-After pausing other threads; circuit open, fail-fast, and probe recovery; connect versus read timeout; disk cache hits; `verify_ssl=false` against a self-signed HTTPS gateway; certificate failure as non-retryable configuration; and batch detectors that fail once on transport errors but split on invalid content.

`tests/test_pipeline_reliability.py` covers malformed inputs with a terminal status for each, corpus-detector isolation, interruption during template comparison with resume that recomputes nothing and matches a clean run byte for byte, output write failure keeping previous reports and resuming at reporting, reconciliation failure never promoting, reconciliation detecting a workbook missing a record, incompatible and corrupt checkpoints, transient gateway failures completing every record, an outage failing records as retryable with bounded requests, SIGKILL during model requests with resume that repeats no completed call, and unrequested trial lookups.

`tests/test_load_dataset_generator.py` covers reproducibility, manifest counts, and concurrent entity-request deduplication.
`tests/conftest.py` now allows loopback connections only.

## Measured Results

All runs used the final code on the development host (4 CPUs, 15 GiB RAM), one fresh process per run via `scripts/benchmark_scale.py`, with reports under `outputs/benchmarks/` (not committed).
The test double added 20 ms per request; its latencies describe the double, not GPT-OSS.

| Run | Evidence | Model | Inputs | Wall s | Records/min | Peak RSS MB | Avg cores | Completed / with findings / failed / skipped | Reconciled | Candidate pairs |
|---|---|---|---:|---:|---:|---:|---:|---|---|---:|
| real-519 | real | offline | 519 | 107 | 291 | 270 | 0.99 | 481 / 38 / 0 / 0 | yes | 2,382 |
| real-519 | real | test double | 519 | 170 | 184 | 281 | 0.91 | 481 / 38 / 0 / 0 | yes | 2,382 |
| synthetic-1000 | synthetic | test double | 1,000 | 486 | 124 | 378 | 0.93 | 877 / 123 / 0 / 0 | yes | 13,307 |
| synthetic-3000 | synthetic | test double | 3,000 | 987 | 182 | 1,034 | 0.92 | 2,618 / 382 / 0 / 0 | yes | 33,209 |
| synthetic-6000 (interrupted, resumed) | synthetic | test double | 6,000 | 1,585 + 193 | 203 | 1,220 (resume process; first process not captured) | 0.99 | 5,224 / 776 / 0 / 0 | yes | 37,713 |

| Stage seconds | Parse | Numerical | Design | Template features | Exact reuse | Entity template | Enriched reports | Write | Reconcile |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| real-519 offline | 2.1 | 8.5 | 1.8 | 21.0 | 6.4 | 32.9 | 23.2 | 3.8 | 0.2 |
| real-519 test double | 1.8 | 9.0 | 2.0 | 54.8 | 9.3 | 44.6 | 32.6 | 6.8 | 0.3 |
| synthetic-1000 | 2.7 | 27.2 | 4.6 | 134.1 | 36.7 | 175.8 | 82.5 | 8.7 | 0.4 |
| synthetic-3000 | 4.5 | 46.5 | 7.7 | 252.5 | 55.8 | 349.1 | 202.1 | 43.7 | 1.5 |

| Test-double gateway | Requests | Retries | Failed | Attempt p50 / p95 / p99 s | Request p50 / p95 / p99 s | Cache hits | Max in flight |
|---|---:|---:|---:|---|---|---:|---:|
| real-519 | 3,553 | 0 | 0 | 0.027 / 0.065 / 0.088 | 0.033 / 0.097 / 0.133 | 25 | 4 |
| synthetic-1000 | 6,419 | 0 | 0 | 0.031 / 0.079 / 0.116 | 0.042 / 0.124 / 0.178 | 22 | 4 |
| synthetic-3000 | 19,307 | 0 | 0 | 0.024 / 0.057 / 0.080 | 0.028 / 0.083 / 0.113 | 82 | 4 |

The 6,000-record run was stopped by the owner after all four checkpointed stages had completed (1,585 s from start to the last checkpoint; its gateway counts were not captured) and was resumed with the identical command: the resume restored every stage, made no gateway requests, wrote and reconciled both reports in 193 s (169 s of it writing the workbook and JSON), and removed its checkpoint.
It produced 886 prioritized template pairs and 116 families from 37,713 candidates.
Before the segmentation fixes the same benchmarks took 215 s (real-519 offline) and 1,925 s (synthetic-3000); the original profiled real run took 498.6 s.
Real-data reports from the final code were byte-identical in JSON and identical in every workbook sheet except run metadata.
Template candidate pairs grew sublinearly at the bucket caps (13,307 at 1,000 records, 33,209 at 3,000), and no run materialized all pairs.
Checkpoints took 2 to 10 seconds per run; the largest state was 27 MB at 519 records and 232 MB at 3,000.
Peak memory is reached during exact text reuse, whose per-record ten-word phrase sets measured 111 MB at 1,500 records.

Each run's JSON and workbook reconciled (status totals, identities, per-record status and active finding counts, template links).
A scan of the real runs' metrics, summaries, and captured log lines for all 519 titles and 44,482 eight-word abstract windows found no matches.
Failure injection, isolation, checkpoint, and resume behaviour is proven by the automated tests listed above (small synthetic volumes, test double over real HTTP and HTTPS).

Not measured: the second and third consecutive 6,000-record runs and the first 6,000-record process's peak memory (the series was stopped at the owner's request), the high-similarity, long-abstract, and mixed benchmarks at full volume (covered only by tests at small volume), live GPT-OSS latency, quota, and cost, and a CPU or memory limit for the deployment host.

## Remaining Risks And Recommended Configuration

| Risk | Status |
|---|---|
| Three-run 6,000-record repeatability | Not demonstrated; one complete run (interrupted and resumed) is recorded above |
| Live GPT-OSS capacity | Unknown; about 6.4 entity requests per record were measured with the double, so a 6,000-record live run needs roughly 38,000 requests, which is about 2.7 hours at 1 second per request and 4 concurrent requests before retries |
| Deployment limit | No agreed CPU or memory limit; peak memory grows with batch size and similar-template density |
| Real 6,000-record template density | Estimated at 150,000 to 300,000 candidate pairs from real growth; the synthetic workload is about 2.5 times denser than real at 519 records |
| Single-process CPU bound | Template comparison uses one core; wall time is dominated by pair comparison |
| Checkpoint trust | Checkpoints are pickled; resume only from directories the pipeline wrote |
| Resumed failures | A resume keeps retryable record failures; rerun with `--discard-checkpoint` to retry them |

Recommended operating configuration: one batch per output directory; `--llm-max-concurrency 4` unless the gateway owner approves more; default gateway timeouts (10 s connect, 120 s read, 300 s response), 3 attempts, 1 to 30 s jittered backoff, circuit after 5 consecutive failures with 60 s cooldown; `INTELLIHUB_VERIFY_SSL=false` as deployed or a CA bundle; at least 4 GiB RAM and 2 CPUs per 6,000-record batch pending a measured limit; confirm `run_summary.json` is reconciled before using reports.

## Readiness Conclusion

**Not ready** for a controlled 6,000-abstract production run.

Reliability behaviour (record isolation, classified retries, backpressure, checkpoint and resume, atomic reconciled reports) is implemented and verified by tests and by real and synthetic runs up to the volumes shown.
The remaining blockers are evidence, not known defects: three consecutive 6,000-record synthetic runs, an agreed deployment resource limit, and a staged live GPT-OSS test with approved quota and budget.
Completing the first two would support "conditionally ready"; the live test is required before "ready".

Revised estimate: 3 to 5 engineering days for the remaining Phase 2 evidence (about half a day of machine time for the synthetic series and stress profiles, plus the live test and limit agreement), excluding waiting for gateway approval.

## Deferred To Phase 3

Unknown or new tortured-phrase detection and detector-level accuracy evaluation on reviewed data remain Phase 3 work.
The nonsense-candidate detector reviews identical sentences once across records; if that shared review fails, only the first record is marked incomplete (the detector is excluded from all outputs today).
