from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from content_integrity.entity_evaluation import evaluate_entities
from content_integrity.entity_extraction import extract_typed_entities
from scripts.evaluate_masked_entity_rules import TYPE_NAMES, _abstracts, _gold_entities


MODEL_TYPES = {
    "Gene_or_gene_product": "Gene",
    "Cancer": "Disease/cancer",
    "Pathological_formation": "Disease/cancer",
    "Simple_chemical": "Drug",
    "Cell": "Cell line",
}
def _merge(deterministic: list[dict], model: list[dict], threshold: float) -> list[dict]:
    model = [item for item in model if item["score"] >= threshold and item["entity_type"] in MODEL_TYPES]
    deterministic_spans = [(item["start"], item["end"]) for item in deterministic]
    merged = list(deterministic)
    merged.extend(
        {**item, "entity_type": MODEL_TYPES[item["entity_type"]]}
        for item in model
        if not any(item["start"] < end and item["end"] > start for start, end in deterministic_spans)
    )
    selected = []
    for item in sorted(merged, key=lambda value: (value["start"], -(value["end"] - value["start"]))):
        if not any(item["start"] < other["end"] and item["end"] > other["start"] for other in selected):
            selected.append(item)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark PubMedBERT NER with the deterministic ASCO masker.")
    parser.add_argument("--input-xml", type=Path, required=True)
    parser.add_argument("--masked-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--model", default="judithrosell/BioNLP13CG_PubMedBERT_NER")
    parser.add_argument("--thresholds", type=float, nargs="+", default=[0.0, 0.5, 0.7, 0.8, 0.9])
    args = parser.parse_args()

    os.environ["ASCO_NER_DISABLED"] = "1"
    texts = _abstracts(args.input_xml)
    rows = json.loads(args.masked_json.read_text(encoding="utf-8"))
    samples, unmatched = [], 0
    for row in rows:
        record_id = row["masked_abstract"].split(maxsplit=1)[0]
        text = texts.get(record_id)
        if text is None:
            continue
        gold, skipped = _gold_entities(text, row["entities"])
        unmatched += skipped
        deterministic = [
            {"start": item.start, "end": item.end, "entity_type": TYPE_NAMES.get(item.entity_type, item.entity_type)}
            for item in extract_typed_entities(text)
        ]
        samples.append((record_id, text, deterministic, gold))

    from transformers import BertTokenizerFast, pipeline
    tokenizer = BertTokenizerFast.from_pretrained(args.model)
    tokenizer.model_max_length = 512
    ner = pipeline("token-classification", model=args.model, tokenizer=tokenizer, aggregation_strategy="simple", device=-1)
    predictions = ner([item[1] for item in samples], batch_size=4, stride=64)
    model_rows = [[{
        "start": int(item["start"]), "end": int(item["end"]),
        "entity_type": item["entity_group"], "score": float(item["score"]),
    } for item in prediction] for prediction in predictions]

    output = []
    summaries = {}
    for threshold in args.thresholds:
        report = evaluate_entities([
            (_merge(sample[2], prediction, threshold), sample[3])
            for sample, prediction in zip(samples, model_rows)
        ])
        summaries[str(threshold)] = report["overall"]
        output.extend({"threshold": threshold, "entity_type": name, **metrics} for name, metrics in report.items())

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["threshold", "entity_type", "tp", "fp", "fn", "precision", "recall", "f1"])
        writer.writeheader()
        writer.writerows(output)
    print(json.dumps({"model": args.model, "records": len(samples), "unmatched_reference_entities": unmatched, "overall_by_threshold": summaries}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
