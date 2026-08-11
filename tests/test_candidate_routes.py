from __future__ import annotations

import unittest

from content_integrity.candidate_routes import generate_candidate_pairs
from content_integrity.models import ParsedRecord


class CandidateRouteTests(unittest.TestCase):
    def test_title_and_exact_masked_body_routes_are_retained(self) -> None:
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", title="Outcomes of EGFR in lung cancer", abstract_text="50 patients received abemaciclib."),
            ParsedRecord(source_file="x.xml", record_id="B", title="Outcomes of KRAS in breast cancer", abstract_text="60 patients received ribociclib."),
        ]
        pairs = generate_candidate_pairs(records)
        self.assertEqual(len(pairs), 1)
        self.assertIn("title_template", pairs[0].routes)
        self.assertIn("exact_masked_body", pairs[0].routes)


if __name__ == "__main__":
    unittest.main()
