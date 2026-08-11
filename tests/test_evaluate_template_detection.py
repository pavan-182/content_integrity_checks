import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "evaluate_template_detection",
    ROOT / "scripts" / "evaluate_template_detection.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

_load_gold = MODULE._load_gold
_load_predictions = MODULE._load_predictions
_score = MODULE._score


class EvaluateTemplateDetectionTests(unittest.TestCase):
    def test_gold_labels_and_prediction_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gold = root / "gold.csv"
            pred = root / "pred.csv"
            with gold.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "pair_id",
                    "abstract_id_a",
                    "abstract_id_b",
                    "reviewed_verdict",
                    "pair_class",
                    "recommended_pair_class",
                ])
                writer.writerow(["G1", "rw1", "2", "Confirmed template reuse", "Possible template reuse", "Possible template reuse"])
                writer.writerow(["G2", "3", "4", "No suspicious template reuse", "Insufficient evidence", "Insufficient evidence"])
                writer.writerow(["G3", "5", "6", "Borderline / needs full-text review", "Possible template reuse", "Needs manual review"])
            with pred.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["record_id", "matched_record_id"])
                writer.writerow(["1", "rw2"])
                writer.writerow(["3", "4"])
                writer.writerow(["7", "8"])
            labels, excluded = _load_gold(gold, "reviewed_verdict", exclude_manual=True)
            predictions = _load_predictions(pred)
            metrics = _score(labels, predictions, excluded)

        self.assertEqual(labels[("1", "2")], True)
        self.assertEqual(labels[("3", "4")], False)
        self.assertEqual(excluded, [("5", "6")])
        self.assertEqual(predictions, {("1", "2"), ("3", "4"), ("7", "8")})
        self.assertEqual((metrics.tp, metrics.fp, metrics.fn, metrics.tn), (1, 1, 0, 0))
        self.assertAlmostEqual(metrics.precision, 1 / 2)
        self.assertAlmostEqual(metrics.recall, 1.0)

    def test_manual_predictions_are_excluded_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pred = Path(directory) / "pred.csv"
            with pred.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["record_id", "matched_record_id", "review_status"])
                writer.writerow(["1", "2", "candidate"])
                writer.writerow(["3", "4", "needs_manual_review"])
            self.assertEqual(_load_predictions(pred), {("1", "2")})
            self.assertEqual(_load_predictions(pred, include_manual_predictions=True), {("1", "2"), ("3", "4")})


if __name__ == "__main__":
    unittest.main()
