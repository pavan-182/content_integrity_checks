from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asco_integrity.detectors.design_contradiction import (
    LLMDesignContradictionValidator,
    detect_design_contradictions,
)
from asco_integrity.reporting import write_csv
from asco_integrity.utils import dedupe_records
from asco_integrity.validators.context_validator import build_gpt_oss_client
from asco_integrity.xml_parser import discover_xml_files, parse_xml_records


CSV_COLUMNS = [
    "finding_id", "check_type", "check_triggered", "record_id", "source_file",
    "title", "contradiction_type", "evidence", "severity", "confidence",
    "matched_source_type", "matched_source_id", "review_reason", "attribute_1",
    "value_1", "section_1", "sentence_1", "attribute_2", "value_2", "section_2",
    "sentence_2", "validation_status", "validation_reason", "review_status",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect incompatible study-design descriptions in XML abstracts."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--validate-llm", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()

    xml_files = discover_xml_files(args.input_dir)
    if not xml_files:
        parser.error(f"No XML files found in {args.input_dir}")
    records, warnings = dedupe_records([
        record for path in xml_files for record in parse_xml_records(path)
    ])
    comparable = [
        record
        for record in records
        if record.parse_status != "failed" and (record.title.strip() or record.abstract_text.strip())
    ]
    validator = (
        LLMDesignContradictionValidator(build_gpt_oss_client(args.env_file))
        if args.validate_llm
        else None
    )
    findings = detect_design_contradictions(comparable, validator=validator)
    write_csv(args.output_csv, [finding.to_dict() for finding in findings], CSV_COLUMNS)
    print(
        f"xml_files={len(xml_files)} records={len(records)} "
        f"comparable_records={len(comparable)} findings={len(findings)} "
        f"dedupe_warnings={len(warnings)} output={args.output_csv}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
