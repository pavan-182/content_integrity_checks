from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.candidate_routes import generate_candidate_pairs
from content_integrity.xml_parser import parse_xml_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ASCO candidate pairs with their retrieval routes.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    pairs = [pair.to_dict() for pair in generate_candidate_pairs(parse_xml_records(args.input_xml))]
    fields = ["left_record_id", "right_record_id", "routes", "route_count"]
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(pairs)
    print(f"candidate_pairs={len(pairs)} output={args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
