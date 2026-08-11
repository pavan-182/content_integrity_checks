from __future__ import annotations

import unittest

from content_integrity.family_clustering import build_suspicious_families
from content_integrity.pair_classification import CLASSIFIER_VERSION, PairClassification


def _pair(left: str, right: str, pair_class: str, score: float) -> PairClassification:
    return PairClassification(CLASSIFIER_VERSION, left, right, pair_class, "test", ("evidence",), (), (), "no_structured_context", score, "test")


class FamilyClusteringTests(unittest.TestCase):
    def test_only_strong_eligible_edges_connect_and_transitive_outlier_is_marked(self) -> None:
        rows = build_suspicious_families([
            _pair("A", "B", "possible_template_reuse", 0.9),
            _pair("B", "C", "possible_template_reuse", 0.8),
            _pair("C", "D", "possible_related_duplicate", 0.8),
            _pair("E", "F", "possible_companion_analysis", 1.0),
            _pair("G", "H", "possible_template_reuse", 0.65),
        ])
        self.assertEqual({row.record_id for row in rows}, {"A", "B", "C"})
        self.assertEqual({row.representative_record_id for row in rows}, {"B"})
        self.assertEqual(next(row.member_status for row in rows if row.record_id == "A"), "member")


if __name__ == "__main__":
    unittest.main()
