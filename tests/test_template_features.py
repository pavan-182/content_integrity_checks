from __future__ import annotations

import unittest
from unittest.mock import Mock

from content_integrity.detectors.entity_normalized_template import detect_entity_normalized_templates
from content_integrity.detectors.exact_text_reuse import detect_exact_text_reuse
from content_integrity.enriched_reporting import build_enriched_reports
from content_integrity.entity_extraction import EntityExtractor
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

    def test_model_inference_runs_once_per_short_record_and_features_are_reused(self) -> None:
        client = Mock()
        client.complete.return_value = '{"entities": []}'
        extractor = EntityExtractor(client)
        records = [
            ParsedRecord(
                source_file="sample.xml",
                record_id=record_id,
                title=f"Study {record_id}",
                abstract_text="EGFR was measured. Overall survival improved in 50 patients.",
                abstract_sections=[{
                    "section": "Results",
                    "text": "EGFR was measured. Overall survival improved in 50 patients.",
                }],
            )
            for record_id in ("A", "B")
        ]
        features = [build_template_features(record, extractor=extractor) for record in records]
        detect_exact_text_reuse(records, features=features)
        detect_entity_normalized_templates(records, features=features)
        build_enriched_reports(records, features=features)
        self.assertEqual(client.complete.call_count, len(records))
        self.assertEqual(extractor.inference_count, len(records))


if __name__ == "__main__":
    unittest.main()
