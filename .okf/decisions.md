---
type: Decision Record
title: Phase 1 stabilization decisions
description: Small, evidence-backed decisions that make the content-integrity pipeline deterministic and safe to resume.
status: stable
tags: [asco, phase1, architecture]
generated: { by: openai/codex-gpt5, at: 2026-09-15T00:00:00Z }
sources:
  - id: phase1-handoff
    resource: /docs/PHASE1_STABILIZATION.md
    title: Phase 1 Stabilization handoff
    author: openai/codex-gpt5
    last_modified: 2026-09-15
  - id: ci-workflow
    resource: /.github/workflows/ci.yml
    title: Continuous integration workflow
    last_modified: 2026-09-15
---

# Decisions

## Run Isolation

Entity extraction receives an explicit `EntityExtractor` owned by the pipeline run.

Inference counts, response caches, locks, and client identity therefore cannot leak between sequential or concurrent runs.

The old `set_entity_llm_client` process-global mutation was removed.

## Cache Identity

Cache keys include every response-affecting request value plus endpoint and credential scope.

Corrupt or incompatible envelopes are treated as cache misses.

## Deterministic Tests

Tests clear gateway environment state, block live sockets, use explicit fake endpoints, and use checked-in or temporary fixtures.

## Migration Boundary

PubMedBERT evaluation code was removed because reference searches showed no supported runtime path.

Production requirements contain only the GPT-OSS-era runtime dependencies.

## Data Hygiene

Tracked generated JSON artifacts were inspected but not deleted or rewritten because provenance and licensing require human approval.

New tests use synthetic minimal data.

## Phase 2 Record Status

Every input record ends as completed, completed_with_findings, failed, or skipped, and reports are promoted only after they reconcile from disk.

## Phase 2 Registry Lookups

With owner approval, an uncached trial identifier is not an operational failure when `--verify-trials` was not requested.

## Phase 2 Resume

Stage checkpoints resume only with identical code, inputs, dictionary, options, and gateway identity; gateway responses are reused through the content-addressed cache.
