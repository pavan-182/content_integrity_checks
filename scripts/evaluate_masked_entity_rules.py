from __future__ import annotations

import argparse
import csv
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.entity_evaluation import evaluate_entities
from content_integrity.entity_extraction import extract_typed_entities
from content_integrity.utils import normalize_whitespace


TYPE_NAMES = {
    "number": "Other numeric value", "percent": "Percentage", "disease": "Disease/cancer",
    "treatment_class": "Treatment class", "endpoint": "Clinical endpoint", "biomarker": "Biomarker",
    "pvalue": "P-value", "drug": "Drug", "protein": "Protein", "date": "Date",
    "gene": "Gene", "registry": "Registry/database", "assay": "Assay", "population": "Population",
    "trial_id": "Trial registration ID", "cell_line": "Cell line", "pathway": "Pathway",
}


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _abstracts(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    output = {}
    for metadata in (item for item in root.iter() if _local_name(item) == "article-meta"):
        record_id = next((
            normalize_whitespace("".join(item.itertext()))
            for item in metadata.iter()
            if _local_name(item) == "article-id" and item.attrib.get("custom-type") == "abstract-id"
        ), "")
        abstract = next((item for item in metadata.iter() if _local_name(item) == "abstract"), None)
        if record_id and abstract is not None:
            output[record_id] = normalize_whitespace(" ".join(abstract.itertext()))
    return output


def _gold_entities(text: str, values: list[dict[str, str]]) -> tuple[list[dict[str, object]], int]:
    entities, cursor, unmatched = [], 0, 0
    for item in values:
        value = item["text"]
        start = text.find(value, cursor)
        if start < 0:
            unmatched += 1
            continue
        entities.append({"start": start, "end": start + len(value), "entity_type": item["entity_type"]})
        cursor = start + len(value)
    return entities, unmatched


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic entity rules against masked ASCO reference labels.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--masked-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    args = parser.parse_args()
    texts, rows = _abstracts(args.input_xml), json.loads(args.masked_json.read_text(encoding="utf-8"))
    pairs, unmatched, missing_records = [], 0, 0
    for row in rows:
        record_id = row["masked_abstract"].split(maxsplit=1)[0]
        text = texts.get(record_id)
        if text is None:
            missing_records += 1
            continue
        gold, skipped = _gold_entities(text, row["entities"])
        unmatched += skipped
        predicted = [
            {"start": entity.start, "end": entity.end, "entity_type": TYPE_NAMES.get(entity.entity_type, entity.entity_type)}
            for entity in extract_typed_entities(text)
        ]
        pairs.append((predicted, gold))
    report = evaluate_entities(pairs)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        fields = ["entity_type", "tp", "fp", "fn", "precision", "recall", "f1"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({"entity_type": name, **metrics} for name, metrics in report.items())
    print(json.dumps({
        "records": len(pairs), "unmatched_reference_entities": unmatched,
        "missing_records": missing_records, "overall": report["overall"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
