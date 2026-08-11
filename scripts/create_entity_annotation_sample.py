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

from content_integrity.entity_extraction import extract_typed_entities
from content_integrity.xml_parser import parse_xml_records


TARGET_TYPES = {
    "gene", "protein", "mirna", "lncrna", "drug", "disease", "biomarker", "trial_id",
    "date", "percent", "pvalue", "number", "pathway", "cell_line", "assay", "endpoint",
    "registry", "population", "treatment_class",
}


def _sections(paths: list[Path]) -> list[dict[str, object]]:
    rows = []
    for path in paths:
        source = path.name
        for record in parse_xml_records(path):
            for index, section in enumerate(record.abstract_sections):
                text = section["text"]
                entities = extract_typed_entities(text, section["section"])
                rows.append({
                    "sample_id": f"{path.stem}:{record.record_id}:{index}",
                    "source_file": source, "record_id": record.record_id,
                    "section_index": index, "section": section["section"], "text": text,
                    "predicted": [entity.to_dict() for entity in entities],
                })
    return rows


def select_sections(rows: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    """Deterministic greedy coverage of types, then section labels and remaining rows."""
    selected, seen_ids, covered, section_counts = [], set(), set(), Counter()
    while len(selected) < min(limit, len(rows)):
        def score(row: dict[str, object]) -> tuple[int, int, int, str]:
            types = {item["entity_type"] for item in row["predicted"]} & TARGET_TYPES
            new_types = len(types - covered)
            section_bonus = 1 if section_counts[row["section"]] == 0 else 0
            breast_bonus = 1 if row["source_file"] == "Breast_Cancer_Metastatic_publication.xml" else 0
            return new_types, section_bonus, breast_bonus, str(row["sample_id"])
        candidate = max((row for row in rows if row["sample_id"] not in seen_ids), key=score, default=None)
        if candidate is None:
            break
        selected.append(candidate)
        seen_ids.add(candidate["sample_id"])
        covered.update(item["entity_type"] for item in candidate["predicted"])
        section_counts[candidate["section"]] += 1
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a deterministic ASCO entity-annotation sample.")
    parser.add_argument("--input-xml", type=Path, action="append", required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    selected = select_sections(_sections(args.input_xml), args.limit)
    fields = ["sample_id", "source_file", "record_id", "section_index", "section", "text", "predicted_entities_json", "gold_entities_json", "review_status", "reviewer_notes"]
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in selected:
            writer.writerow({**{key: row[key] for key in fields[:6]}, "predicted_entities_json": json.dumps(row["predicted"], ensure_ascii=False), "gold_entities_json": "", "review_status": "pending", "reviewer_notes": ""})
    covered = sorted({item["entity_type"] for row in selected for item in row["predicted"]})
    print(json.dumps({"sections": len(selected), "covered_types": covered, "missing_target_types": sorted(TARGET_TYPES - set(covered))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
