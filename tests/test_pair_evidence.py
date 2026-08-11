from __future__ import annotations

import unittest

from content_integrity.models import ParsedRecord
from content_integrity.pair_evidence import collect_pair_evidence


class PairEvidenceTests(unittest.TestCase):
    def test_masked_body_and_title_evidence_are_explicit(self) -> None:
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", title="Outcomes of EGFR in lung cancer", abstract_text="A total of 50 patients received abemaciclib during treatment."),
            ParsedRecord(source_file="x.xml", record_id="B", title="Outcomes of KRAS in breast cancer", abstract_text="A total of 60 patients received ribociclib during treatment."),
        ]
        evidence = collect_pair_evidence(records)
        self.assertEqual(len(evidence), 1)
        self.assertIn("strong_masked_body_with_original_support", evidence[0].direct_evidence)
        self.assertIn("title_template", evidence[0].retrieval_routes)
        self.assertGreater(evidence[0].masked_body_similarity, evidence[0].original_body_similarity)


if __name__ == "__main__":
    unittest.main()
