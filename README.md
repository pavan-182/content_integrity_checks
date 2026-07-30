# ASCO Content Integrity POC

The pipeline parses ASCO XML, runs deterministic content-integrity checks, optionally uses the configured GPT-OSS endpoint for semantic discovery and validation, and writes CSV, JSONL, and Excel review outputs.

```bash
python -m pip install -r requirements.txt
python scripts/run_pipeline.py --input-dir WILEY_LIVE_PREFLIGHT_metadata_files --output-dir outputs
```

LLM response-trace semantic discovery and validation are separate, opt-in features:

```bash
python scripts/run_pipeline.py --detect-llm-semantic --validate-llm
```

Do not enable either model-backed option for ASCO content until endpoint privacy and retention approval is complete. See [LLM Response Trace Pipeline](docs/LLM_RESPONSE_TRACE_PIPELINE.md) and [Pipeline Workflow](docs/pipeline_workflow.md).
