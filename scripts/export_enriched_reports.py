from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.enriched_reporting import (
    ABSTRACT_REPORT_COLUMNS,
    FAMILY_REPORT_COLUMNS,
    PAIR_REPORT_COLUMNS,
    build_enriched_reports,
)
from content_integrity.reporting import write_csv
from content_integrity.xml_parser import parse_xml_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Export consolidated ASCO pair, family, and abstract reports.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--pairs-csv", type=Path, required=True)
    parser.add_argument("--families-csv", type=Path, required=True)
    parser.add_argument("--abstracts-csv", type=Path, required=True)
    args = parser.parse_args()
    pair_rows, family_rows, abstract_rows = build_enriched_reports(parse_xml_records(args.input_xml))
    write_csv(args.pairs_csv, pair_rows, PAIR_REPORT_COLUMNS)
    write_csv(args.families_csv, family_rows, FAMILY_REPORT_COLUMNS)
    write_csv(args.abstracts_csv, abstract_rows, ABSTRACT_REPORT_COLUMNS)
    print(f"pair_rows={len(pair_rows)} family_rows={len(family_rows)} abstract_rows={len(abstract_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
