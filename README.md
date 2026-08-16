# <######> POC

The pipeline parses XML, runs deterministic checks, optionally uses the configured endpoint for semantic discovery and validation, and writes one canonical JSON report plus one editor-triage workbook.

`content_integrity_results.json` is keyed by normalized DOI (or abstract ID when no DOI is supplied) so its `checks` arrays can be merged directly with authorship-integrity results. Template pairs contain their pair-level sub-detections; numerical, design, and trial checks are nested once as record-level corroborating evidence.

```bash
python -m pip install -r requirements.txt
python scripts/run_pipeline.py --input-dir metadata_files --output-dir outputs
```

LLM response-trace semantic discovery and validation are separate, opt-in features:

```bash
python scripts/run_pipeline.py --detect-llm-semantic --validate-llm
```
