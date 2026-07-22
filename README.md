# ASCO Content Integrity Screening POC

This repo contains a deterministic technical feasibility POC for TNQTech Integrity Central using the provided Wiley XML corpus as substitute data.

## What It Does

The pipeline:

- Discovers Wiley XML files
- Parses `article` and `article_set` XML shapes
- Builds normalized records for each file
- Detects explicit LLM response traces
- Detects known tortured / nonsense phrases from a versioned seed dictionary
- Clusters similar abstracts via masked template comparison
- Optionally validates flagged findings with GPT-OSS 20B when `--validate-llm` is enabled
- Aggregates risk at the record level
- Exports CSV, JSONL, and one consolidated Excel workbook

## What It Does Not Do

- It does **not** detect AI-generated authorship
- It does **not** make acceptance or rejection decisions
- It does **not** infer misconduct

## Corpus Observed In This Run

- Total XML files: `761`
- Root schemas: `639` `article`, `122` `article_set`
- Parsed cleanly: `688`
- Parsed with warnings: `73`
- Missing abstract text: `73`
- Abstracts with explicit structured section detection: `168`
- Tortured-phrase findings: `5` findings across `4` records
- Template cluster members: `64` rows across `28` clusters

## Dependencies

- Python `3.11+`
- `lxml`
- `openpyxl`

Install them in a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

From the repo root:

```bash
.venv/bin/python scripts/run_pipeline.py \
  --input-dir WILEY_LIVE_PREFLIGHT_metadata_files \
  --tortured-dictionary "🤷_tortured.csv" \
  --output-dir outputs
```

Add `--validate-llm` to run the optional context validation pass on the flagged findings.

You can also import and call `asco_integrity.pipeline.run_default_pipeline()` from Python.

## Inputs

- `WILEY_LIVE_PREFLIGHT_metadata_files/`: Wiley XML substitute corpus
- `🤷_tortured.csv`: versioned tortured-phrase seed dictionary

## Outputs

Written under `outputs/`:

- `parsed_records.jsonl`
- `parsed_records.csv`
- `integrity_findings.csv`
- `template_clusters.csv`
- `pattern_dictionary.csv`
- `parse_warnings.csv`
- `run_metadata.jsonl`
- `content_integrity_screening_poc.xlsx`

## Workbook Sheets

The workbook includes:

- Data Inventory
- Abstract Summary
- Integrity Findings
- Template Clusters
- Pattern Dictionary
- Parse Warnings
- Run Metadata

## Detector Scope

### LLM Response Trace

Deterministic pattern checks for:

- AI self-identification
- Chatbot response preambles
- Capability / refusal language
- Conversation labels
- Prompt leakage
- Interface residue
- Lightweight markdown residue

### Tortured Phrase

Deterministic phrase matching against a versioned dictionary derived from `🤷_tortured.csv`.

This POC keeps the dictionary conservative by only enabling multiword phrases, which reduces generic false positives.

### Template Detection

Documents are normalized, masked, and compared for structural similarity. The implementation uses blocked pairwise matching, which is practical for the current corpus size.

## Known Limitations

- Wiley XML is substitute data here, not ASCO XML
- Some records have no usable abstract text and are flagged in `parse_warnings.csv`
- The tortured-phrase dictionary is deterministic and seed-based, not a production fingerprint store
- Template clustering is explainable but not embedding-based
- AI-generated text detection remains intentionally out of scope for this POC

## How This Differs From The Eventual ASCO Implementation

- This run uses Wiley files as substitute input
- The final ASCO version will need ASCO-specific XML field mapping and validation
- The eventual production dictionary for tortured phrases should be curated against ASCO language and editorial policy
- The production system may need stronger performance tuning and additional QA controls
