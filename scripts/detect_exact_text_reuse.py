from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asco_integrity.detectors.exact_text_reuse import (
    detect_exact_text_reuse,
)
from asco_integrity.reporting import write_csv
from asco_integrity.utils import dedupe_records
from asco_integrity.xml_parser import (
    discover_xml_files,
    parse_wiley_xml_records,
)

CSV_COLUMNS = [
    "pair_id",
    "check_type",
    "check_triggered",
    "evidence",
    "severity",
    "confidence",
    "matched_source_type",
    "matched_source_id",
    "review_reason",
    "record_id",
    "matched_record_id",
    "source_file",
    "matched_source_file",
    "title",
    "matched_title",
    "match_type",
    "matched_sentence_count",
    "shared_text_coverage",
    "matched_sections",
    "relationship_context",
    "review_status",
    "matched_text_blocks",
    "record_matched_sentences",
    "matched_record_sentences",
]




def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect exact and substantial text reuse between XML abstracts."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)

    parser.add_argument("--min-sentence-words", type=int, default=10)
    parser.add_argument("--min-shared-sentences", type=int, default=2)
    parser.add_argument("--min-shared-coverage", type=float, default=0.15)
    parser.add_argument("--min-shared-block-words", type=int, default=30)
    parser.add_argument("--max-sentence-frequency", type=int, default=10)

    args = parser.parse_args()

    xml_files = discover_xml_files(args.input_dir)
    if not xml_files:
        parser.error(f"No XML files found in {args.input_dir}")

    records = [
        record
        for path in xml_files
        for record in parse_wiley_xml_records(path)
    ]

    records, warnings = dedupe_records(records)

    comparable_records = [
        record
        for record in records
        if record.parse_status != "failed"
        and record.abstract_text.strip()
    ]

    if len(comparable_records) < 2:
        parser.error("At least two usable abstracts are required.")

    findings = detect_exact_text_reuse(
        comparable_records,
        min_sentence_words=args.min_sentence_words,
        min_shared_sentences=args.min_shared_sentences,
        min_shared_coverage=args.min_shared_coverage,
        min_shared_block_words=args.min_shared_block_words,
        max_sentence_frequency=args.max_sentence_frequency,
    )

    findings = sorted(
        findings,
        key=lambda finding: (
            finding.record_id,
            finding.matched_record_id,
            finding.match_type,
        ),
    )

    write_csv(
        args.output_csv,
        [finding.to_dict() for finding in findings],
        CSV_COLUMNS,
    )

    unique_pairs = {
        tuple(sorted((finding.record_id, finding.matched_record_id)))
        for finding in findings
    }

    print(
        f"xml_files={len(xml_files)} "
        f"records={len(records)} "
        f"comparable_records={len(comparable_records)} "
        f"pairs={len(unique_pairs)} "
        f"findings={len(findings)} "
        f"dedupe_warnings={len(warnings)} "
        f"output={args.output_csv}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
