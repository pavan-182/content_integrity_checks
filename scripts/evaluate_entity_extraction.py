from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.entity_evaluation import evaluate_entities


def _entities(value: str, sample_id: str, column: str) -> list[dict[str, object]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{sample_id}: invalid {column}: {error.msg}") from error
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise ValueError(f"{sample_id}: {column} must be a JSON list of objects")
    for item in parsed:
        if not {"start", "end", "entity_type"} <= item.keys():
            raise ValueError(f"{sample_id}: {column} entity needs start, end, entity_type")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure exact entity span/type precision and recall from reviewed ASCO samples.")
    parser.add_argument("--annotation-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    pairs = []
    with args.annotation_csv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("review_status") != "complete":
                continue
            pairs.append((_entities(row["predicted_entities_json"], row["sample_id"], "predicted_entities_json"), _entities(row["gold_entities_json"], row["sample_id"], "gold_entities_json")))
    report = evaluate_entities(pairs)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["entity_type", "tp", "fp", "fn", "precision", "recall", "f1"])
        writer.writeheader()
        writer.writerows({"entity_type": name, **metrics} for name, metrics in report.items())
    print(json.dumps({"reviewed_sections": len(pairs), "overall": report.get("overall", {})}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
