# ASCO Template Detection: Planned vs Delivered

**Status date:** 2026-08-11  
**Purpose:** Final implementation and validation handoff  
**Product boundary:** Editorial review aid, not an automatic misconduct, authorship, acceptance, or rejection decision

## 1. Executive summary

The original goal was to turn template detection from a broad masked-similarity check into a transparent, reviewer-auditable workflow for ASCO abstracts. The plan covered reliable XML ingestion, typed biomedical masking, multiple candidate-retrieval routes, direct and entity-substituted reuse evidence, legitimate-study context, pair classification, family clustering, editorial prioritization, and consolidated reporting.

Work Items 1–15 were completed as code, tests, validation utilities, and exported artifacts. Work Item 16 remains deliberately deferred research.

The final repository contains two related paths:

1. **Production batch runner:** `scripts/run_pipeline.py` parses a directory of XML files, runs the production integrity checks, consolidates exact-text and entity-normalized template evidence, builds verified families, assigns record-level risk, and creates CSV/JSONL/Excel reports.
2. **Advanced template work-item toolchain:** Work Items 3–15 add reusable features, routed candidates, evidence tiers, study context, transparent classifications, suspicious-family rules, editorial priorities, enriched reports, entity substitutions, and signal-validation exports. These modules exist and are tested, but the complete Work Item 5–15 chain is not yet wired into the default production runner as one end-to-end path.

The system is usable now as a candidate-generation and editorial-triage tool. High and very-high template candidates are the strongest review queue. Medium local entity-substitution candidates still contain oncology genre noise and require manual review.

## 2. What we originally planned

### Product plan

The V1 product plan was to:

- process approximately 6,000 ASCO XML abstracts;
- detect tortured phrases, explicit LLM response residue, and repeated abstract templates;
- preserve exact source evidence for every flag;
- distinguish suspicious reuse from ordinary oncology wording and legitimate related studies;
- assign explainable editorial-review priority;
- include every abstract, even when it has no findings;
- produce one filterable, sortable, pivot-ready Excel workbook; and
- keep all final editorial decisions with human reviewers.

AI-generated-text authorship detection, scientific-quality review, novelty assessment, and automatic acceptance or rejection were outside the committed product boundary.

### Template-detection plan

The detailed template workflow planned the following stages:

1. Establish a reviewer-labelled evaluation baseline.
2. Parse structured abstracts and retain provenance.
3. Normalize title, body, and sections and extract typed entities.
4. Build one reusable versioned feature object per abstract.
5. Retrieve candidate pairs through body, section, title, and validated research routes.
6. Compare direct reuse and entity-substituted templates.
7. Separate primary, supporting, and contextual evidence.
8. Add legitimate same-study and companion-analysis context.
9. Apply transparent pair classifications.
10. Build verified suspicious families from eligible pair edges.
11. Assign editorial priority without allowing family size or context to inflate evidence.
12. Export reviewer-ready pair, family, abstract, and substitution evidence.
13. Validate molecular-axis, assay-workflow, endpoint, recipe, and semantic routes before production use.
14. Defer stylometry, historical-family matching, gated LLM ambiguity review, vocabulary discovery, and generated summaries until the core system was stable.

## 3. What was delivered

| Work item | Planned capability | Final delivered state |
|---:|---|---|
| 1 | Reviewer-labelled evaluation baseline | Complete. Gold v3 contains 180 reviewed pairs; 178 are automatic labels and 2 require manual review. |
| 2 | Structured XML ingestion and provenance | Complete. Titles, sections, authors, affiliations, trial IDs, source text, offsets, exclusions, parse status, and warnings are retained. |
| 3 | Typed biomedical extraction and masking | Complete as a hybrid extractor. Deterministic rules are always available; SciSpaCy is an optional gap-filler selected with `ASCO_SCISPACY_MODEL`. |
| 4 | Versioned reusable feature object | Complete. Original, normalized, and masked title/body/sections, entities, trial IDs, source hash, and version metadata are exportable as JSONL. |
| 5 | Dedicated title-template comparison | Complete. Exact masked titles and conservative title shingles retrieve candidates without making title-only findings automatically. |
| 6 | Candidate generation and route tracking | Complete. Body, exact original/masked, exact section, and title routes are deduplicated and retained per pair. |
| 7 | Direct pair evidence | Complete. Exact body/Results reuse, copied blocks, and separate title/body/section similarities are exported. |
| 8 | Tiered evidence scoring | Complete. Primary evidence is required; Results/Conclusions receive stronger support; context cannot create a finding. |
| 9 | Same-study and companion context | Complete. Trial, registry, dates, sample, population, treatment, endpoint, author, and affiliation context is exported without raising evidence scores. |
| 10 | Transparent pair classification | Complete. Versioned rules produce template-reuse, related-duplicate, companion, or insufficient-evidence outcomes. |
| 11 | Suspicious family clustering | Complete. Only eligible high-scoring pair classes create edges; representatives and transitive outliers are checked. |
| 12 | Editorial priority | Complete. Auditable evidence bands map to High, Medium, Low, or None; context and family size do not raise priority. |
| 13 | Enriched reporting | Complete. Pair, family, and abstract schemas join routes, evidence, context, classifications, priorities, and family state. |
| 14 | Rich entity substitutions | Complete. Shared, side-specific, and likely substituted typed values retain supporting sentences. |
| 15 | Research-signal validation | Complete. Molecular-axis retrieval was rejected for topic noise; assay workflow and endpoint bundles remain disabled; semantic embeddings remain deferred. |
| 16 | Optional research | Not started by design. Stylometry, historical-family matching, gated LLM review, offline vocabulary discovery, and generated reviewer summaries remain deferred. |

## 4. Final production pipeline

The current default runner is:

```bash
python3 scripts/run_pipeline.py \
  --input-dir INPUT_DIRECTORY \
  --output-dir OUTPUT_DIRECTORY
```

Its default path is local and deterministic. It performs:

- recursive XML discovery and parsing;
- record deduplication and stable identity handling;
- deterministic explicit LLM-response-trace checks;
- tortured-phrase dictionary checks;
- exact-text and rare-phrase template comparison;
- entity-normalized template comparison;
- canonical pair consolidation and verified family clustering;
- numerical and study-design contradiction checks;
- local clinical-trial reference checks;
- record-level risk aggregation; and
- CSV, JSONL, and consolidated Excel export.

Optional semantic LLM checks, nonsense review, context validation, and external ClinicalTrials.gov verification run only when explicitly enabled.

### Production outputs

Each run writes:

- `parsed_records.jsonl` and `parsed_records.csv`;
- `integrity_findings.csv` and `detailed_findings.csv`;
- `template_pair_findings.csv` and `template_clusters.csv`;
- `numerical_contradictions.csv` and `design_contradictions.csv`;
- `trial_verification.csv`;
- `pattern_dictionary.csv`, `parse_warnings.csv`, and `run_metadata.jsonl`; and
- `content_integrity_screening_poc.xlsx`.

Template pair CSV rows are directional for abstract-level review. Two rows normally represent one canonical pair and must not be counted as two independent findings.

## 5. NER decision and final behavior

The intended design was deterministic extraction for stable labels and NER only where oncology entities were difficult or borderline.

The final implementation follows that design conservatively:

- deterministic patterns always extract stable types such as trial IDs, dates, percentages, p-values, numbers, URLs, and emails;
- oncology-aware rules cover genes, proteins, miRNAs, lncRNAs, drugs, diseases, biomarkers, pathways, cell lines, assays, endpoints, registries, populations, and treatment classes;
- SciSpaCy is optional and contributes only mapped entity types;
- deterministic spans are retained when the model is absent or fails to load; and
- uncertain model labels are not guessed into unsupported output types.

SciSpaCy is not active by default. The latest real-ASCO run had no `ASCO_SCISPACY_MODEL` configured, so it used the deterministic/hybrid rule path. This avoids making the batch runner depend on an unavailable or incompatible model.

Entity validation against the supplied 244-abstract masked reference produced:

- 9,041 exact span-and-type matches;
- 69.1% precision;
- 63.0% recall; and
- 65.9% F1.

Structural masking checks passed for all 443 tested sections in the earlier breast-cancer validation corpus.

## 6. The planned 4C research gate and its outcome

Stage 4C in `template_detection_low_level.html` was explicitly planned as research-only supporting evidence. It was not supposed to create production findings until it demonstrated incremental recall without excessive topic similarity.

The validation result was:

| Signal | Candidate pairs | Gold positives | Gold negatives | Incremental positives | Decision |
|---|---:|---:|---:|---:|---|
| Molecular axis | 165 | 12 | 4 | 0 | Rejected for oncology topic noise |
| Assay workflow | 8 | 3 | 0 | 0 | Keep disabled; no incremental value |
| Endpoint bundle | 0 | 0 | 0 | 0 | Keep disabled |
| Semantic embeddings | Not run | — | — | — | Deferred |

This validation gate worked as intended: research signals that looked plausible were not promoted into production without demonstrated value.

## 7. Validation outcomes

### Initial reviewer-labelled baseline

The first baseline deliberately measured the broad candidate detector:

- 84 true positives;
- 25 false positives;
- 0 false negatives;
- 69 true negatives;
- 77.1% precision;
- 100% recall; and
- 87.0% F1.

This established that the initial detector retrieved the known positives but was too permissive.

### Latest production runner on the retracted corpus

The repaired current runner completed on 106 abstracts and generated 81 unique template candidates. Against the reviewed gold set:

- 63 reviewer-supported detections;
- 11 reviewer-rejected false positives;
- 21 missed reviewer-supported pairs;
- 85.1% labelled precision;
- 75.0% recall; and
- 79.7% F1.

The remaining output contained one reviewer-excluded derivative/borderline pair and six unlabelled pairs. Manual inspection found that the six unlabelled pairs were likely ordinary single-sentence oncology boilerplate rather than useful template findings.

Quality by tier was more informative than the overall number:

- all 16 high or very-high predictions covered by the gold set were reviewer-supported;
- all 11 known false positives were medium-confidence local entity-substitution candidates; and
- medium candidates remain a human-review queue, not confirmed findings.

### Latest run on `real_asco_files`

The final production run processed:

- 244 breast local/regional/adjuvant abstracts;
- 275 care-delivery abstracts;
- 519 total records;
- 0 parse warnings or failures;
- 43 unique template candidate pairs represented by 86 directional rows;
- 11 very-high, 20 high, and 12 medium candidates;
- 4 high-confidence candidate families;
- 6 numerical contradiction candidates; and
- 5 trial references requiring manual verification because their registries are unsupported by the V1 automated adapter.

The run completed in 1 minute 52 seconds with approximately 231 MB peak memory. There are no reviewer labels for this new 519-record corpus, so these counts do not establish precision or recall.

Review package:

- `outputs/real_asco_files_pipeline_20260811/content_integrity_screening_poc.xlsx`
- `outputs/real_asco_files_pipeline_20260811/template_pair_findings.csv`
- `outputs/real_asco_files_pipeline_20260811/template_clusters.csv`
- `outputs/real_asco_files_pipeline_20260811/integrity_findings.csv`

## 8. Performance issue found and fixed

The entity-normalized detector originally stalled and was killed while processing the 106-record retracted corpus. The bottleneck was repeated expensive `SequenceMatcher` work across local sentence-window combinations.

The final fix added a cached multiset token-overlap upper bound. A pair is skipped only when that mathematical upper bound cannot reach the existing similarity threshold. This preserves threshold-capable matches while avoiding unnecessary expensive comparisons.

After the fix:

- the entity detector completed the retracted corpus in approximately 10.9 seconds;
- the complete 519-record real-ASCO run completed successfully; and
- 47 focused production tests and the larger 77-test work-item suite passed.

## 9. What was not delivered

The following remain outside the completed production scope:

- independent calibration on a representative held-out ASCO batch;
- demonstrated performance at the full planned scale of approximately 6,000 records;
- stylometric similarity as supporting evidence;
- comparison against a reviewer-approved historical suspicious-template library;
- production semantic-embedding retrieval;
- molecular-axis, assay-workflow, endpoint-bundle, or scientific-recipe findings;
- LLM-generated ambiguity decisions or reviewer summaries in the default run;
- general AI-authorship or AI-generated-text detection;
- automatic scientific correctness, novelty, merit, acceptance, rejection, or misconduct decisions; and
- automatic verification for every international clinical-trial registry.

## 10. Remaining product and engineering work

The shortest responsible next path is:

1. Have reviewers label the 43 real-ASCO candidate pairs, starting with the 11 very-high and 20 high candidates.
2. Use those labels to recalibrate medium local entity-substitution behavior, especially generic one-sentence oncology matches.
3. Decide whether to wire the completed Work Item 5–15 route/evidence/classification modules into `scripts/run_pipeline.py` or keep them as a separate evaluation toolchain.
4. Run a representative scale test before claiming readiness for approximately 6,000 abstracts.
5. Start Work Item 16 only if reviewer evidence shows a specific recall gap that the existing direct and entity-normalized methods cannot cover.

## 11. Final assessment

What was planned was a transparent, high-precision, reviewer-first ASCO template-detection system. What was delivered is a functioning local production batch pipeline plus a broader set of tested template-research and reporting modules.

The strongest current findings are useful for editorial triage, the output is auditable, the full Excel deliverable exists, and the pipeline now runs on real multi-file ASCO input. It is not yet an automatic finding system: medium entity-normalized matches remain noisy, the newest real corpus is unlabelled, full-scale performance is unproven, and the advanced Work Item 5–15 chain still needs a product decision before being integrated into the default runner.
