from __future__ import annotations

import unittest

from content_integrity.candidate_routes import generate_candidate_pairs
from content_integrity.models import ParsedRecord
from content_integrity.template_matching_common import _candidate_pairs


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

    def test_pathological_approximate_buckets_are_skipped(self) -> None:
        common = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
        records = [
            ParsedRecord("x.xml", record_id=f"R{index}", abstract_text=f"{common} unique{index}")
            for index in range(51)
        ]
        texts = {record.record_id: record.abstract_text for record in records}
        self.assertEqual(_candidate_pairs(records, texts, texts), set())

    def test_exact_matches_are_not_lost_when_bucket_is_large(self) -> None:
        records = [
            ParsedRecord("x.xml", record_id=f"R{index}", abstract_text="identical exact abstract")
            for index in range(51)
        ]
        pairs = generate_candidate_pairs(records)
        self.assertEqual(len(pairs), 51 * 50 // 2)
        self.assertTrue(all("exact_original_body" in pair.routes for pair in pairs))


if __name__ == "__main__":
    unittest.main()
