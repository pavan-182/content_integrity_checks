from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asco_integrity.detectors.entity_normalized_template import (
    MASKED_SIMILARITY_THRESHOLD,
    MAXIMUM_PLACEHOLDER_RATIO,
    MINIMUM_SKELETON_WORDS,
    MINIMUM_SUBSTITUTIONS,
    ORIGINAL_SUPPORT_THRESHOLD,
    detect_entity_normalized_templates,
)
from asco_integrity.reporting import write_csv
from asco_integrity.utils import dedupe_records
from asco_integrity.xml_parser import discover_xml_files, parse_wiley_xml_records


CSV_COLUMNS = [
    "pair_id", "check_type", "check_triggered", "evidence", "severity",
    "confidence", "matched_source_type", "matched_source_id", "review_reason",
    "record_id", "matched_record_id", "source_file", "matched_source_file",
    "title", "matched_title", "match_type", "masked_skeleton_similarity",
    "original_text_similarity", "ngram_similarity", "weighted_section_similarity",
    "high_value_section_similarity", "matched_sections", "variable_substitutions",
    "shared_skeleton_excerpt", "relationship_context", "review_status",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect entity-normalized wording templates between XML abstracts."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--masked-similarity-threshold", type=float, default=MASKED_SIMILARITY_THRESHOLD
    )
    parser.add_argument(
        "--original-support-threshold", type=float, default=ORIGINAL_SUPPORT_THRESHOLD
    )
    parser.add_argument(
        "--minimum-skeleton-words", type=int, default=MINIMUM_SKELETON_WORDS
    )
    parser.add_argument(
        "--maximum-placeholder-ratio", type=float, default=MAXIMUM_PLACEHOLDER_RATIO
    )
    parser.add_argument(
        "--minimum-substitutions", type=int, default=MINIMUM_SUBSTITUTIONS
    )
    args = parser.parse_args()

    xml_files = discover_xml_files(args.input_dir)
    if not xml_files:
        parser.error(f"No XML files found in {args.input_dir}")
    records, warnings = dedupe_records([
        record for path in xml_files for record in parse_wiley_xml_records(path)
    ])
    comparable = [
        record
        for record in records
        if record.parse_status != "failed" and record.abstract_text.strip()
    ]
    if len(comparable) < 2:
        parser.error("At least two usable abstracts are required.")

    findings = detect_entity_normalized_templates(
        comparable,
        masked_similarity_threshold=args.masked_similarity_threshold,
        original_support_threshold=args.original_support_threshold,
        minimum_skeleton_words=args.minimum_skeleton_words,
        maximum_placeholder_ratio=args.maximum_placeholder_ratio,
        minimum_substitutions=args.minimum_substitutions,
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
