---
type: Verification Record
title: Phase 2 local verification
description: Checks and benchmark evidence for Phase 2 on the development host.
status: stable
tags: [asco, phase2, verification]
generated: { by: anthropic/claude-opus-5, at: 2026-09-15T00:00:00Z }
sources:
  - id: phase2-report
    resource: /docs/PHASE2_RELIABILITY_CAPACITY.md
    title: Measured results and readiness conclusion
    last_modified: 2026-09-15
---

# Environment

Ubuntu 24.04 on Linux x86-64 with 4 CPUs and 15 GiB RAM, Python 3.12.3 in an isolated virtual environment, Node 22.22.2.

# Results

* `pip check`, compilation, Ruff, and `git diff --check` passed.
* `python -m pytest tests/ -q`: 350 passed, 122 subtests passed.
* `python scripts/run_eval.py`: all template precision and recall pairs 1.000, no reconciliation errors.
* Frontend `npm ci`, 12 tests, and production build passed.
* Benchmarks on final code: real 519 offline and test double, synthetic 1,000 and 3,000 with the test double; all succeeded and reconciled.
* One 6,000-record synthetic run completed after an interruption and resume: 6,000 accounted for, 0 failed, reconciled; runs two and three were not executed after the user stopped the series.

# Interpretation

Synthetic and test-double results are engineering evidence only, not detector accuracy or live gateway capacity.
