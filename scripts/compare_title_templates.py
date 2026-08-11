from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.template_features import build_template_features
from content_integrity.title_templates import TitleTemplateMatch, compare_title_templates
from content_integrity.xml_parser import parse_xml_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve and compare masked ASCO title-template candidates.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    features = [build_template_features(record) for record in parse_xml_records(args.input_xml)]
    matches = [match.to_dict() for match in compare_title_templates(features)]
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        fields = list(matches[0]) if matches else list(TitleTemplateMatch.__dataclass_fields__)
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(matches)
    print(f"records={len(features)} title_candidates={len(matches)} output={args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
