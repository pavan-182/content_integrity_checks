from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.template_features import build_template_features
from content_integrity.xml_parser import parse_xml_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Export versioned ASCO template features as JSONL.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--output-jsonl", type=Path, required=True)
    args = parser.parse_args()
    records = parse_xml_records(args.input_xml)
    with args.output_jsonl.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(build_template_features(record).to_dict(), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    print(json.dumps({"records": len(records), "output": str(args.output_jsonl)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
