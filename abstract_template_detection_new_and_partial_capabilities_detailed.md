# Abstract Template Detection — New and Partially Existing Capabilities

## Purpose

This document explains the template-detection capabilities that are either:

- **Completely new** — not currently implemented in the abstract template pipeline.
- **Partially existing** — a basic capability exists, but it does not yet meet the intended product scope.

The document is written for product, engineering, research, and editorial stakeholders. It explains what each capability does, what must be built or improved, why it matters, and the limits that must be applied.

## Final implementation status — 11 August 2026

The deterministic/hybrid template detector is the production baseline. It combines exact-text reuse and entity-normalized local substitution evidence, keeps legitimate related/companion work separate from suspicious template reuse, and excludes generic sponsor/statistical language from rare-phrase evidence. Duplicate exact/entity evidence does not promote confidence, and unsupported registry notices remain informational rather than risk-eligible.

Validation results:

- Reviewer-labelled benchmark: 85.1% precision, 75.0% recall, 79.7% F1 (178 automatic labels; 2 manual labels excluded).
- Real-ASCO corpus: 519 abstracts, 13 unique related/companion pairs, 0 suspicious families, and no sponsor/statistical boilerplate evidence.
- The default runner now writes the integrated advanced review chain: 2,382 routed candidate pairs, 2,381 insufficient-evidence pairs, 1 possible-related-work pair at Low priority, 519 abstract summaries, and 0 advanced suspicious families.
- Full regression suite: 203 tests passed.
- Updated architecture diagram: `template_detection_low_level.html`.

SciSpaCy was tested as an optional gap-filler using `en_ner_bionlp13cg_md`. It reduced entity precision from 69.1% to 56.2% and F1 from 65.9% to 59.4%, while recall remained approximately 63.0%. It is therefore not enabled by default.

## Scope boundary

The template-detection scope is limited to:

- Abstract title
- Abstract body
- Structured abstract sections
- Abstract metadata, including authors, affiliations, trial identifiers, and related-study context

The scope does **not** include:

- Full manuscripts
- Figures or images
- Supplementary files
- Primer validation
- Scientific-quality assessment
- Automatic misconduct findings
- Automatic acceptance or rejection decisions

A template finding is evidence for editorial review. It is not a verdict.

## Decision guide

- **Include now** — commit to the deterministic abstract-template roadmap.
- **Include with constraint** — include the capability, but keep it within an explicit boundary.
- **Validate first** — test prevalence, false positives, and incremental value on representative ASCO abstracts before full implementation.
- **Defer / optional** — not required for deterministic V1.

---

# 1. Completely New Capabilities

## Stylometric similarity

**Decision:** Defer / optional  
**Evidence role:** Supporting evidence only

### What it adds

Stylometric similarity compares writing habits rather than copied wording. Possible features include:

- Sentence-length distribution
- Punctuation patterns
- Function-word usage
- Passive-voice frequency
- Readability measures
- Repeated syntactic structures
- Paragraph and sentence rhythm

### How it would work

1. Extract a style feature vector from each abstract.
2. Compare the feature vectors between candidate abstracts.
3. Report whether the writing profiles are unusually similar.
4. Use the result only when stronger title, body, or structural evidence is also present.

### Why it may help

Two abstracts may be rewritten enough to remove obvious word overlap while retaining a similar writing style. Stylometry may provide weak corroborating evidence in such cases.

### Why it is not required now

Writing style is not specific enough to prove template reuse. Similarity may be caused by:

- Conference formatting requirements
- Language-editing services
- Institutional writing practices
- Translation
- Shared scientific conventions
- The same legitimate author group

### Required safeguards

- Never use stylometry as primary evidence.
- Do not create a template finding from style similarity alone.
- Calibrate features on ASCO abstracts before use.
- Display the result as supporting context, not as proof of common authorship or misconduct.

---

## Similarity to known suspicious abstracts

**Decision:** Defer / optional  
**Evidence role:** Candidate retrieval or supporting evidence

### What it adds

This capability compares a new abstract with abstract families that were reviewed in earlier batches.

It answers:

> Does this new abstract resemble a previously reviewed template family, even when that family does not recur in the current submission batch?

### How it would work

1. Store reviewed abstract families in a reference library.
2. Assign each family a stable identifier.
3. Store versioned title, body, entity, structure, and signature representations.
4. Compare incoming abstracts with the reference library.
5. Return the matching family, similarity evidence, and source records.

### Why it may help

A template may reappear in a later year or submission cycle. Current-batch clustering cannot detect this when only one new abstract uses the old pattern.

### What must exist before implementation

- A reviewer-approved reference library
- Stable family identifiers
- Versioned feature representations and indexes
- Provenance showing which historical records caused the match
- Rules for adding, updating, and removing reference families
- Clear separation between reviewed-suspicious and merely similar records

### Required safeguards

- Historical similarity must not become an automatic verdict.
- The report must show the exact reference family and evidence.
- Changes to the reference library must be auditable.
- A weak historical match must not override stronger current-batch evidence.

---

## Transparent pair classification

**Decision:** Include now  
**Evidence role:** Decision and routing layer

### What it adds

The current pipeline reports similarity, confidence, severity, and match types. This capability adds a clear product-facing classification for each abstract pair.

Recommended classes:

- **Possible template reuse** — strong repeated structure without a clear legitimate explanation
- **Possible companion analysis** — related study or dataset with a different question, population, or endpoint
- **Possible related duplicate** — related study context exists, but strong full-abstract or Results reuse remains
- **Insufficient evidence** — similarity exists, but it is not specific or strong enough for template routing

### How it would work

1. Collect primary template evidence.
2. Collect supporting scientific-pattern evidence.
3. Collect same-study and companion-analysis context.
4. Apply transparent classification rules.
5. Record the rule path that produced the final class.

### Example rule path

```text
Strong Results reuse
+ high masked-body similarity
+ shared trial ID
+ same population
+ no endpoint difference
= Possible related duplicate
```

### Why it is needed

A similarity score does not explain what a pair represents. Editors need to know whether the pair is likely:

- A reused template
- A legitimate companion analysis
- A related duplicate
- Too weak to route

This improves review consistency and prevents legitimate companion analyses from being mixed with suspicious template families.

### Required output

Each pair should contain:

- Final pair class
- Rule path
- Primary evidence
- Supporting evidence
- Contextual evidence
- Limitations or uncertainty note

### Required safeguards

- Classification must be rule-based and reproducible.
- Same-study context must not suppress exact full-abstract or Results reuse.
- Weak supporting signals must not independently create a suspicious class.

### Implemented transparent classification

`content_integrity.pair_classification.classify_pairs` applies `asco-pair-classifier-v1` rules and records the final class, rule path, primary/supporting/contextual evidence, study-context interpretation, review score, and limitation note.

- No primary evidence becomes `insufficient_evidence` regardless of supporting or contextual signals.
- Exact full-body or Results reuse plus related-study context becomes `possible_related_duplicate`.
- Other primary evidence plus aligned companion context becomes `possible_companion_analysis`.
- Remaining primary-evidence pairs become `possible_template_reuse`.

`scripts/export_pair_classifications.py` produced 1,154 rows for the supplied local/regional XML. All are `insufficient_evidence` because none met the intentionally strict Work Item 7 primary-evidence gates.

---

## Molecular-axis signatures

**Decision:** Validate first  
**Evidence role:** Supporting evidence or candidate retrieval

### What it adds

This capability identifies repeated molecular relationship structures even when the entity names change.

Example structure:

```text
lncRNA -> sponges -> miRNA -> targets -> gene
```

Two abstracts may use different lncRNAs, miRNAs, genes, and diseases while retaining the same relationship pattern.

### How it would work

1. Extract typed entities such as lncRNA, miRNA, gene, protein, pathway, and disease.
2. Detect explicit relationship verbs such as regulates, targets, activates, inhibits, or sponges.
3. Normalize synonyms and relationship wording.
4. Build an ordered molecular-axis signature.
5. Compare signatures across candidate abstracts.

### Example

```text
Abstract A: HOTAIR -> sponges -> miR-34a -> targets -> MET
Abstract B: MALAT1 -> sponges -> miR-200c -> targets -> ZEB1

Normalized signature:
LNCRNA -> SPONGES -> MIRNA -> TARGETS -> GENE
```

### Why it is needed

Basic text similarity may miss paraphrased abstracts. Typed relationship structure can reveal a repeated scientific storyline after variable substitution.

### What must be validated

- How often explicit molecular axes occur in ASCO abstracts
- Whether extraction is reliable from short abstract text
- Whether the signal finds cases missed by current text matching
- Whether common molecular-language patterns create false positives

### Required safeguards

- Molecular-axis similarity cannot be primary evidence by itself.
- Only explicit relationships in the abstract should be used.
- Do not infer unstated biological relationships.
- Store the source sentence for every extracted relationship.

### Validation result

`content_integrity.signal_validation` extracted only explicit relation triples with typed molecular entities on both sides, then compared their candidate coverage against the existing routes and the reviewer-labelled retracted-paper corpus. Molecular-axis signatures retrieved 12 labelled positives, but no positives beyond existing routes and one incremental labelled negative; the report recommendation is `reject_for_topic_noise`.

The signal was rejected because coarse signatures such as `LNCRNA -> INHIBIT -> GENE` collapse many legitimate oncology papers into the same bucket. It generated 165 candidate pairs from 24 signatures, including 149 unlabelled pairs.

---

## Assay workflow signatures

**Decision:** Validate first  
**Evidence role:** Supporting evidence only

### What it adds

This capability extracts and compares ordered experimental workflows.

Example:

```text
RT-qPCR -> transfection -> CCK-8 -> Transwell -> luciferase assay -> Western blot
```

### How it would work

1. Maintain a controlled assay vocabulary and alias list.
2. Detect assay mentions in the Methods and Results sections.
3. Normalize aliases to canonical assay names.
4. Preserve the order in which assays appear.
5. Compare complete and partial assay sequences.

### Why it is needed

A repeated experimental sequence can remain stable even when wording, genes, and diseases change. It may therefore support a template-reuse finding.

### Why validation is required

Many legitimate abstracts use common assay combinations. For example, RT-qPCR and Western blot frequently occur together. A common workflow can create large numbers of false matches.

### Required safeguards

- Assay overlap must remain supporting evidence.
- Common workflows must not create high-confidence findings.
- Candidate buckets must have frequency limits.
- The system should give more weight to distinctive ordered workflows than to common individual assays.
- Every extracted assay should retain the supporting section and sentence.

### Validation result

Ordered assay signatures retrieved 3 labelled positives, 0 labelled negatives, and no incremental positives beyond existing routes. They remain `keep_disabled` as a report-only signal.

The route was redundant rather than demonstrably harmful: standard workflows such as RT-qPCR and Western blot are common across unrelated studies, so they are not specific enough to create template findings independently.

---

## Endpoint bundle signatures

**Decision:** Validate first  
**Evidence role:** Supporting or companion-analysis context

### What it adds

This capability normalizes the outcomes measured or reported in an abstract.

Examples include:

- Proliferation
- Migration
- Invasion
- Apoptosis
- Cell cycle
- Overall survival
- Progression-free survival
- Tumour growth
- Response rate
- Quality of life

### How it would work

1. Detect endpoint and outcome phrases.
2. Normalize synonyms to canonical endpoint names.
3. Record the section where each endpoint appears.
4. Build an endpoint set or ordered bundle.
5. Compare shared and different endpoints across abstracts.

### Why it is needed

Repeated endpoint bundles may support a repeated abstract template. Endpoint differences are also important for identifying legitimate companion analyses.

### Example

```text
Shared study context: same trial and population
Abstract A endpoints: overall survival, progression-free survival
Abstract B endpoints: quality of life, adverse events

Interpretation: likely companion analyses rather than duplicate results
```

### Required safeguards

- Endpoint overlap cannot be primary evidence.
- Common oncology endpoints must receive low specificity.
- Endpoint differences should be considered alongside trial, population, and time-period evidence.
- The report must distinguish exact endpoint reuse from broad topic similarity.

### Validation result

The current endpoint vocabulary produced one bundle signature but no candidate pairs on the labelled corpus. Endpoint bundles remain `keep_disabled`; expanding vocabulary requires separate extraction validation first.

This is an incomplete implementation result, not a negative scientific finding: the extractor was too sparse to produce a useful candidate route.

---

## Optional LLM review for ambiguous pairs

**Decision:** Defer / optional  
**Evidence role:** Ambiguity annotation only

### What it adds

A language model reviews a small, gated set of borderline pairs where deterministic rules cannot resolve uncommon wording.

Possible questions include:

- Are the populations equivalent?
- Are two endpoint phrases referring to the same outcome?
- Do two relationship descriptions express the same molecular direction?
- Does the text explicitly indicate a companion or follow-up analysis?

### How it would work

1. Run all deterministic checks first.
2. Select only unresolved pairs that meet a strict gating rule.
3. Send only the necessary snippets and structured evidence.
4. Ask the model to return constrained fields.
5. Store the model output separately from deterministic evidence.

### Why it may help

Scientific language can express the same concept in uncommon ways that are difficult to capture with fixed rules.

### Required safeguards

- The model must not become the primary template classifier.
- It must not override exact or deterministic evidence.
- It must not add facts that are absent from the supplied text.
- Every response must include supporting snippets.
- A deterministic fallback must remain available.
- Model and prompt versions must be recorded.

---

## Offline pattern and vocabulary discovery

**Decision:** Defer / optional  
**Evidence role:** Offline rule discovery

### What it adds

A language model periodically reviews confirmed misses and proposes new deterministic vocabulary or patterns.

Possible proposals include:

- New molecular relationship verbs
- New assay aliases
- New endpoint phrases
- New registry or database names
- New title-template patterns

### How it would work

1. Collect reviewer-confirmed misses.
2. Provide the missed examples to the model offline.
3. Ask for proposed aliases, rules, or patterns with supporting examples.
4. Review proposals manually.
5. Add approved changes to versioned dictionaries or rules.
6. Run regression tests before release.

### Why it is needed

Scientific language changes over time. Manual dictionaries may become incomplete, especially for new assay names, molecular terms, and endpoint wording.

### Required safeguards

- No model-generated rule enters production automatically.
- Every proposal requires human approval.
- Every approved change requires regression testing.
- Vocabulary and rule versions must be recorded.
- Proposals must include evidence from confirmed examples.

---

## Reviewer-friendly evidence summaries

**Decision:** Defer / optional  
**Evidence role:** Reporting only

### What it adds

This capability converts structured pair or family evidence into a short plain-language explanation.

Example:

> These abstracts share a highly similar masked title and Results structure. The main substitutions are the gene and cancer type. They do not share a trial ID or author group. The finding is classified as possible template reuse.

### How it would work

1. Generate the structured pair or family evidence first.
2. Pass only approved structured fields to the model.
3. Produce a short explanation using a fixed format.
4. Preserve a deterministic text fallback.

### Why it is needed

Complex findings may contain many metrics, substitutions, and context fields. A short explanation can reduce reviewer effort.

### Required safeguards

- The summary must not change the detection result.
- It must not introduce evidence absent from structured fields.
- It must clearly distinguish evidence from limitations.
- A deterministic summary must remain available.
- Model and prompt versions must be recorded.

---

# 2. Partially Existing Capabilities

## Same template, different authors

**Decision:** Include with constraint  
**Evidence role:** Context only

### What exists today

- Template comparison does not depend on author names.
- Shared authors and affiliations are already used as legitimate-reuse context.
- Different author groups do not currently create a template finding.

### Improvement needed

Add clear author-group context to the pair report:

- Number of shared authors
- Overlap percentage
- Shared corresponding author
- Shared affiliations
- Apparently unrelated author groups

### Why it matters

A strong template match across unrelated author groups may be more notable to reviewers than a match within the same research group. However, different authors do not prove template reuse.

### Scope constraint

Author difference must never act as a standalone detector. The finding must come from title, body, section, or structural evidence.

---

## Repeated title formula

**Decision:** Include now  
**Evidence role:** Primary or candidate-retrieval evidence

### What exists today

The title is mainly used when abstract text is unavailable. A dedicated title-comparison path is not consistently applied when the abstract body exists.

### Improvement needed

For every abstract title:

1. Normalize case and punctuation.
2. Mask typed biomedical values.
3. Preserve fixed wording.
4. Extract the entity-type sequence.
5. Create a normalized title signature.
6. Compare original and masked similarity.
7. Add controls for generic title patterns.

### Example

```text
Expression of EGFR predicts survival in lung cancer
Expression of HER2 predicts survival in breast cancer

Masked title:
Expression of <GENE> predicts survival in <DISEASE>
```

### Why it matters

Titles are short and often preserve a rigid formula even when the abstract body has been paraphrased.

### Required safeguards

- Generic forms such as “Role of X in Y” must receive low specificity.
- A title-only match should require distinctive fixed wording, multiple repeated records, or supporting body evidence.

---

## Low-overlap semantic similarity

**Decision:** Validate first  
**Evidence role:** Candidate retrieval or supporting evidence

### What exists today

- Entity masking
- N-gram comparison
- Section-level similarity
- Meaningful original-text support requirements

These methods recover some paraphrased cases, but they do not provide broad semantic retrieval.

### Improvement needed

Test semantic retrieval as a candidate-generation route:

1. Create scientific-text embeddings for titles, sections, or full abstracts.
2. Retrieve nearest-neighbour candidates.
3. Verify candidates with deterministic title, body, entity, or structural evidence.
4. Reject pairs that are only about the same oncology topic.

### Why it matters

Some templates may be paraphrased enough to remove ordinary text overlap.

### Why validation is required

Oncology abstracts often discuss the same diseases, therapies, endpoints, and study designs. Semantic similarity can therefore produce many legitimate topic matches.

### Required safeguards

- Semantic similarity must not create a template finding by itself.
- It should be used mainly to retrieve candidates.
- A deterministic verification stage is mandatory.
- Evaluation must measure false positives from topic similarity.

### Validation result

Semantic embedding retrieval is deferred. No semantic model or dependency was added because the deterministic signal validation found no incremental positive coverage to justify broadening candidate retrieval.

---

## Template evaluation baseline

**Decision:** Include now  
**Evidence role:** Evaluation foundation

### What exists today

- A synthetic labelled corpus
- An evaluation script
- Pair-level and family-level error measurement

### Improvement needed

Create a representative reviewer-labelled ASCO evaluation set containing:

- Exact full-abstract reuse
- Exact Results or Methods reuse
- Partial and reordered reuse
- Entity-swapped templates
- Repeated title formulas
- Low-overlap paraphrased templates
- Companion analyses
- Same-study related duplicates
- Common-domain false positives
- Unrelated negative controls

### Required labels

Each pair should include:

- Expected pair class
- Expected primary evidence
- Expected legitimate-study context
- Whether it should enter a suspicious family
- Reviewer notes

### Why it matters

The baseline is needed to:

- Measure precision and recall
- Calibrate thresholds
- Compare old and new pipeline versions
- Measure incremental value of each new signal
- Detect regressions
- Quantify false positives from companion analyses

### Required evaluation outputs

- Pair precision and recall
- Family precision and recall
- False-positive categories
- Missed-pattern categories
- Wrong family merges

### Current labelled evaluation results

- Version 3 gold set: **180** labelled pairs, including **2** manual-review labels.
- Automatic evaluation, excluding manual-review labels: **84 TP**, **25 FP**, **0 FN**, **69 TN**.
- Precision: **77.1%**; recall: **100%**; F1: **87.0%**.
- The remaining false positives are retained as labelled negatives for detector calibration.

---

## Typed biomedical entity extraction and masking

**Decision:** Include now  
**Evidence role:** Representation foundation

### Implemented baseline

`content_integrity.entity_extraction` is the shared hybrid extractor used by the entity-template detector and masked-section export. It uses deterministic patterns plus conservative contextual patterns; it is **not** a trained NER model.

It masks broad values such as:

- Gene
- Drug
- Disease
- Biomarker
- Trial ID
- Date
- Percentage
- P-value
- Number

It also extracts the richer types requested for this work item:

- miRNA and lncRNA
- Protein
- Pathway
- Cell line
- Assay
- Endpoint
- Registry or database
- Population
- Treatment class

Each exported entity retains its original text, normalized value, entity type, character offsets, section, sentence index, extraction method, confidence, and vocabulary version (`asco-hybrid-v1`). The entity-normalized template detector preserves its calibrated protein/gene comparison behaviour while the export retains the richer protein type.

### Real-ASCO structural validation

On `Breast_Cancer_Metastatic_publication.xml`, the extractor processed **443** structured sections and all **443** passed source-span, non-overlap, and masked-text reconstruction checks. The exports are:

- `outputs/breast_cancer_masked_sections.csv`
- `outputs/breast_cancer_masking_validation.csv`

### Semantic validation

The supplied local/regional masked-reference data was evaluated against `Breast_Cancer_Local_Regional_Adjuvant_publication.xml` with `scripts/evaluate_masked_entity_rules.py`.

- 244 records were evaluated.
- 9,041 exact span-and-type matches yielded **69.1% precision**, **63.0% recall**, and **65.9% F1**.
- 33 supplied reference entities could not be aligned after XML normalisation; they were excluded transparently.
- Per-type metrics are in `outputs/local_regional_entity_metrics.csv`.

The deterministic rules remain the production baseline. Any NER experiment must demonstrate an improvement against this benchmark before it is enabled.

The extractor already retains:

- Original text
- Normalized value
- Entity type
- Character offsets
- Section and sentence
- Extraction method
- Vocabulary or model version

### Why it matters

Broad masking may erase important scientific roles. For example, treating a miRNA and a target gene as the same generic gene type makes substitution explanations less useful and may weaken molecular-pattern detection.

### Required safeguards

- Preserve the original text alongside the masked form.
- Record extraction provenance.
- Do not force uncertain entities into a specific type without a confidence or unknown category.

---

## Reusable template feature index

**Decision:** Include now  
**Evidence role:** Architecture foundation

### Implemented feature object

`content_integrity.template_features.TemplateFeatures` is the shared, versioned JSON-serializable representation for one parsed record. It retains title, abstract, and structured-section original, normalised, and masked text; typed entities with offsets and provenance; trial IDs; structured-abstract state; and a stable content hash.

`scripts/export_template_features.py` exports these objects as JSONL. The supplied local/regional XML produced 244 objects in `outputs/local_regional_template_features.jsonl`.

### Suggested feature object

```yaml
abstract_id: A123
title:
  original: "..."
  normalized: "..."
  masked: "..."
  signature: "..."
body:
  original: "..."
  normalized: "..."
  masked: "..."
sections:
  background: "..."
  methods: "..."
  results: "..."
  conclusion: "..."
entities:
  - type: GENE
    original: EGFR
    normalized: EGFR
    section: results
context:
  trial_ids: [NCT12345]
  databases: []
  authors: []
  affiliations: []
signatures:
  title_hash: "..."
  masked_body_hash: "..."
  section_fingerprints: {}
  entity_type_sequence: []
version:
  extractor_version: "..."
  vocabulary_version: "..."
```

### How it should be used

The same feature object should support:

- Candidate retrieval
- Pair comparison
- Pair classification
- Family clustering
- Reporting
- Evaluation and reproducibility

### Why it matters

A shared feature object prevents:

- Different detectors extracting the same value differently
- Repeated processing
- Mismatches between detection and reporting
- Untraceable results after rules or vocabularies change

### Required safeguards

- Every feature object must be versioned.
- Raw input and original spans must remain available.
- Changes to extraction logic must trigger regression testing.

---

## Enriched variable-substitution reporting

**Decision:** Include now  
**Evidence role:** Explainability

### What exists today

The pipeline reports changed values for existing broad entity types.

### Improvement needed

For every matched pair, report:

- Values shared by both abstracts
- Values only in the left abstract
- Values only in the right abstract
- Likely substitutions

### Implemented typed substitution evidence

`content_integrity.entity_substitutions.collect_entity_substitutions` compares title and abstract typed entities by normalized value. It exports shared values, values unique to either record, same-type substitution candidates, and up to two source sentences per side. Assays and treatment classes remain descriptive shared/side-specific context rather than substitution candidates.

`scripts/export_entity_substitutions.py` produced 1,154 candidate-pair rows for the supplied local/regional XML. The same fields are included in the enriched pair report. They are explanatory only and never change class, family eligibility, score, or editorial priority.
- Entity type of each value
- Relationship slot when available
- Supporting sentence or section

### Example

| Slot | Abstract A | Abstract B |
|---|---|---|
| lncRNA | HOTAIR | MALAT1 |
| miRNA | miR-34a | miR-200c |
| Target gene | MET | ZEB1 |
| Disease | Lung cancer | Breast cancer |

### Why it matters

A score tells the reviewer that two abstracts are similar. Substitution reporting explains how one abstract may have been produced from the same template.

### Required safeguards

- Distinguish confirmed substitutions from unmatched values.
- Do not infer a relationship slot unless the abstract states it.
- Always retain the source text for review.

---

## Dedicated title-template comparison

**Decision:** Include now  
**Evidence role:** Primary or candidate-retrieval evidence

### Implemented baseline

`content_integrity.title_templates` derives a SHA-256 signature from each masked title in `TemplateFeatures`, retrieves exact signature matches and conservative near matches sharing at least two masked trigrams, then records original and masked title similarity.

`scripts/compare_title_templates.py` exports the retrieval results. The supplied local/regional XML produced 533 title candidates in `outputs/local_regional_title_template_candidates.csv`.

This is retrieval evidence only: title matches do not create pair findings or classification without later body/context support.

### Comparison flow

1. Retrieve candidate titles using masked hashes or shared masked trigrams.
2. Compare original and masked wording.
3. Identify fixed phrases and substituted entity slots.
4. Measure how common the title formula is in the batch.
5. Use body evidence to confirm weak title-only cases.

### Why it matters

A repeated title formula may remain visible when the body is heavily paraphrased.

### Required safeguards

- Common title formats cannot independently create a high-confidence finding.
- Title-only findings must clearly state that body support is absent or limited.

---

## Structured same-dataset and companion-analysis context

**Decision:** Include now  
**Evidence role:** Legitimate-reuse context

### What exists today

The current pipeline uses:

- Shared trial IDs
- Shared authors
- Shared affiliations
- Related-analysis wording

These signals can reduce severity but do not produce a structured study-context classification.

### Improvement needed

Extract and compare:

- Trial or protocol ID
- Registry or database name
- Study period
- Sample size
- Population and eligibility description
- Treatment arms
- Authors and affiliations
- Primary and secondary endpoints
- Follow-up, subgroup, extension, or companion wording

### Suggested context output

```yaml
same_trial_id: true
same_database: false
same_population: likely
same_period: true
shared_authors: 4
shared_affiliations: 2
endpoint_overlap: partial
explicit_companion_wording: true
context_interpretation: likely_companion_analysis
```

### Why it matters

ASCO may receive multiple legitimate analyses from the same trial or dataset. These abstracts may reuse study descriptions while answering different questions.

### Required safeguards

- Context changes classification and routing; it does not erase evidence.
- Exact full-abstract or Results reuse must remain visible.
- Different endpoints should support companion-analysis classification only when study context also aligns.

### Implemented structured context export

`content_integrity.study_context.compare_study_context` now exports trial/database, period, sample-size, population, treatment, endpoint, author, affiliation, and explicit companion wording for each routed candidate pair. Its interpretation is contextual only: endpoint differences can produce `likely_companion_analysis` only when trial or other independent study context aligns.

`scripts/export_study_context.py` produced 1,154 rows for the supplied local/regional XML: 291 `possible_related_study` and 863 `no_structured_context`; none met the conservative aligned-study gate.

---

## Primary, supporting, and contextual evidence tiers

**Decision:** Include now  
**Evidence role:** Evidence architecture

### Implemented direct-evidence export

`content_integrity.pair_evidence.collect_pair_evidence` evaluates every routed candidate pair and exports exact original-body and Results-section reuse, the largest shared original block, masked and original title/body similarity, strongest masked-section similarity, and explicit direct-evidence codes.

`scripts/export_pair_evidence.py` produced 1,154 rows for the supplied local/regional XML. No row met its intentionally strict direct-evidence gates, so no title or masked similarity was treated as a finding by itself.

### Implemented tiered scoring

`content_integrity.evidence_scoring.score_pair_evidence` separates primary direct evidence, supporting title/body/section similarity, and contextual trial/author/affiliation overlap.

- A pair receives a non-zero review score only when it has primary evidence.
- Supporting evidence is capped at 0.25 and can only strengthen primary evidence.
- Results and Conclusions section similarity receive 0.20 supporting weight; Background receives 0.10 and Methods 0.05.
- Contextual evidence never changes the review score.

`scripts/export_evidence_scores.py` exported 1,154 rows for the supplied local/regional XML. All were `none` under the strict primary-evidence gates, which prevents candidate retrieval from being mistaken for a finding.

#### Primary evidence

Evidence that can directly support a template finding:

- Exact full-abstract reuse
- Exact Results reuse
- Substantial shared text
- Distinctive masked title template
- Strong masked body template with original-text support

#### Supporting evidence

Evidence that strengthens primary evidence but should not create a finding alone:

- Molecular-axis similarity
- Assay workflow similarity
- Endpoint bundle overlap
- Scientific-recipe similarity
- Stylometric similarity

#### Contextual evidence

Evidence used to explain legitimate relationships or route the pair:

- Shared trial ID
- Shared database
- Shared authors or affiliations
- Same population
- Companion-analysis wording
- Different endpoints

### Confidence rule

Confidence should increase only when independent evidence agrees. It should not increase merely because several correlated metrics are present.

### Why it matters

This prevents common scientific features from overstating template confidence and keeps legitimate-study context separate from suspicious evidence.

### Required safeguards

- Every evidence item must have a defined tier.
- Confidence rules must be documented and versioned.
- Supporting or contextual evidence alone cannot create high confidence.

### Implemented editorial priority

`content_integrity.editorial_scoring.assign_editorial_priority` applies `asco-editorial-priority-v1` without modifying the existing review score: primary evidence at 0.85 or above is High, 0.75–0.849 is Medium, and the 0.65 title-template band is Low. Likely companion analyses are capped at Low; study context and family size never increase priority.

The thresholds reuse the audited primary-evidence weights and are intentionally conservative. They should be statistically re-fit only after the reviewer-labelled dataset contains this score schema. `scripts/export_editorial_priorities.py` produced 1,154 `None` priorities for the supplied local/regional XML because no pair met the primary-evidence gate.

---

## Enriched pair, family, and abstract reporting

**Decision:** Include now  
**Evidence role:** Reporting and explainability

### What exists today

The pipeline already reports detailed pair metrics, substitutions, and family summaries.

### Improvement needed

#### Pair-level fields

- Pair class
- Rule path
- Title evidence
- Body and section evidence
- Primary, supporting, and contextual evidence
- Shared and substituted entities
- Same-dataset fields
- Candidate-retrieval route
- Limitations

#### Family-level fields

- Representative abstract
- Dominant title template
- Dominant body template
- Common substitutions
- Shared molecular axis
- Shared assay workflow
- Shared endpoint bundle
- Family density
- Outliers
- Excluded companion-analysis pairs

#### Abstract-level fields

- Number of accepted template links
- Family ID
- Role in family, such as medoid or outlier
- Strongest finding
- Review status

### Why it matters

Stakeholders need to understand:

- Why a pair or family was surfaced
- Which evidence is strong
- Which evidence is only supporting
- Whether a legitimate study relationship exists
- How the result should be reviewed

### Required safeguards

- Every report value must be traceable to structured evidence.
- Reports must distinguish measured evidence from interpretation.
- Output schema versions must be recorded.

### Implemented enriched reports

`content_integrity.enriched_reporting.build_enriched_reports` writes `asco-enriched-report-v1` pair, family, and abstract schemas. Pair rows join retrieval routes, title/body/section measures, evidence tiers, structured study context, class, editorial priority, family status, rule path, and limitations. Family rows remain empty when no eligible edges exist; abstract rows preserve candidate and finding counts without treating candidates as findings.

`scripts/export_enriched_reports.py` produced 1,154 pair rows, 0 family rows, and 244 abstract rows for the supplied local/regional XML. Entity substitution details are deliberately left for Work Item 14.

---

## Section-aware scientific-recipe signatures

**Decision:** Validate first  
**Evidence role:** Supporting structural evidence

### What exists today

- Abstract sections are compared separately.
- Results and Conclusions receive additional weight.
- Section-level similarity is available.

### Improvement needed

Build a scientific-recipe representation that records what type of scientific information appears in each section.

### Example recipe

```yaml
background:
  - biomarker_rationale
methods:
  - expression_measurement
  - transfection
  - proliferation_assay
results:
  - increased_proliferation
  - increased_migration
conclusion:
  - proposed_therapeutic_target
```

The recipe may combine:

- Typed entities
- Molecular relationships
- Assays
- Endpoints
- Claim types
- Section placement
- Component order

### Why it matters

Two abstracts may use different words but retain the same scientific production structure.

### What must be validated

- Extraction accuracy by section
- Incremental recall over current masked-text comparison
- False positives from standard abstract conventions
- Value in structured versus unstructured abstracts

### Required safeguards

- Normal Background–Methods–Results–Conclusion structure cannot be treated as suspicious.
- The recipe must depend on specific scientific components, not only section headings.
- Recipe similarity remains supporting evidence unless combined with stronger template evidence.

---

## Enriched candidate-generation indexes

**Decision:** Validate first  
**Evidence role:** Candidate retrieval

### Implemented routes

`content_integrity.candidate_routes.generate_candidate_pairs` reuses the existing body candidate generator and adds the title-template route. Each deduplicated pair retains every route that retrieved it:

- `body_candidate`
- `exact_masked_body`
- `exact_original_body`
- `exact_masked_section`
- `title_template`

`scripts/export_candidate_routes.py` exports this provenance. On the supplied local/regional XML, it produced 1,154 unique pairs: 694 body-route pairs and 533 title-route pairs, with overlap retained as multiple routes.

Molecular, assay, endpoint, recipe, and semantic routes remain deferred until their evidence value is validated.

### Route provenance

For every candidate pair, record why it was retrieved.

Example:

```yaml
retrieval_routes:
  - masked_title_match
  - molecular_axis_bucket
  - section_fingerprint_match
```

### Frequency controls

Common signatures can create very large candidate buckets. Apply:

- Maximum bucket size
- Minimum specificity
- Frequency-based down-weighting
- Sampling or secondary filters
- Deduplication across routes

### Why it matters

New evidence cannot improve detection unless the pipeline retrieves the relevant pairs. Candidate generation must remain efficient for approximately 6,000 abstracts.

### Required safeguards

- Retrieval does not equal a finding.
- Every candidate requires detailed verification.
- Common assay and endpoint buckets must be capped.
- Each new route must show incremental value in evaluation.

---

## Enriched family clustering and signatures

**Decision:** Validate first  
**Evidence role:** Family-level evidence

### What exists today

The pipeline already supports:

- Graph-based clustering
- Medoid or representative-record selection
- Member verification
- Family density
- Dominant match type
- Family confidence

### Improvement needed

#### Edge eligibility

Only accepted pair classes should create suspicious family edges:

- Possible template reuse
- Possible related duplicate

Companion-analysis and insufficient-evidence pairs should remain visible but should not connect suspicious families.

#### Enriched family signature

For each family, summarize:

- Dominant title template
- Dominant body or section template
- Common entity substitutions
- Shared molecular-axis pattern
- Shared assay workflow
- Shared endpoint bundle
- Common study context
- Outlier members
- Excluded legitimate relationships

### Example family summary

> Five abstracts share the title formula “Expression of X predicts survival in Y.” Four also share the same Results structure. The main substitutions are gene, disease, and population. Three abstracts share a common assay sequence. One record is an outlier because its Results and endpoints differ.

### Why it matters

A connected-record list does not explain what defines the family. A family signature gives editors a coherent view of the repeated pattern.

### What must be validated

- Whether pair classes prevent incorrect family merges
- Whether enriched signatures improve reviewer understanding
- Whether outlier detection prevents weak transitive members
- Whether family summaries remain stable when thresholds change

### Required safeguards

- Weak transitive links must not automatically validate all members.
- Every family member should be checked against the representative pattern.
- Companion analyses should not form suspicious-family edges.
- Family confidence must be based on accepted pair evidence, not family size alone.

### Implemented suspicious-edge clustering

`content_integrity.family_clustering.cluster_suspicious_families` builds connected components only from `possible_template_reuse` and `possible_related_duplicate` pairs whose review score is at least 0.75. It chooses the representative with the greatest accepted-edge evidence, reports mean accepted-edge score independently of family size, and marks transitive-only members as outliers when they lack a direct eligible edge to the representative.

`scripts/export_suspicious_families.py` produced a schema-only CSV for the supplied local/regional XML because all 1,154 candidate pairs were classified as insufficient evidence; no companion or weak pair was promoted into a family.

---

# 3. Recommended Delivery Order

## Commit now

1. Template evaluation baseline
2. Typed biomedical entity extraction and masking
3. Reusable template feature index
4. Enriched variable-substitution reporting
5. Dedicated title-template comparison
6. Structured same-dataset and companion-analysis context
7. Primary, supporting, and contextual evidence tiers
8. Transparent pair classification
9. Enriched pair, family, and abstract reporting
10. Author-group context with explicit constraints

## Validate before implementation

1. Low-overlap semantic candidate retrieval
2. Molecular-axis signatures
3. Assay workflow signatures
4. Endpoint bundle signatures
5. Section-aware scientific-recipe signatures
6. Enriched candidate-generation routes
7. Enriched family signatures and edge rules

## Defer or keep optional

1. Stylometric similarity
2. Historical matching against known suspicious abstract families
3. LLM review for ambiguous pairs
4. Offline LLM vocabulary discovery
5. LLM-generated reviewer summaries

---

# 4. Product Principles

- Template detection provides evidence for editorial review, not a misconduct verdict.
- Exact full-abstract and Results reuse must remain visible even when study context is shared.
- Different author groups are context, not template evidence.
- Common assays and endpoints cannot independently create high-confidence findings.
- Semantic and LLM-based methods must not replace deterministic evidence.
- Every pair and family decision must be reproducible and traceable.
- Feature, vocabulary, rule, threshold, model, and output versions must be recorded.
- Companion analyses should remain visible but should not be mixed into suspicious template families.
