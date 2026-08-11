from __future__ import annotations

import unittest

from content_integrity.models import ParsedRecord
from content_integrity.signal_validation import (
    assay_workflow_signatures,
    endpoint_bundle_signatures,
    molecular_axis_signatures,
    validate_signals,
)


class SignalValidationTests(unittest.TestCase):
    def test_molecular_axis_requires_explicit_relation(self) -> None:
        record = ParsedRecord(source_file="x.xml", record_id="A", abstract_text="HOTAIR sponges miR-34a and targets MET.")
        self.assertTrue(molecular_axis_signatures(record))
        record.abstract_text = "HOTAIR and miR-34a were measured with MET."
        self.assertEqual(molecular_axis_signatures(record), [])

    def test_assay_and_endpoint_signatures_are_specific(self) -> None:
        record = ParsedRecord(source_file="x.xml", record_id="A", abstract_sections=[
            {"section": "Methods", "text": "RT-qPCR was followed by Western blot."},
            {"section": "Results", "text": "Overall survival and progression-free survival improved."},
        ])
        self.assertEqual(assay_workflow_signatures(record)[0].signature, "rt qpcr > western blot")
        self.assertIn("overall survival", endpoint_bundle_signatures(record)[0].signature)

    def test_validation_reports_gold_coverage_without_enabling_routes(self) -> None:
        records = [
            ParsedRecord(source_file="x.xml", record_id="A", abstract_text="HOTAIR sponges miR-34a and targets MET."),
            ParsedRecord(source_file="x.xml", record_id="B", abstract_text="MALAT1 sponges miR-200c and targets ZEB1."),
        ]
        results, _ = validate_signals(records, {("A", "B"): True})
        molecular = next(item for item in results if item.signal == "molecular_axis")
        self.assertEqual(molecular.gold_positive_retrieved, 1)


if __name__ == "__main__":
    unittest.main()
