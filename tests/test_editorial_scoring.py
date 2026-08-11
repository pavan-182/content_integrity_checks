from __future__ import annotations

import unittest

from content_integrity.editorial_scoring import assign_editorial_priority
from content_integrity.pair_classification import CLASSIFIER_VERSION, PairClassification


def _classification(pair_class: str, score: float) -> PairClassification:
    return PairClassification(CLASSIFIER_VERSION, "A", "B", pair_class, "test", ("evidence",), (), (), "no_structured_context", score, "test")


class EditorialScoringTests(unittest.TestCase):
    def test_priority_bands_and_companion_cap(self) -> None:
        self.assertEqual(assign_editorial_priority(_classification("insufficient_evidence", 0.0)).review_priority, "None")
        self.assertEqual(assign_editorial_priority(_classification("possible_template_reuse", 0.65)).review_priority, "Low")
        self.assertEqual(assign_editorial_priority(_classification("possible_template_reuse", 0.75)).review_priority, "Medium")
        self.assertEqual(assign_editorial_priority(_classification("possible_related_duplicate", 0.85)).review_priority, "High")
        self.assertEqual(assign_editorial_priority(_classification("possible_companion_analysis", 1.0)).review_priority, "Low")


if __name__ == "__main__":
    unittest.main()
