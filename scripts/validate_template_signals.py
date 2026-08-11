from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.signal_validation import load_gold_labels, validate_signals
from content_integrity.xml_parser import parse_xml_records


def _write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate deterministic template signals without enabling them.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--gold-csv", type=Path, required=True)
    parser.add_argument("--summary-csv", type=Path, required=True)
    parser.add_argument("--examples-csv", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    args = parser.parse_args()
    labels, manual = load_gold_labels(args.gold_csv)
    results, examples = validate_signals(parse_xml_records(args.input_xml), labels, manual)
    _write_csv(args.summary_csv, [item.to_dict() for item in results], list(results[0].to_dict()) if results else [])
    _write_csv(args.examples_csv, examples, ["signal", "record_id", "signature", "section", "source_sentence", "bucket_size"])
    args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json.write_text(json.dumps({"labelled_pairs": len(labels), "manual_pairs_excluded": len(manual), "results": [item.to_dict() for item in results]}, indent=2), encoding="utf-8")
    print(f"signals={len(results)} examples={len(examples)} output={args.summary_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
