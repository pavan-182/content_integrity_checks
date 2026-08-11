from __future__ import annotations

import unittest

from content_integrity.enriched_reporting import build_enriched_reports, directional_finding_rows
from content_integrity.models import ParsedRecord


class EnrichedReportingTests(unittest.TestCase):
    def test_pair_family_and_abstract_reports_join_completed_outputs(self) -> None:
        text = "A total of 50 patients received abemaciclib during treatment."
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", title="Study A", abstract_text=text),
            ParsedRecord(source_file="x.xml", record_id="B", title="Study B", abstract_text=text),
        ]
        pairs, families, abstracts = build_enriched_reports(records)
        self.assertEqual(pairs[0]["pair_class"], "possible_template_reuse")
        self.assertEqual(pairs[0]["review_priority"], "High")
        self.assertEqual(families, [])
        self.assertEqual({row["record_id"] for row in abstracts}, {"A", "B"})

        directional = directional_finding_rows(pairs)
        self.assertEqual(len(directional), 2)
        self.assertEqual({row["pair_id"] for row in directional}, {"PAIR-A--B"})
        self.assertEqual(
            {(row["left_record_id"], row["right_record_id"]) for row in directional},
            {("A", "B"), ("B", "A")},
        )

    def test_directional_findings_exclude_insufficient_candidates(self) -> None:
        self.assertEqual(directional_finding_rows([{"review_priority": "None"}]), [])


if __name__ == "__main__":
    unittest.main()
