---
type: Project State
title: ASCO pipeline Phase 2 reliability state
description: Phase 2 hardening is implemented and verified locally; 6,000-record synthetic validation and live gateway measurement are incomplete.
status: in_progress
tags: [asco, phase2, reliability, capacity]
generated: { by: anthropic/claude-opus-5, at: 2026-09-15T00:00:00Z }
stale_after: 2026-10-15
sources:
  - id: phase2-report
    resource: /docs/PHASE2_RELIABILITY_CAPACITY.md
    title: Phase 2 reliability and capacity report
    last_modified: 2026-09-15
  - id: operations
    resource: /docs/OPERATIONS.md
    title: Batch operations runbook
    last_modified: 2026-09-15
---

# Current State

Phase 2 builds on the uncommitted Phase 1 worktree at revision `f5d93b14a72feb4e77ff64a42c831014ec689d0c`.

No commit, push, pull request, live gateway call, tracked-data deletion, or dataset publication was performed.

# Completed Areas

* Gateway client: classified failures, bounded in-flight requests, connect/read/deadline timeouts, jittered backoff with Retry-After, shared rate-limit cooldown, circuit breaker, fail-fast authentication, telemetry.
* Pipeline: run IDs, per-stage metrics, per-record terminal status and failure details, corpus-detector isolation, stage checkpoints with compatibility fingerprints, staged reports reconciled from disk before promotion.
* Measured bottlenecks fixed: sentence segmentation (real 519 batch 498 s to 107 s) and segmentation cache thrash (3,000 synthetic records 1,925 s to 987 s), with unchanged real-data reports.
* Parser crash on XML comments, batch-ending duplicate DOIs, and sequential live entity extraction fixed.
* Reproducible synthetic dataset generator, deterministic GPT-OSS test double, repeatable benchmark harness, and manual load workflow.

# Scope Boundary

Detector thresholds, detection methods, risk scores, and the frontend design were not changed.
