# Codex Implementation Ticket: Tortured Phrase Risk Aggregation Fix — P0

## Objective

Fix the confirmed product bug where tortured-phrase findings rejected by GPT-OSS validation still affect abstract-level risk.

This ticket is deliberately narrow.

Do not redesign the full tortured-phrase pipeline. Do not add biomedical ontologies, dictionary tiers, embeddings, cross-corpus retrieval, or new candidate-discovery methods in this task.

The goal is to correct validation-status handling, risk aggregation, exact duplicate handling, reviewer visibility, and regression coverage.

---

# Repository Context

Repository:

```text
pavan-182/content_integrity_checks
```

Read these files before modifying anything:

```text
asco_integrity/detectors/tortured_phrase.py
asco_integrity/validators/context_validator.py
asco_integrity/aggregation/risk_engine.py
asco_integrity/pipeline.py
asco_integrity/models.py
asco_integrity/reporting.py
asco_integrity/detectors/nonsense_candidate.py
tests/test_pipeline.py
```

Also inspect any additional tests that cover:

```text
ContextValidator
Finding
ValidationResult
risk aggregation
Excel or CSV reporting
tortured phrase detection
nonsense candidate detection
```

Do not rely only on the approximate line numbers in this ticket. Confirm the current implementation before editing.

---

# Confirmed Current Problems

## 1. Rejected findings still affect risk

The pipeline runs `ContextValidator.validate()` for tortured-phrase findings and sets `finding.validation_status`.

However, `_aggregate_findings()` does not use `validation_status` when computing:

```text
highest severity
detector types
total finding count
overall risk
overall content risk
```

As a result, a tortured-phrase finding explicitly rejected by GPT-OSS as legitimate usage still contributes to abstract-level risk.

---

## 2. Infrastructure failures are mapped to uncertainty

`context_validator.py` currently catches errors such as:

```text
RuntimeError
KeyError
TypeError
ValueError
```

in one block and maps them to:

```text
uncertain
```

This incorrectly treats:

```text
network failure
HTTP failure
timeout
retry exhaustion
malformed JSON
missing response fields
```

as genuine model uncertainty.

---

## 3. Unvalidated candidates affect risk

Findings from `nonsense_candidate.py` use:

```text
validation_status = "candidate"
```

These candidate findings currently affect finding counts and may increase overall risk even though they have not been validated.

---

## 4. Exact duplicate tortured-phrase findings are not consolidated

The tortured-phrase detector may emit duplicate findings for the same rule and the same actual text occurrence.

There is currently no exact duplicate consolidation step before aggregation.

This ticket must remove exact duplicates only.

Do not attempt semantic, nested, or cross-rule overlap consolidation in this task.

---

# Behavioural Decisions for This Ticket

Implement the following rules exactly.

## Risk eligibility

Risk-eligible validation statuses:

```text
confirmed
blank validation status for deterministic tortured-phrase findings when validation is disabled
```

Risk-ineligible validation statuses:

```text
rejected
uncertain
validation_failed
candidate
```

A blank validation status must remain risk-eligible for deterministic tortured-phrase findings in this P0 ticket to preserve existing behaviour when validation is disabled.

Do not convert stored blank values to another status in this task.

For reviewer summary counts only, treat a blank validation status as:

```text
not_validated
```

---

## Risk exclusion must be complete

Risk-ineligible findings must not affect any of the following:

```text
highest_severity
detector_types
total_finding_count
overall_risk
overall_content_risk
any derived review-priority field based on those values
```

Do not fix only the finding count while leaving severity or detector-type aggregation unchanged.

---

## Reviewer visibility

All findings must remain visible in detailed output regardless of risk eligibility.

This includes:

```text
confirmed
rejected
uncertain
validation_failed
candidate
not_validated
```

Excluding a finding from risk must not delete it from:

```text
CSV output
Excel output
JSON output
detailed findings output
validation audit information
```

---

## Exact duplicate definition

Only remove findings that represent the same actual match occurrence.

A duplicate must have the same:

```text
record ID
section or field
rule ID
normalized matched text
match start offset
match end offset
```

Use existing character offsets if they already exist.

If offsets are currently available in the regex loop but not stored in the `Finding` model, prefer deduplicating before constructing the final `Finding` objects.

Do not add large schema changes solely for deduplication.

If no offsets or equivalent match-position information exist, use the most precise occurrence identifier available and document the limitation.

Do not collapse two separate occurrences of the same phrase in different positions.

Do not claim this is overlapping-finding consolidation. It is exact duplicate consolidation only.

---

# Required Changes

## 1. Add `validation_failed` as a distinct status

File:

```text
asco_integrity/validators/context_validator.py
```

Update exception handling so that genuine model uncertainty remains different from technical or parsing failure.

Target behaviour:

```python
try:
    raw = self.client.complete(...)
    parsed = _parse_validator_payload(raw)

    status = normalize_whitespace(str(parsed["status"])).lower()
    reason = normalize_whitespace(str(parsed["reason"]))

    if status not in {"confirmed", "rejected", "uncertain"}:
        raise ValueError("validator payload contains unsupported status")

    if not reason:
        raise ValueError("validator payload missing reason")

except RuntimeError as exc:
    status = "validation_failed"
    reason = "Validator request failed because of an infrastructure error."
    logger.exception(
        "Tortured phrase validator infrastructure failure: %s",
        exc,
    )

except (KeyError, TypeError, ValueError) as exc:
    status = "validation_failed"
    reason = "Validator response could not be parsed."
    logger.exception(
        "Tortured phrase validator response parsing failure: %s",
        exc,
    )
```

Adapt this to the existing logger and code style.

Requirements:

```text
RuntimeError must map to validation_failed.
Malformed or incomplete validator output must map to validation_failed.
A valid model response with status uncertain must remain uncertain.
Do not expose credentials, request headers, secrets, or full abstract text in logs.
Preserve existing validation metadata and raw-response handling where already supported.
Do not silently discard the original exception.
```

If the client raises additional specific timeout, transport, or HTTP exception types, inspect them and map them consistently to `validation_failed`.

Do not broadly catch `Exception` unless the current repository conventions require it. If a broad catch is necessary, log it and map it to `validation_failed`, not `uncertain`.

---

## 2. Document supported validation statuses

File:

```text
asco_integrity/models.py
```

No major schema redesign is required.

Add documentation near `Finding.validation_status` and `ValidationResult.status`.

Supported statuses:

```text
""
confirmed
rejected
uncertain
validation_failed
candidate
```

For reporting purposes, blank status may be displayed or counted as:

```text
not_validated
```

Do not add dictionary quality, source provenance, biomedical context, or other Phase 2 fields.

---

## 3. Add explicit risk-eligibility logic

Prefer a small private helper close to the aggregation logic in:

```text
asco_integrity/pipeline.py
```

Example:

```python
RISK_INELIGIBLE_VALIDATION_STATUSES = {
    "rejected",
    "uncertain",
    "validation_failed",
    "candidate",
}


def _is_risk_eligible_finding(finding: Finding) -> bool:
    status = normalize_whitespace(
        finding.validation_status or ""
    ).lower()

    return status not in RISK_INELIGIBLE_VALIDATION_STATUSES
```

Keep `_risk_from_signals()` pure if it currently accepts only summary values.

Do not change `_risk_from_signals()` to accept full `Finding` objects unless the existing architecture clearly requires that.

If risk eligibility already belongs naturally in `risk_engine.py`, it may be placed there, but use one canonical helper only. Do not create duplicate eligibility logic in multiple modules.

---

## 4. Apply risk filtering consistently during aggregation

File:

```text
asco_integrity/pipeline.py
```

Inside `_aggregate_findings()` or the equivalent aggregation path, create clearly named collections.

Example:

```python
tortured_findings_all = [
    finding
    for finding in record_findings
    if finding.detector_type == "tortured_phrase"
]

tortured_findings_risk_eligible = [
    finding
    for finding in tortured_findings_all
    if _is_risk_eligible_finding(finding)
]

risk_eligible_non_llm_findings = [
    finding
    for finding in record_findings
    if finding.detector_type != "llm_response_trace"
    and _is_risk_eligible_finding(finding)
]
```

Use the unfiltered collection for reporting and status counts.

Use only the risk-eligible collection for:

```text
highest severity
detector type count
finding count used by risk
overall risk
overall content risk
derived review priority
```

Leave these unrelated pathways unchanged unless tests prove they share the same bug:

```text
template strongest pair
template severity
LLM response trace priority
template clustering
LLM response trace aggregation
```

Do not modify template or LLM-trace logic as part of this ticket.

---

## 5. Keep candidate findings out of risk

Findings with:

```text
validation_status = "candidate"
```

must not affect abstract-level risk.

They must remain visible to reviewers.

Candidate exclusion must apply to:

```text
highest severity
detector types
risk-driving finding count
overall risk
overall content risk
review priority derived from risk
```

Add a separate candidate count to the abstract-level output.

---

## 6. Add exact duplicate consolidation

File:

```text
asco_integrity/detectors/tortured_phrase.py
```

Add a small exact duplicate consolidation step before returning findings.

Do not create a large new consolidation framework.

Preferred logic:

```python
def _deduplicate_exact_matches(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[object, ...]] = set()
    deduplicated: list[Finding] = []

    for finding in findings:
        key = (
            finding.record_id,
            finding.section_or_field,
            finding.rule_id,
            normalize_whitespace(finding.matched_text).lower(),
            finding.start_offset,
            finding.end_offset,
        )

        if key in seen:
            continue

        seen.add(key)
        deduplicated.append(finding)

    return deduplicated
```

Adapt field names to the actual model.

If `Finding` has no offset fields:

1. Inspect where `re.Match.start()` and `re.Match.end()` are available.
2. Deduplicate using those positions before final object construction.
3. Avoid adding broad schema changes for this task.
4. Add a comment explaining that nested or overlapping findings from different rules remain intentionally unresolved.

Required behaviour:

```text
same rule + same occurrence → one finding
same rule + same phrase at two different offsets → two findings
different rules matching overlapping text → remain separate in P0
```

---

## 7. Add abstract-level status counts

Update the per-record summary and reporting output.

Use the complete unfiltered tortured-phrase collection.

Add these fields:

```text
tortured_confirmed_count
tortured_rejected_count
tortured_uncertain_count
tortured_validation_failed_count
tortured_candidate_count
tortured_not_validated_count
```

Suggested implementation:

```python
def _normalized_validation_status(finding: Finding) -> str:
    status = normalize_whitespace(
        finding.validation_status or ""
    ).lower()

    return status or "not_validated"
```

Then count each status from:

```text
tortured_findings_all
```

Do not use the risk-filtered collection for reviewer counts.

Preserve existing fields such as:

```text
tortured_phrase_flag
tortured_phrase_count
validation_status
```

Do not rename or remove existing columns.

Append the new fields in the documented tortured-phrase section or at the end of the existing output schema.

Update:

```text
reporting column definitions
schema documentation
snapshot tests
CSV tests
Excel tests
JSON tests
```

where applicable.

Call out any downstream schema impact in the final implementation summary.

---

## 8. Preserve detailed validation evidence

Do not remove or overwrite existing fields that show:

```text
matched phrase
expected term
evidence snippet
section or field
validator status
validator reason
rule ID
severity
confidence
```

Rejected, uncertain, failed, candidate, and unvalidated findings must still appear in the detailed findings output.

Only their risk contribution changes.

---

# Tests to Add

Before implementing the fix, add at least one failing regression test that reproduces the current bug.

Then implement the changes and make the tests pass.

## 1. Rejected finding does not affect risk

Create a tortured-phrase finding with:

```text
validation_status = "rejected"
```

Assert that it does not affect:

```text
highest severity
detector type count
risk-driving finding count
overall risk
overall content risk
```

Also assert:

```text
tortured_rejected_count == 1
```

and confirm the finding remains present in detailed output.

---

## 2. Validation failure does not affect risk

Use a stub validator client whose `complete()` method raises `RuntimeError`.

Assert:

```text
validation status == validation_failed
tortured_validation_failed_count == 1
the finding remains visible
the finding does not affect overall risk
the finding does not affect overall content risk
```

---

## 3. Malformed validator output maps to `validation_failed`

Use a stub client that returns:

```text
invalid JSON
missing status
unsupported status
missing reason
```

At minimum, cover one malformed JSON case and one structurally invalid payload case.

Assert:

```text
status == validation_failed
reason indicates a parsing failure
status is not uncertain
```

---

## 4. Genuine model uncertainty remains `uncertain`

Return a valid validator payload containing:

```json
{
  "status": "uncertain",
  "reason": "The phrase may be legitimate in this context."
}
```

Assert:

```text
status == uncertain
tortured_uncertain_count == 1
the finding remains visible
the finding does not affect risk
```

---

## 5. Confirmed finding still affects risk

Create a tortured-phrase finding with:

```text
validation_status = "confirmed"
```

Assert that it continues to affect risk according to existing behaviour.

This is a regression guard.

Also assert:

```text
tortured_confirmed_count == 1
```

---

## 6. Blank deterministic validation status preserves current behaviour

Create a deterministic tortured-phrase finding with:

```text
validation_status = ""
```

Assert:

```text
it remains risk-eligible
tortured_not_validated_count == 1
it remains visible in detailed output
```

This preserves current behaviour when validation is disabled.

---

## 7. Candidate findings do not affect risk

Create one or more findings with:

```text
validation_status = "candidate"
```

Assert that they do not affect:

```text
highest severity
detector types
risk-driving finding count
overall risk
overall content risk
```

Also assert:

```text
tortured_candidate_count reflects the findings where applicable
candidate findings remain visible in detailed output
```

If candidates use a separate detector type, add the count under the appropriate existing summary structure while preserving the product rule that candidates are visible but risk-ineligible.

---

## 8. Exact duplicate match is removed

Generate two findings representing the same:

```text
record
field
rule
normalized text
start offset
end offset
```

Assert that only one is returned.

---

## 9. Repeated phrase at different offsets is preserved

Generate two matches with:

```text
same record
same field
same rule
same matched text
different offsets
```

Assert that both remain.

---

## 10. Different overlapping rules remain separate

Create two findings from different rule IDs that overlap in text.

Assert that both remain in P0.

Document that semantic or cross-rule consolidation is deferred.

---

## 11. Existing risk paths remain unchanged

Add regression checks proving that this ticket does not alter:

```text
LLM response trace risk
template detection risk
template pair strength
existing confirmed tortured phrase behaviour
```

---

# Full Validation Requirements

Run the full repository test suite.

Also run any configured:

```text
formatter
linter
type checker
schema tests
report snapshot tests
```

Do not run only `tests/test_pipeline.py`.

All newly added tests and all existing tests must pass.

---

# Explicit Non-Goals

Do not implement any of the following in this ticket:

```text
dictionary quality tiers
dictionary provenance scoring
retrieved paper evidence scoring
local AND/NOT context redesign
biomedical NER
oncology ontology integration
drug or gene entity resolver
masked language model discovery
embedding similarity
PubMed retrieval
cross-corpus comparison
semantic duplicate consolidation
nested phrase consolidation
unknown phrase discovery redesign
gene-and-drug prefilter changes
new CLI flags
large Finding schema redesign
automatic rejection decisions
```

These belong to later phases.

---

# Backward Compatibility Requirements

Preserve:

```text
existing CLI behaviour
existing detector interfaces
existing report columns
existing JSON fields
existing confirmed-finding risk behaviour
existing template behaviour
existing LLM trace behaviour
```

New report fields may be added, but existing fields must not be renamed or removed.

Do not reorder existing report columns unnecessarily.

Place new columns either:

```text
next to the current tortured-phrase fields
```

or:

```text
at the end of the output schema
```

Update documentation and snapshot expectations accordingly.

---

# Definition of Done

This ticket is complete only when all of the following are true:

1. A rejected tortured-phrase finding does not affect abstract-level risk.

2. A validator infrastructure failure is labeled:

```text
validation_failed
```

and does not affect risk.

3. A malformed validator response is labeled:

```text
validation_failed
```

and does not affect risk.

4. A genuine validator response of:

```text
uncertain
```

remains distinct from technical failure and does not affect risk.

5. A confirmed tortured-phrase finding continues to affect risk according to existing behaviour.

6. A deterministic tortured-phrase finding with blank validation status remains risk-eligible when validation is disabled.

7. Candidate findings do not affect severity, detector count, finding count, overall risk, overall content risk, or risk-derived priority.

8. Exact duplicate findings for the same match occurrence collapse to one.

9. Repeated occurrences at different offsets remain separate.

10. Findings from different overlapping rules remain separate in this P0 implementation.

11. Abstract-level reporting includes:

```text
tortured_confirmed_count
tortured_rejected_count
tortured_uncertain_count
tortured_validation_failed_count
tortured_candidate_count
tortured_not_validated_count
```

12. Every finding remains visible in detailed reviewer output regardless of risk eligibility.

13. Existing output fields are preserved.

14. Existing template and LLM response trace behaviour remains unchanged.

15. The full repository test suite passes.

16. Any configured formatter, linter, type checker, and report-schema tests pass.

---

# Required Final Response from Codex

After implementation, provide:

## Files changed

List every modified file.

## Behaviour changed

Explain:

```text
which statuses now contribute to risk
which statuses do not contribute to risk
how blank validation status behaves
how candidate findings behave
how exact duplicate detection works
```

## Tests added

List each new regression or integration test.

## Validation performed

Report the exact commands run and their results.

## Compatibility notes

State whether:

```text
report columns were added
schema snapshots changed
downstream consumers may need updates
```

## Deferred work

Explicitly confirm that the following remain deferred:

```text
dictionary tiers
biomedical context validation
local-context rule redesign
unknown candidate discovery redesign
semantic overlap consolidation
embeddings
cross-corpus retrieval
```
