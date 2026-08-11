from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path


POSITIVE_VERDICTS = {
    "Confirmed template reuse",
    "Probable template reuse",
    "Probable related/derivative reuse",
    "Probable template reuse / related duplicate",
}

NEGATIVE_VERDICTS = {
    "No suspicious template reuse",
    "Confirmed negative control",
    "Generic genre-template similarity only",
    "Not supported as template reuse",
    "Legitimate topic continuity; no suspicious template reuse",
    "Generic title formula only",
}

MANUAL_VERDICTS = {
    "Borderline / needs full-text review",
}


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((_normalize_id(left), _normalize_id(right))))


def _normalize_id(value: str) -> str:
    value = value.strip()
    return value.removeprefix("rw")


def _load_gold(path: Path, verdict_column: str, exclude_manual: bool) -> tuple[dict[tuple[str, str], bool], list[tuple[str, str]]]:
    labels: dict[tuple[str, str], bool] = {}
    excluded: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            pair = _pair_key(row["abstract_id_a"], row["abstract_id_b"])
            verdict = row[verdict_column].strip()
            manual = (
                verdict in MANUAL_VERDICTS
                or row.get("recommended_pair_class", "").strip() == "Needs manual review"
            )
            if manual and exclude_manual:
                excluded.append(pair)
            elif verdict in POSITIVE_VERDICTS:
                labels[pair] = True
            elif verdict in NEGATIVE_VERDICTS:
                labels[pair] = False
            else:
                labels[pair] = row.get("pair_class", "").strip() == "Possible template reuse"
    return labels, excluded


def _load_prediction_sets(path: Path) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        if {"record_id", "matched_record_id"} <= fieldnames:
            left_key, right_key = "record_id", "matched_record_id"
        elif {"abstract_id_a", "abstract_id_b"} <= fieldnames:
            left_key, right_key = "abstract_id_a", "abstract_id_b"
        else:
            raise ValueError(
                "Prediction CSV must contain either record_id/matched_record_id or abstract_id_a/abstract_id_b."
            )
        predictions, manual = set(), set()
        for row in reader:
            pair = _pair_key(row[left_key], row[right_key])
            predictions.add(pair)
            if row.get("review_status", "").strip() == "needs_manual_review":
                manual.add(pair)
        return predictions, manual


def _load_predictions(path: Path, include_manual_predictions: bool = False) -> set[tuple[str, str]]:
    predictions, manual = _load_prediction_sets(path)
    return predictions if include_manual_predictions else predictions - manual


@dataclass(frozen=True, slots=True)
class Metrics:
    tp: int
    fp: int
    fn: int
    tn: int
    precision: float
    recall: float
    f1: float


def _score(
    labels: dict[tuple[str, str], bool],
    predictions: set[tuple[str, str]],
    excluded: list[tuple[str, str]],
    *,
    count_unlabelled_as_negative: bool = False,
) -> Metrics:
    tp = fp = fn = tn = 0
    excluded_set = set(excluded)
    for pair, truth in labels.items():
        predicted = pair in predictions
        if truth:
            if predicted:
                tp += 1
            else:
                fn += 1
        else:
            if predicted:
                fp += 1
            else:
                tn += 1
    if count_unlabelled_as_negative:
        fp += len(predictions - set(labels) - excluded_set)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return Metrics(tp=tp, fp=fp, fn=fn, tn=tn, precision=precision, recall=recall, f1=f1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate template-detection pairs against a gold label CSV.")
    parser.add_argument("--gold-csv", type=Path, required=True)
    parser.add_argument("--predictions-csv", type=Path, nargs="+", required=True)
    parser.add_argument("--verdict-column", default="reviewed_verdict")
    parser.add_argument("--include-manual", action="store_true")
    parser.add_argument(
        "--include-manual-predictions",
        action="store_true",
        help="Count needs_manual_review predictions as automatic positives.",
    )
    parser.add_argument(
        "--count-unlabelled-as-negative",
        action="store_true",
        help="Use only when the gold CSV exhaustively labels every possible pair.",
    )
    args = parser.parse_args()

    labels, excluded = _load_gold(args.gold_csv, args.verdict_column, exclude_manual=not args.include_manual)
    loaded = [_load_prediction_sets(path) for path in args.predictions_csv]
    all_predictions = set().union(*(predictions for predictions, _ in loaded))
    manual_predictions = set().union(*(manual for _, manual in loaded))
    predictions = all_predictions if args.include_manual_predictions else all_predictions - manual_predictions
    metrics = _score(
        labels,
        predictions,
        excluded,
        count_unlabelled_as_negative=args.count_unlabelled_as_negative,
    )

    missed = sorted(pair for pair, truth in labels.items() if truth and pair not in predictions)
    unlabelled = predictions - set(labels) - set(excluded)
    false_positive = {pair for pair, truth in labels.items() if not truth and pair in predictions}
    if args.count_unlabelled_as_negative:
        false_positive |= unlabelled

    print(json.dumps({
        "gold_pairs": len(labels),
        "excluded_pairs": len(excluded),
        "predicted_pairs": len(predictions),
        "manual_review_predictions": len(manual_predictions),
        "unlabelled_predictions": len(unlabelled),
        "tp": metrics.tp,
        "fp": metrics.fp,
        "fn": metrics.fn,
        "tn": metrics.tn,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
    }, indent=2, sort_keys=True))
    if missed:
        print(f"missed={missed}")
    if false_positive:
        print(f"false_positives={sorted(false_positive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
