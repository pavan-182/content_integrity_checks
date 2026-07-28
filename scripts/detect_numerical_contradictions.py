from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asco_integrity.detectors.numerical_contradiction import (
    PERCENTAGE_TOLERANCE,
    detect_numerical_contradictions,
)
from asco_integrity.reporting import write_csv
from asco_integrity.utils import dedupe_records
from asco_integrity.xml_parser import discover_xml_files, parse_xml_records


CSV_COLUMNS = [
    "finding_id", "check_type", "check_triggered", "record_id", "source_file",
    "title", "contradiction_type", "evidence", "severity", "confidence",
    "review_reason", "section", "source_sentence", "reported_values",
    "calculated_value", "difference", "tolerance", "review_status",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect high-confidence internal numerical contradictions in XML abstracts."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--percentage-tolerance",
        type=float,
        default=PERCENTAGE_TOLERANCE,
        help="Allowed percentage-point difference for count/percentage rounding.",
    )
    args = parser.parse_args()
    if args.percentage_tolerance < 0:
        parser.error("--percentage-tolerance must be non-negative")

    xml_files = discover_xml_files(args.input_dir)
    if not xml_files:
        parser.error(f"No XML files found in {args.input_dir}")
    records, warnings = dedupe_records([
        record for path in xml_files for record in parse_xml_records(path)
    ])
    comparable = [
        record
        for record in records
        if record.parse_status != "failed" and record.abstract_text.strip()
    ]
    findings = detect_numerical_contradictions(
        comparable,
        percentage_tolerance=args.percentage_tolerance,
    )
    write_csv(args.output_csv, [finding.to_dict() for finding in findings], CSV_COLUMNS)
    print(
        f"xml_files={len(xml_files)} records={len(records)} "
        f"comparable_records={len(comparable)} findings={len(findings)} "
        f"dedupe_warnings={len(warnings)} output={args.output_csv}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
