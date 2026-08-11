from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.detectors.entity_normalized_template import (
    _representation,
    _rare_title_candidate_pairs,
    _rare_title_tokens,
    _section_candidate_pairs,
    _title_candidate_pairs,
    detect_entity_normalized_templates,
)
from content_integrity.template_matching_common import _candidate_pairs
from content_integrity.xml_parser import parse_xml_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Run entity-template detection over a candidate slice.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--stop", type=int, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    records = parse_xml_records(args.input_xml)
    lookup = {record.record_id: record for record in records}
    representations = {record_id: _representation(record) for record_id, record in lookup.items()}
    candidates = _candidate_pairs(
        records,
        {record_id: value.skeleton for record_id, value in representations.items()},
        {record_id: value.normalized for record_id, value in representations.items()},
    ) | _section_candidate_pairs(representations) | _title_candidate_pairs(records) | _rare_title_candidate_pairs(records)
    title_token_map = _rare_title_tokens(records)
    selected = sorted(candidates)[args.start:args.stop]
    rows = []
    for left_id, right_id in selected:
        rows.extend(
            {
                "record_id": item.record_id,
                "matched_record_id": item.matched_record_id,
                "match_type": item.match_type,
                "confidence": item.confidence,
                "review_status": item.review_status,
                "evidence": item.evidence,
            }
            for item in detect_entity_normalized_templates(
                [lookup[left_id], lookup[right_id]], rare_title_tokens=title_token_map
            )
        )
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["record_id", "matched_record_id", "match_type", "confidence", "review_status", "evidence"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"candidates={len(candidates)} selected={len(selected)} predictions={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
