from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.entity_extraction import mask_text, validate_masking
from content_integrity.xml_parser import parse_xml_records


def main() -> int:
    parser = argparse.ArgumentParser(description="Export and validate hybrid masked ASCO abstract sections.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--validation-csv", type=Path, required=True)
    args = parser.parse_args()
    rows, checks, entity_types = [], [], Counter()
    for record in parse_xml_records(args.input_xml):
        for index, item in enumerate(record.abstract_sections):
            source = item["text"]
            masked, entities = mask_text(source, item["section"])
            errors = validate_masking(source, masked, entities)
            entity_types.update(entity.entity_type for entity in entities)
            rows.append({
                "record_id": record.record_id,
                "section_index": index,
                "section": item["section"],
                "original_text": source,
                "masked_text": masked,
                "entities_json": json.dumps([entity.to_dict() for entity in entities], ensure_ascii=False),
            })
            checks.append({"record_id": record.record_id, "section": item["section"], "valid": not errors, "errors": " | ".join(errors)})
    for path, data, fields in (
        (args.output_csv, rows, list(rows[0]) if rows else []),
        (args.validation_csv, checks, ["record_id", "section", "valid", "errors"]),
    ):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)
    print(json.dumps({"sections": len(rows), "valid_sections": sum(check["valid"] for check in checks), "entity_types": entity_types}, default=dict, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
