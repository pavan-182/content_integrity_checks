from __future__ import annotations

import unittest

from content_integrity.models import ParsedRecord
from content_integrity.template_features import build_template_features
from content_integrity.title_templates import compare_title_templates


class TitleTemplateTests(unittest.TestCase):
    def test_masked_title_formula_is_retrieved_and_compared(self) -> None:
        features = [
            build_template_features(ParsedRecord(source_file="x.xml", record_id="A", title="Outcomes of EGFR in lung cancer")),
            build_template_features(ParsedRecord(source_file="x.xml", record_id="B", title="Outcomes of KRAS in breast cancer")),
            build_template_features(ParsedRecord(source_file="x.xml", record_id="C", title="Clinical trial enrollment barriers in oncology")),
        ]
        matches = compare_title_templates(features)
        self.assertEqual([(match.left_record_id, match.right_record_id) for match in matches], [("A", "B")])
        self.assertTrue(matches[0].exact_masked_signature)
        self.assertGreater(matches[0].masked_title_similarity, matches[0].original_title_similarity)


if __name__ == "__main__":
    unittest.main()
