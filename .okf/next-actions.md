---
type: Work Queue
title: Phase 2 continuation actions
description: Work required before a controlled 6,000-abstract production run can be supported.
status: stable
tags: [asco, phase2, follow-up]
generated: { by: anthropic/claude-opus-5, at: 2026-09-15T00:00:00Z }
stale_after: 2026-10-15
sources:
  - id: phase2-report
    resource: /docs/PHASE2_RELIABILITY_CAPACITY.md
    title: Remaining risks and readiness conclusion
    last_modified: 2026-09-15
---

# Pending

1. Complete three consecutive 6,000-record synthetic runs with `scripts/benchmark_scale.py --runs 3` (about 30 minutes each on the development host).
2. Agree a deployment memory and CPU limit, then confirm the 6,000-record peak stays within it.
3. With approved credentials, quota, and budget, run a staged live GPT-OSS test starting small.
4. Commit and push through the authorized workflow and confirm CI.

# Deferred Work

Phase 3: unknown tortured-phrase detection and detector-level accuracy evaluation.
