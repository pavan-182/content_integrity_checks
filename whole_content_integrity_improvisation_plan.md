At this point I would stop treating the repository as a collection of detectors and start treating it as **one content-integrity product pipeline whose final contract is an Excel workbook + JSON report**.

The strengthening work should happen in two dimensions:

* **Solution logic:** Is each detector finding the right thing, with correct false-positive controls and validation?
* **Engineering:** Is every detector reliable, testable, scalable, observable, and producing consistent output?

The target should be:

```text
XML / input
    ↓
Parse + validate input
    ↓
Shared preprocessing
    ↓
Detectors
    ↓
Candidate findings
    ↓
Validation / verification
    ↓
Final normalized findings
    ↓
ONE decision / aggregation layer
    ↓
Canonical report model
      ↙          ↘
   JSON         Excel
```

Not:

```text
Detector
 ├─ custom logic
 ├─ custom severity
 ├─ custom status
 ├─ custom Excel transformation
 └─ custom JSON transformation
```

The current reporting layer already has a very broad shared schema, including findings, pair records, abstract summaries, validation states, template metrics and editor fields.  The next step is to make that schema the **result of a clean internal data model**, rather than having reporting repair inconsistent detector outputs.

---

# 1. Template Detection

This is already the strongest pipeline. **Strengthen it; don't redesign it.**

| Area                  | What to fix                                                                                                                                                                                       |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Solution logic        | Preserve two distinct signals: **direct text reuse** and **entity-normalized/template reuse**. Do not merge them into one similarity score.                                                       |
| Candidate retrieval   | Remove the remaining all-pairs rare-title comparison. `_rare_title_candidate_pairs()` currently considers combinations across records; replace it with an inverted rare-token index.              |
| Candidate blocking    | Put maximum bucket/frequency limits on all approximate blocking strategies, not only n-gram and shape buckets. Current common candidate generation already uses several good blocking approaches. |
| Entity extraction     | **Extract entities once per abstract.** Do not repeatedly run PubMedBERT for abstract → sections → sentence windows → two-sentence windows.                                                       |
| Entity representation | Store entity spans + types once, and derive abstract/section/sentence masks from those spans.                                                                                                     |
| PubMedBERT            | Cache model output per record. Current implementation runs a CPU HF pipeline when PubMedBERT is enabled.                                                                                          |
| Thresholds            | Keep current thresholds for V1, but label them as configurable/calibrated parameters and re-evaluate them on real ASCO-labelled pairs.                                                            |
| Related-study logic   | Keep shared trial/authors/affiliations/declared-related-study context. It should modify review priority, not delete the raw match.                                                                |
| Pair representation   | Internally store **one canonical A–B pair**. Generate A→B and B→A rows only when building editor-facing output.                                                                                   |
| Scalability           | Precompute normalized text, skeletons, shingles, rare tokens and entity masks once.                                                                                                               |
| Testing               | Candidate retrieval recall test + classification test + related-study false-positive tests + 6k performance benchmark.                                                                            |

### Template final finding

Internally:

```text
pair_id
record_a
record_b

direct_reuse:
    triggered
    match_type
    shared_text_coverage
    matched_sentences

template_reuse:
    triggered
    masked_similarity
    original_similarity
    section_similarity
    substitutions

relationship_context

classification
severity
review_priority
evidence
```

Then Excel can show directional A→B/B→A records without duplicating the actual computation.

---

# 2. LLM Response Trace Pipeline

The core logic is good. The big fix is **state management + scalability**.

Current semantic detection already batches records with a maximum records-per-batch and validates the returned JSON carefully, but those batches are executed sequentially.

| Area               | What to fix                                                                                                               |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| Known patterns     | Keep deterministic catalogue matching as the strongest signal.                                                            |
| Semantic discovery | Keep it optional and candidate-producing. It should discover variants/new patterns, not independently declare misconduct. |
| Source grounding   | Continue requiring model evidence to map back to an exact submitted-text span.                                            |
| Validation         | Make validation state authoritative.                                                                                      |
| Rejected           | Must not remain an active LLM trace.                                                                                      |
| Uncertain          | Keep for editor review, but do not treat as confirmed.                                                                    |
| Supporting-only    | Do not allow it to independently create High priority.                                                                    |
| LLM calls          | Use bounded concurrency instead of serial execution.                                                                      |
| Batching           | Continue token-budget packing; configure max batch size centrally.                                                        |
| Failures           | Retry → smaller batch → individual record → `validation_failed`. Never silently drop.                                     |
| Metrics            | Record batch count, calls, retries, failed records, model ID and prompt version.                                          |

The key state transition should be:

```text
detected candidate
      ↓
validation
      ↓
 ┌────────────┬────────────┬───────────┐
 confirmed    uncertain     rejected
     ↓            ↓             ↓
 active      editor review    inactive
```

That state should be the same in Python, JSON and Excel.

---

# 3. Known Tortured Phrase Pipeline

This should remain predominantly deterministic.

| Area                    | What to fix                                                                                                                                                 |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rule engine             | Keep PPS/fingerprint-based matching.                                                                                                                        |
| Proximity logic         | Implement proximity operators correctly instead of merely stripping `~N` semantics.                                                                         |
| Rule indexing           | Compile/index dictionary once at startup.                                                                                                                   |
| Rule provenance         | Every result should contain dictionary version + rule ID + source.                                                                                          |
| Confidence              | Stop treating constants like 0.87/0.98 as if they are calibrated probabilities. Use categorical `rule_strength` or clearly label them heuristic confidence. |
| Context validation      | Decide operationally whether validation is mandatory for ambiguous rules.                                                                                   |
| Exact evidence          | Always store the exact phrase and surrounding source text.                                                                                                  |
| False-positive controls | Add known legitimate scientific usages to regression tests.                                                                                                 |
| Dictionary updates      | Version fingerprint dictionaries; never silently replace them.                                                                                              |

I would make the output explicit:

```text
match_type = known_tortured_phrase

rule_id
fingerprint
expected_term
matched_text

dictionary_version
rule_strength

validation_status
validation_reason

review_priority
```

---

# 4. Novel Tortured / Nonsense Candidate Pipeline

This should remain a **candidate-discovery pipeline**, separate from known fingerprints.

The current candidate logic is intentionally narrow. Strengthen candidate retrieval before strengthening the LLM.

| Area                | What to fix                                                                                                                                 |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Candidate retrieval | Broaden beyond sentences requiring both gene-like and drug-like signals.                                                                    |
| Candidate routes    | Add terminology anomaly, unusual scientific noun phrase, biomedical substitution, known-fingerprint-neighbour and syntactic anomaly routes. |
| LLM                 | Use the LLM only after deterministic prefiltering.                                                                                          |
| Batch execution     | Review multiple candidate sentences per LLM request rather than one request per sentence.                                                   |
| Evidence            | Require exact returned phrase to exist in the source sentence.                                                                              |
| Known fingerprints  | If a novel candidate is actually a known fingerprint, route it to known tortured phrases instead.                                           |
| Status              | Keep all novel findings as `candidate` until validated.                                                                                     |
| Risk                | Candidate-only findings must not independently alter overall content risk.                                                                  |
| Learning loop       | Editor-confirmed novel phrases should be exportable as candidates for future dictionary updates, not automatically inserted.                |

Architecture:

```text
Abstract
   ↓
cheap candidate generators
   ↓
candidate sentence pool
   ↓
deduplicate / rank
   ↓
batched GPT review
   ↓
source-span verification
   ↓
candidate finding
   ↓
editor / validation
```

---

# 5. Numerical Contradiction Pipeline

The existing sentence-local approach is a sensible V1.

I would keep deterministic arithmetic and extend the **claim representation**, rather than using an LLM to judge numbers.

| Area             | What to fix                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------ |
| Current rules    | Keep percentage/count mismatch, numerator > denominator, impossible %, reversed intervals, median/range etc. |
| Cross-sentence   | Add cross-sentence population and outcome comparisons.                                                       |
| Cross-section    | Compare Methods population claims with Results population claims.                                            |
| Claim extraction | Introduce a small structured `NumericClaim` model.                                                           |
| Units            | Normalize `%`, counts, months, years, ratios where possible.                                                 |
| Context          | Associate number with population/outcome rather than matching arbitrary nearby numbers.                      |
| Confidence       | Base confidence on claim linkage strength, not arbitrary numeric constants.                                  |
| LLM              | Do not make LLM the primary arithmetic judge. Optional only for ambiguous contextual linkage later.          |

Example:

```text
Methods:
"120 patients were enrolled."

Results:
"143 patients were evaluable."
```

This should not automatically be a contradiction.

You need:

```text
claim 1:
population = enrolled
count = 120

claim 2:
population = evaluable
count = 143
```

Then logic decides whether those claims are actually incompatible.

That reduces false positives significantly.

---

# 6. Study Design Contradiction Pipeline

This one needs a **logic-state fix before further feature expansion**.

The current problem is essentially:

```text
deterministic contradiction detected
        ↓
check_triggered = True
        ↓
LLM says rejected
        ↓
finding still exists as triggered
```

The detection and validation states need separation.

Target:

```text
candidate contradiction
      ↓
contextual rules
      ↓
possible contradiction
      ↓
optional validation
      ↓
confirmed / uncertain / rejected
```

| Area                 | What to fix                                                                                         |
| -------------------- | --------------------------------------------------------------------------------------------------- |
| Detection            | Continue extracting explicit study-design descriptors.                                              |
| Contradiction matrix | Keep deterministic incompatible-design rules.                                                       |
| Suppression          | Keep parent study, secondary analysis, extension study, retrospective analysis etc.                 |
| Validation gating    | **Rejected must deactivate the finding.**                                                           |
| Uncertain            | Retain for editor review only.                                                                      |
| LLM role             | Validator, not primary detector.                                                                    |
| Evidence             | Keep both design claims and source sections.                                                        |
| State                | Introduce `candidate → confirmed/uncertain/rejected`.                                               |
| Tests                | Every contradiction rule should have positive + legitimate coexistence + rejected-validation cases. |

This is P0.

---

# 7. Unverifiable Clinical Trial Pipeline

The conceptual distinction here is already good:

```text
trial ID missing
≠
registry unavailable
≠
unsupported registry
≠
invalid ID
```

Preserve that.

| Area                 | What to fix                                                                                            |
| -------------------- | ------------------------------------------------------------------------------------------------------ |
| Parsing              | Continue separating registry formats.                                                                  |
| Verification state   | Use explicit states: verified / not_found / lookup_failed / unsupported / invalid_format / missing_id. |
| Risk                 | `lookup_failed` must never mean `not_found`.                                                           |
| Unsupported registry | Manual verification, not a suspicious finding.                                                         |
| Cache                | Add persistent registry cache instead of only run-time caching.                                        |
| Network              | Add bounded parallel requests.                                                                         |
| Rate limits          | Central retry/backoff/rate limiter.                                                                    |
| Provenance           | Record registry, lookup timestamp and source.                                                          |
| Offline mode         | Make offline/online status explicit in run metadata.                                                   |
| Output               | Separate `verification_status` from `review_priority`.                                                 |

A trial result should look like:

```text
trial_id
registry
verification_status
verification_source
lookup_timestamp
lookup_error

review_required
review_reason
```

Not just:

```text
trial_flag = true
```

---

# 8. Shared Preprocessing — create this once

This is one of the most important repository-level fixes.

Currently detectors independently derive representations.

Create something conceptually like:

```python
ProcessedRecord
```

containing:

```text
record_id

original_text
normalized_text

sections
sentences

tokens

entities
entity_spans

masked_text
masked_sections
masked_sentences

trial_ids

metadata
parse_status
```

Then:

```text
              ProcessedRecord
              /     |      \
             /      |       \
        template   LLM     numerical
        detector   trace    contradiction
```

Every detector should consume shared preprocessing rather than rediscovering the same information.

This fixes:

* duplicate CPU work,
* repeated PubMedBERT inference,
* inconsistent text normalization,
* inconsistent sentence splitting,
* debugging difficulty,
* reproducibility.

---

# 9. Standardize the detector interface

Every detector should ultimately behave like:

```python
detect(context) -> list[Finding]
```

or for pair-based detectors:

```python
detect_pairs(context) -> list[PairFinding]
```

No detector should know anything about Excel.

No detector should know anything about frontend JSON.

No detector should calculate overall submission risk.

---

# 10. Fix the shared Finding model

Your current generic `Finding` already centralizes many fields, which is good, but states are mostly strings and `confidence` can be either float or string.

Strengthen it.

Conceptually:

```text
Finding

identity
---------
finding_id
record_id
detector_type
check_type

evidence
--------
matched_text
evidence_snippet
section
source_span

assessment
----------
severity
validation_status
review_priority

provenance
----------
rule_id
detector_version
model_id
prompt_version

editor
------
editor_label
editor_notes
```

Then specialized information goes into a typed `details` structure.

For example:

```json
"details": {
  "reported_percentage": 54.2,
  "expected_percentage": 47.8
}
```

instead of adding dozens of template-specific columns to the core finding object.

---

# 11. Separate four concepts that are currently too easy to mix

This should become an explicit architectural rule:

```text
DETECTION
Did the algorithm find something?

VALIDATION
Did additional evidence confirm it?

REVIEW PRIORITY
Should an editor inspect it?

OVERALL ABSTRACT DECISION
What is the combined content-integrity priority?
```

For example:

```text
Template match detected = yes

Relationship context = same registered trial

Finding valid = yes

Review priority = Low

Overall abstract priority = Low
```

Detection does **not** automatically mean High risk.

---

# 12. One authoritative aggregation engine

There must be exactly **one** place that calculates:

```text
overall_content_risk
review_required
review_reason
```

The frontend, workbook writer and JSON writer must not calculate those values again.

The pipeline already has central aggregation concepts.

Strengthen that and remove parallel business logic from reporting.

Target:

```text
findings
   +
pair findings
   +
validation states
        ↓
DecisionEngine
        ↓
AbstractResult
```

Then:

```text
AbstractResult
   ├── JSON writer
   └── Excel writer
```

---

# 13. Canonical JSON model

I would make the JSON the machine-facing complete representation.

Something close to:

```json
{
  "schema_version": "2.0",
  "run": {
    "run_id": "...",
    "timestamp": "...",
    "git_sha": "...",
    "input_checksum": "...",
    "detector_versions": {},
    "model_versions": {},
    "configuration": {}
  },

  "summary": {
    "records_processed": 6000,
    "records_requiring_review": 0
  },

  "abstracts": [
    {
      "record_id": "A001",
      "overall_content_risk": "Medium",
      "review_required": true,
      "review_reason": "...",

      "checks": {
        "llm_response_trace": {},
        "tortured_phrase": {},
        "numerical_contradiction": {},
        "design_contradiction": {},
        "unverifiable_trial": {},
        "templating": {}
      },

      "finding_ids": [],
      "template_pair_ids": []
    }
  ],

  "findings": [],

  "template_pairs": [],

  "template_families": [],

  "operational_issues": []
}
```

This JSON should retain **all evidence**.

Excel can simplify it for human reviewers.

---

# 14. Excel should be an editor view, not another data model

Your current reporting code already contains a huge set of workbook columns.

I would simplify the final workbook into these core sheets:

| Sheet                  | Purpose                                                   |
| ---------------------- | --------------------------------------------------------- |
| **Dashboard**          | counts, review workload, detector summary                 |
| **Abstracts**          | one row per submitted abstract                            |
| **Findings**           | one row per editor-visible finding                        |
| **Template_Pairs**     | A→B and B→A pair evidence                                 |
| **Operational_Issues** | parse failures, model failures, registry failures         |
| **Run_Metadata**       | versions, thresholds, config, models, dictionary versions |

`Abstracts` becomes the main review queue.

Example:

```text
Record ID
Title

Overall Content Risk
Review Required
Review Reason

LLM Trace
Tortured Phrase
Numerical Contradiction
Design Contradiction
Trial Verification
Template Match

Finding Count
Highest Severity
```

The **Findings** sheet explains why.

The **Template_Pairs** sheet handles pair-specific evidence.

Do not try to put every detail into the Abstracts sheet.

---

# 15. Add operational failures as first-class data

Very important.

Right now:

```text
detector found nothing
```

and:

```text
detector couldn't run
```

must never look identical.

Create:

```text
OperationalIssue

component
record_id
status
error_type
message
retry_count
recoverable
```

Example:

```text
PUBMEDBERT_MODEL_LOAD_FAILED
BEDROCK_TIMEOUT
TRIAL_REGISTRY_RATE_LIMITED
XML_PARSE_FAILED
LLM_VALIDATION_FAILED
```

These should appear in both JSON and an Excel `Operational_Issues` sheet.

---

# 16. Repository structure I would move toward

Not a rewrite. Refactor gradually toward:

```text
content_integrity/

    ingestion/
        xml_parser.py

    preprocessing/
        text.py
        entities.py
        representations.py

    detectors/
        llm_trace/
        tortured_phrase/
        nonsense/
        template/
        numerical/
        study_design/
        trials/

    validation/
        llm_trace.py
        tortured_phrase.py
        study_design.py

    models/
        record.py
        finding.py
        pair.py
        result.py
        enums.py

    decision/
        eligibility.py
        aggregation.py

    orchestration/
        pipeline.py
        executor.py

    reporting/
        json_report.py
        excel_report.py
        schemas.py

    integrations/
        bedrock.py
        registries.py

    observability/
        run_metrics.py
        operational_issue.py
```

Don't perform this directory restructure first.

**Fix behaviour first, then move modules.**

---

# 17. Implementation order

I would execute the repository strengthening in this order:

1. **Define the canonical models first:** `ProcessedRecord`, `Finding`, `PairFinding`, `ValidationStatus`, `ReviewPriority`, `OperationalIssue`, `AbstractResult`, `RunResult`.
2. **Fix the decision/reporting inconsistency:** one authoritative aggregation engine; remove frontend/report-layer risk recalculation.
3. **Fix Design Contradiction validation gating:** rejected results become inactive.
4. **Build shared preprocessing:** normalization, sentence splitting, section representation and entity extraction once per record.
5. **Optimize Template Detection:** remove all-pairs rare-title matching, add blocking caps, cache entity representations, preserve one canonical pair internally.
6. **Strengthen Tortured Phrase:** implement proximity semantics, version dictionary, clean confidence semantics.
7. **Strengthen Novel Nonsense:** broader deterministic candidate routing + batched LLM review.
8. **Strengthen LLM Traces:** bounded concurrent batch execution, consistent validation state and failure reporting.
9. **Strengthen Trial Verification:** persistent cache + rate limiting + concurrency + explicit operational states.
10. **Extend Numerical Contradictions:** introduce structured numerical claims and then cross-sentence/cross-section comparison.
11. **Build one `RunResult`:** all detectors feed the same consolidated result object.
12. **Generate JSON and Excel from that same object.**
13. **Add cross-output tests:** JSON and Excel must report identical counts/statuses/priorities.
14. **Add CI:** unit tests, regression tests, schema tests, lint/type checks.
15. **Add an ASCO-scale benchmark:** runtime, peak memory, template candidate count, PubMedBERT inference count, LLM calls/tokens/retries, registry calls and failure counts.

The repository already has substantial detector-level tests, which is a good foundation.  What it is missing is stronger **system-contract testing**: “Given the same run, do detection → validation → aggregation → JSON → Excel all tell exactly the same story?”

## The end state I would target

```text
                     INPUT
                       │
                       ▼
               XML Parser / QA
                       │
                       ▼
               ProcessedRecord
       normalization + entities once
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   Per-record      Pair-based      External
   detectors       template        verification
        │              │              │
        └──────────────┼──────────────┘
                       ▼
               Candidate Findings
                       │
                       ▼
             Validation / Context
                       │
                       ▼
                Final Findings
                       │
                       ▼
                Decision Engine
                       │
                       ▼
                   RunResult
                  /         \
                 /           \
                ▼             ▼
             JSON           Excel
          machine truth    editor view
```

That is what I would define as **repository V2**.

You do **not** need new detectors before this. The priority now should be making the existing detectors behave as parts of one reliable system: **shared preprocessing → normalized evidence → authoritative validation → authoritative aggregation → identical JSON/Excel outputs**.




Consider this repository **done for the ASCO V1** when you can run the full pipeline on the target dataset and confidently say:

> **Every abstract was processed, every detector either produced a valid result or an explicit failure, and the Excel and JSON tell exactly the same story.**

I would use these as your Definition of Done:

1. **All current pipelines are logically stable**

   * Template detection
   * LLM response traces
   * Known tortured phrases
   * Novel tortured/nonsense candidates
   * Numerical contradictions
   * Study-design contradictions
   * Trial verification
   * No known major false-positive logic bug remains.
   * Rejected/uncertain/failed validations are handled correctly.

2. **No detector silently fails**

   * XML failure → recorded.
   * PubMedBERT failure → recorded.
   * LLM timeout → recorded.
   * Registry failure → recorded.
   * A failed check must never appear as `"no issue found"`.

3. **One authoritative result model exists**

   The flow should be:

   ```text
   detector
      ↓
   finding
      ↓
   validation
      ↓
   final finding state
      ↓
   aggregation
      ↓
   AbstractResult
   ```

   Excel and JSON consume that result. They do not independently recalculate anything.

4. **These concepts are clearly separated**

   ```text
   Detection
   ≠ Validation
   ≠ Review priority
   ≠ Overall content risk
   ```

   This is especially important for LLM traces, design contradictions, template matches and trial verification.

5. **Template detection scales properly**

   * No unnecessary all-vs-all comparisons.
   * Entity extraction runs once per abstract.
   * PubMedBERT results are reused.
   * Pair calculations are canonical internally.
   * A→B / B→A duplication happens only for the reviewer output.

6. **LLM/external calls are operationally safe**

   * Batched where appropriate.
   * Bounded concurrency.
   * Retry limits.
   * Rate limits.
   * Failure tracking.
   * Model/prompt version recorded.
   * No unbounded network loops.

7. **The JSON is your complete machine-readable truth**

   It contains at minimum:

   ```text
   run metadata
   abstracts
   findings
   template pairs
   validation states
   review priorities
   overall results
   operational failures
   detector/model versions
   ```

8. **The Excel is a clean reviewer representation of that JSON**

   At minimum:

   ```text
   Dashboard
   Abstracts
   Findings
   Template_Pairs
   Operational_Issues
   Run_Metadata
   ```

   An editor should be able to answer:

   > What was flagged?
   > Why was it flagged?
   > What evidence supports it?
   > Was it validated?
   > What requires my attention?

9. **Excel and JSON reconciliation tests pass**

   For the same run:

   ```text
   JSON record count
   = Excel abstract count

   JSON active finding count
   = Excel finding count

   JSON review-required count
   = Excel review-required count

   JSON template pair count
   = Excel template pair count
   ```

   And statuses/severities/priorities must agree record-by-record.

10. **A representative ~6,000-abstract run completes successfully**

    This is your most important engineering acceptance test.

    Record:

    ```text
    total runtime
    peak memory
    candidate template pairs
    final template pairs

    LLM request count
    retries
    failures
    token usage

    PubMedBERT inference count
    registry requests
    registry failures

    parse failures
    detector failures
    records successfully completed
    ```

    You don't need an arbitrary "must finish in 10 minutes" target yet. First establish a baseline and verify there are no pathological bottlenecks.

11. **Regression tests cover the important business rules**

    Particularly:

    ```text
    known true positive → detected
    known false positive → suppressed

    rejected validation → not active
    uncertain validation → review, not confirmed
    external lookup failure → not interpreted as suspicious

    related-study template overlap → contextualized
    strong unexplained template overlap → reviewable

    detector failure → operational issue
    ```

12. **The run is reproducible**

    Given the same:

    ```text
    input
    code version
    rule dictionary
    model version
    configuration
    thresholds
    ```

    you should be able to explain exactly how the result was generated.

---

### What is *not* required for you to call V1 done

You do **not** need to:

* eliminate every false positive;
* build perfect cross-document semantic reasoning;
* support hundreds of thousands of papers;
* create a sophisticated distributed architecture;
* continuously add new detectors;
* turn every heuristic into ML;
* make every external registry automatically searchable;
* achieve theoretical perfection.

The V1 boundary should be much simpler:

**Correct logic + controlled failures + ASCO-scale execution + traceable evidence + one source of truth + trustworthy Excel/JSON.**

Once those conditions hold, I would stop strengthening the core repository and move into **real ASCO validation/calibration**. At that point, improvements should be driven by observed false positives, false negatives and editor feedback rather than continued speculative engineering.


For testing use /home/pavankrishna/Projets/ASCO/synthetic_asco_retractionwatch_validation.xml

and use 10 records inside any xml file in this path home/pavankrishna/Projets/ASCO/real_asco_files
