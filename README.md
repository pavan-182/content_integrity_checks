# ######<> POC

The pipeline parses XML, runs deterministic checks, optionally uses the configured endpoint for semantic discovery and validation, and writes CSV, JSONL, and Excel review outputs.

```bash
python -m pip install -r requirements.txt
python scripts/run_pipeline.py --input-dir metadata_files --output-dir outputs
```

LLM response-trace semantic discovery and validation are separate, opt-in features:

```bash
python scripts/run_pipeline.py --detect-llm-semantic --validate-llm
```

