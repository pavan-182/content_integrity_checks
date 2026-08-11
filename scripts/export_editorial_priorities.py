from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.editorial_scoring import EditorialPriority, score_editorial_priorities
from content_integrity.xml_parser import parse_xml_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Export final ASCO template-review priorities.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    rows = [item.to_dict() for item in score_editorial_priorities(parse_xml_records(args.input_xml))]
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        fields = list(rows[0]) if rows else list(EditorialPriority.__dataclass_fields__)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"editorial_priorities={len(rows)} output={args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
