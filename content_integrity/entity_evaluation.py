from __future__ import annotations

from collections import Counter
from typing import Any


def entity_key(entity: dict[str, Any]) -> tuple[int, int, str]:
    return int(entity["start"]), int(entity["end"]), str(entity["entity_type"])


def evaluate_entities(rows: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]) -> dict[str, dict[str, int | float]]:
    """Score exact span-and-type matches; callers validate reviewer JSON separately."""
    counts: dict[str, Counter[str]] = {"overall": Counter()}
    for predicted, gold in rows:
        predicted_keys = {entity_key(item) for item in predicted}
        gold_keys = {entity_key(item) for item in gold}
        for key in predicted_keys & gold_keys:
            counts.setdefault(key[2], Counter())["tp"] += 1
            counts["overall"]["tp"] += 1
        for key in predicted_keys - gold_keys:
            counts.setdefault(key[2], Counter())["fp"] += 1
            counts["overall"]["fp"] += 1
        for key in gold_keys - predicted_keys:
            counts.setdefault(key[2], Counter())["fn"] += 1
            counts["overall"]["fn"] += 1
    result: dict[str, dict[str, int | float]] = {}
    for entity_type, count in sorted(counts.items()):
        tp, fp, fn = count["tp"], count["fp"], count["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        result[entity_type] = {
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        }
    return result
