from __future__ import annotations

import unittest

from content_integrity.models import ParsedRecord
from content_integrity.study_context import compare_study_context


class StudyContextTests(unittest.TestCase):
    def test_different_endpoints_require_aligned_study_context(self) -> None:
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", title="Outcomes of EGFR in lung cancer", trial_ids=["NCT12345678"], abstract_text="Subgroup analysis of NCT12345678 included 100 patients. Overall survival was assessed."),
            ParsedRecord(source_file="x.xml", record_id="B", title="Outcomes of KRAS in breast cancer", trial_ids=["NCT12345678"], abstract_text="Final analysis of NCT12345678 included 100 patients. Progression-free survival was assessed."),
        ]
        context = compare_study_context(records)[0]
        self.assertEqual(context.endpoint_overlap, "different")
        self.assertEqual(context.context_interpretation, "likely_companion_analysis")

        records[1].trial_ids = []
        records[1].abstract_text = "Final analysis included 200 patients. Progression-free survival was assessed."
        context = compare_study_context(records)[0]
        self.assertNotEqual(context.context_interpretation, "likely_companion_analysis")


if __name__ == "__main__":
    unittest.main()
