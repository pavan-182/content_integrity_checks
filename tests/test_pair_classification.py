from __future__ import annotations

import unittest

from content_integrity.evidence_scoring import TieredEvidenceScore
from content_integrity.pair_classification import classify_pair
from content_integrity.study_context import StudyContextComparison


def _score(*primary: str) -> TieredEvidenceScore:
    return TieredEvidenceScore("A", "B", primary, (), (), 1.0 if primary else 0.0, 0.0, 1.0 if primary else 0.0, "primary" if primary else "none")


def _context(interpretation: str) -> StudyContextComparison:
    return StudyContextComparison("A", "B", (), (), (), (), (), (), (), (), (), 0, 0, False, False, "unknown", False, "different", False, interpretation)


class PairClassificationTests(unittest.TestCase):
    def test_rule_precedence_and_primary_gate(self) -> None:
        self.assertEqual(classify_pair(_score(), _context("likely_companion_analysis")).pair_class, "insufficient_evidence")
        self.assertEqual(classify_pair(_score("exact_results_section"), _context("likely_companion_analysis")).pair_class, "possible_related_duplicate")
        self.assertEqual(classify_pair(_score("substantial_shared_original_block"), _context("likely_companion_analysis")).pair_class, "possible_companion_analysis")
        self.assertEqual(classify_pair(_score("strong_masked_body_with_original_support"), _context("no_structured_context")).pair_class, "possible_template_reuse")


if __name__ == "__main__":
    unittest.main()
