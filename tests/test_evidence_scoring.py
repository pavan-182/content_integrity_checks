from __future__ import annotations

import unittest

from content_integrity.evidence_scoring import _review_score, score_pair_evidence
from content_integrity.models import ParsedRecord


class EvidenceScoringTests(unittest.TestCase):
    def test_supporting_evidence_does_not_create_a_review_score(self) -> None:
        self.assertEqual(_review_score(0.0, 0.25), 0.0)

    def test_primary_evidence_can_use_results_support(self) -> None:
        results = "A total of 50 patients received abemaciclib during treatment."
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", title="A study", abstract_text=results, abstract_sections=[{"section": "Results", "text": results}]),
            ParsedRecord(source_file="x.xml", record_id="B", title="B study", abstract_text=results, abstract_sections=[{"section": "Results", "text": results}]),
        ]
        score = score_pair_evidence(records)[0]
        self.assertIn("exact_results_section", score.primary_evidence)
        self.assertGreater(score.review_score, score.primary_score - 0.01)


if __name__ == "__main__":
    unittest.main()
