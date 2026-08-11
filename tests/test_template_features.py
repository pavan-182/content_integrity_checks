from __future__ import annotations

import unittest

from content_integrity.models import ParsedRecord
from content_integrity.template_features import FEATURE_VERSION, build_template_features


class TemplateFeatureTests(unittest.TestCase):
    def test_feature_object_is_stable_and_preserves_section_entities(self) -> None:
        record = ParsedRecord(
            source_file="sample.xml", record_id="A1", title="HER2 breast cancer",
            abstract_text="NCT12345678 enrolled 50 patients.", structured_abstract=True,
            abstract_sections=[{"section": "Methods", "text": "NCT12345678 enrolled 50 patients."}],
            trial_ids=["NCT12345678"],
        )
        first, second = build_template_features(record), build_template_features(record)
        self.assertEqual(first.feature_version, FEATURE_VERSION)
        self.assertEqual(first.source_hash, second.source_hash)
        self.assertIn("<TRIAL_ID>", first.sections[0].masked)
        self.assertEqual(first.sections[0].entities[0].text, "NCT12345678")


if __name__ == "__main__":
    unittest.main()
