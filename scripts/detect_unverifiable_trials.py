from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asco_integrity.detectors.unverifiable_trial import (
    CLINICAL_TRIALS_GOV,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    ClinicalTrialsGovClient,
    detect_unverifiable_trials,
)
from asco_integrity.reporting import write_csv
from asco_integrity.utils import dedupe_records
from asco_integrity.xml_parser import discover_xml_files, parse_wiley_xml_records


CSV_COLUMNS = [
    "finding_id", "check_type", "check_triggered", "evidence", "severity",
    "confidence", "matched_source_type", "matched_source_id", "review_reason",
    "record_id", "source_file", "title", "finding_type", "verification_status",
    "registry_name", "raw_trial_id", "normalized_trial_id", "format_valid",
    "source_section", "source_sentence", "lookup_status", "external_record_id",
    "external_title", "external_study_type", "external_phase",
    "external_recruitment_status", "external_conditions", "external_interventions",
    "external_enrollment", "source_record_url", "cache_hit", "operational_error",
    "review_status",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify reported trial registrations against authoritative registries."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".trial_registry_cache"),
        help="Directory for successful and confirmed-not-found registry responses.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    parser.add_argument(
        "--max-cache-age-seconds",
        type=float,
        default=None,
        help="Optional cache age limit; the default keeps valid entries indefinitely.",
    )
    parser.add_argument(
        "--offline-cache-only",
        action="store_true",
        help="Never access the network; uncached IDs are reported as operational lookup failures.",
    )
    parser.add_argument(
        "--findings-only",
        action="store_true",
        help="Write only rows where check_triggered is true.",
    )
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.max_retries < 0:
        parser.error("--max-retries must be non-negative")
    if args.max_cache_age_seconds is not None and args.max_cache_age_seconds < 0:
        parser.error("--max-cache-age-seconds must be non-negative")

    xml_files = discover_xml_files(args.input_dir)
    if not xml_files:
        parser.error(f"No XML files found in {args.input_dir}")
    records, warnings = dedupe_records([
        record for path in xml_files for record in parse_wiley_xml_records(path)
    ])
    comparable = [
        record
        for record in records
        if record.parse_status != "failed" and (record.title.strip() or record.abstract_text.strip())
    ]
    client = ClinicalTrialsGovClient(
        cache_dir=args.cache_dir,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        offline_cache_only=args.offline_cache_only,
        max_cache_age_seconds=args.max_cache_age_seconds,
    )
    results = detect_unverifiable_trials(
        comparable,
        registry_clients={CLINICAL_TRIALS_GOV: client},
    )
    rows = [
        result.to_dict()
        for result in results
        if not args.findings_only or result.check_triggered
    ]
    write_csv(args.output_csv, rows, CSV_COLUMNS)
    print(
        f"xml_files={len(xml_files)} records={len(records)} "
        f"comparable_records={len(comparable)} verification_rows={len(rows)} "
        f"triggered={sum(result.check_triggered for result in results)} "
        f"dedupe_warnings={len(warnings)} output={args.output_csv}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
