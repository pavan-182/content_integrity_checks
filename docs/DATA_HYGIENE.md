# Tracked Data Inventory

Inspected at baseline commit `f5d93b14a72feb4e77ff64a42c831014ec689d0c` during Phase 1.
No tracked dataset or generated report was deleted or rewritten.
Counts below describe top-level JSON entries, not a validated count of source publications.

| Artifact | Bytes | Entries | Classification |
|---|---:|---:|---|
| `initial_input/final_json.json` | 21,016,139 | 18 | Imported/generated review data |
| `outputs/frontend_run/final_json.json` | 41,966,140 | 30 | Generated/imported authorship report tracked despite `outputs/` ignore rule |
| `initial_input/content_integrity_results.json` | 149,897 | 18 | Generated content-integrity report |
| `🤷_tortured.csv` | 497,525 | n/a | Runtime phrase dictionary, not a generated test fixture |
| `tests/fixtures/eval_corpus/` | small XML/JSON files | n/a | Existing synthetic regression corpus and labels |
| `tests/fixtures/eval_baseline.json`, `scale_baseline.json` | small JSON files | n/a | Existing recorded evaluation/benchmark baselines; `scale_baseline.json` is a historical aggregate from the pre-Phase 2 benchmark |
| `template_detection_low_level.html`, `.png`, `docs/assets/template_detection_pipeline.png` | documentation assets | n/a | Design/reference artifacts, not execution outputs |

## Provenance Findings

The large JSON artifacts contain publication/review metadata, including author, affiliation, DOI, and retraction-related fields.
Their paths, schema, and Git history indicate report/import use; this does not establish a source license or redistribution permission.
The last baseline change touching the report paths was commit `b2f02e58d45ecfd1c5037280948a8dda63b733c7` (`UI fixes`).
No tracked LICENSE, NOTICE, or provenance manifest was found for these artifacts or the phrase dictionary.
Existing documents describe a Wiley seed dictionary and historical publication corpora, but do not establish acquisition dates, licenses, owners, or inclusion approval for these exact files.
The synthetic fixture paths are explicitly allowed through `.gitignore`; ordinary manuscript XML, gateway caches, and output directories are ignored.
Ignoring a path does not remove files already tracked there.

## Approval-Dependent Cleanup

The repository owner should confirm source, custodian, permitted use, license, and inclusion approval for each report and the runtime dictionary.
After approval, move necessary reference data to access-controlled storage and replace runtime/demo dependencies with synthetic fixtures.
The Phase 1 frontend test now uses a tiny synthetic fixture, and the pipeline does not automatically load the tracked authorship report.
Removal from the current tree and any Git-history rewrite are separate decisions; neither was performed.
Record approved locations and checksums in a provenance manifest without copying publication content into it.
Retain the phrase dictionary until its supported runtime replacement and provenance have been reviewed.
Keep the existing evaluation and scale baselines; Phase 1 does not regenerate or claim new scale evidence from them.

New Phase 1 tests generate synthetic inputs in temporary directories or use the small checked-in frontend fixture.
No credentials, manuscripts, production responses, or generated execution reports were added to version control.

## Phase 2 Test Data

Phase 2 scale evidence uses `scripts/generate_load_dataset.py`, whose text comes only from the grammar in that script.
The earlier `scripts/benchmark_scale.py` replicated and perturbed real XML to build its corpus; it now runs named datasets only and never copies manuscript text.
Generated datasets, benchmark reports, checkpoints, and run outputs are written under `outputs/` or temporary directories and are not committed.
The tracked `tests/fixtures/scale_baseline.json` was left unchanged as a historical aggregate and is not Phase 2 evidence.
Benchmark and run reports contain aggregate counts, timings, identifiers, and hashes, not abstract text.

