from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.detectors.llm_trace import candidate_to_finding
from content_integrity.detectors.llm_trace_semantic import (
    DEFAULT_INPUT_TOKEN_BUDGET,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_RECORDS_PER_BATCH,
    MAX_INPUT_TOKEN_BUDGET,
    MAX_OUTPUT_TOKENS,
    PROMPT_VERSION,
    RULES,
    SYSTEM_PROMPT,
    _record_payload,
    _user_prompt,
    analyze_batch,
    detect_semantic_traces,
    estimate_request_tokens,
    estimate_tokens,
    pack_batches,
    validate_model_response,
)
from content_integrity.models import ParsedRecord
from content_integrity.utils import dedupe_records, normalize_whitespace
from content_integrity.validators.context_validator import build_gpt_oss_client
from content_integrity.xml_parser import discover_xml_files, parse_xml_records


CSV_COLUMNS = [
    "finding_id",
    "record_id",
    "source_file",
    "title",
    "rule_id",
    "category",
    "matched_text",
    "evidence_snippet",
    "section_or_field",
    "severity",
    "confidence",
    "reason",
    "model_id",
    "prompt_version",
    "batch_id",
]


def analyze_records(
    client: Any,
    records: list[ParsedRecord],
    *,
    input_token_budget: int = DEFAULT_INPUT_TOKEN_BUDGET,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_records_per_batch: int = DEFAULT_MAX_RECORDS_PER_BATCH,
) -> tuple[list[dict[str, Any]], int]:
    candidates, stats = detect_semantic_traces(
        client,
        records,
        input_token_budget=input_token_budget,
        max_output_tokens=max_output_tokens,
        max_records_per_batch=max_records_per_batch,
    )
    titles = {record.record_id: record.title for record in records}
    rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        finding = candidate_to_finding(candidate)
        rows.append(
            {
                **finding.to_dict(),
                "finding_id": f"LLM-FND-{index:05d}",
                "title": titles.get(candidate.record_id, ""),
                "reason": candidate.reason,
                "model_id": candidate.discovery_model_id,
                "prompt_version": candidate.discovery_prompt_version,
                "batch_id": candidate.discovery_batch_id,
            }
        )
    return rows, stats.batch_count


def write_findings_csv(path: str | Path, rows: list[dict[str, Any]]) -> Path:
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=resolved.parent,
            prefix=f".{resolved.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows({column: row.get(column, "") for column in CSV_COLUMNS} for row in rows)
        os.replace(temporary_path, resolved)
    except Exception:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
        raise
    return resolved


def load_records(input_dir: str | Path) -> tuple[list[ParsedRecord], int]:
    xml_files = discover_xml_files(input_dir)
    if not xml_files:
        raise ValueError(f"No XML files found under {input_dir}")
    records = [record for path in xml_files for record in parse_xml_records(path)]
    failed = [record for record in records if record.parse_status == "failed"]
    if failed:
        raise ValueError(f"Failed to parse {len(failed)} XML record(s)")
    empty = [record for record in records if not normalize_whitespace(f"{record.title} {record.abstract_text}")]
    if empty:
        raise ValueError(f"No inspectable title or abstract text for {len(empty)} record(s)")
    records, warnings = dedupe_records(records)
    return records, len(warnings)


def _bounded_int(name: str, value: int, minimum: int, maximum: int) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect LLM response traces in XML titles and abstracts using GPT-OSS.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-csv", default="outputs/llm_response_traces.csv")
    parser.add_argument("--input-token-budget", type=int, default=DEFAULT_INPUT_TOKEN_BUDGET)
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--max-records-per-batch", type=int, default=DEFAULT_MAX_RECORDS_PER_BATCH)
    args = parser.parse_args(argv)
    try:
        records, dedupe_warning_count = load_records(args.input_dir)
        rows, batch_count = analyze_records(
            build_gpt_oss_client(),
            records,
            input_token_budget=_bounded_int("input-token-budget", args.input_token_budget, 1, MAX_INPUT_TOKEN_BUDGET),
            max_output_tokens=_bounded_int("max-output-tokens", args.max_output_tokens, 1, MAX_OUTPUT_TOKENS),
            max_records_per_batch=_bounded_int("max-records-per-batch", args.max_records_per_batch, 1, 1000),
        )
        output_path = write_findings_csv(args.output_csv, rows)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(json.dumps({"xml_records": len(records), "initial_batches": batch_count, "dedupe_warnings": dedupe_warning_count, "llm_response_traces": len(rows), "output_csv": str(output_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
