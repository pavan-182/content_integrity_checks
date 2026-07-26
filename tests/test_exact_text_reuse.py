from __future__ import annotations

import unittest

from asco_integrity.detectors.exact_text_reuse import _determine_confidence, detect_exact_text_reuse
from asco_integrity.models import ParsedRecord


def _record(record_id: str, methods: str, results: str, **kwargs) -> ParsedRecord:
    return ParsedRecord(
        source_file=f"{record_id}.xml",
        record_id=record_id,
        abstract_text=f"{methods} {results}",
        abstract_sections=[
            {"section": "Methods", "text": methods},
            {"section": "Results", "text": results},
        ],
        **kwargs,
    )


class ExactTextReuseTests(unittest.TestCase):
    def test_detects_exact_results_and_preserves_pair_evidence(self) -> None:
        shared = (
            "Treatment significantly improved progression free survival and reduced disease "
            "progression across all prespecified patient subgroups in the final analysis."
        )
        findings = detect_exact_text_reuse(
            [
                _record("A", "We enrolled adults with lung cancer at five clinical centers.", shared),
                _record("B", "We studied an independent breast cancer cohort using registry data.", shared),
            ]
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].match_type, "exact_results_section")
        self.assertEqual(findings[0].matched_sections, ["Results"])
        self.assertTrue(findings[0].check_triggered)
        self.assertEqual(findings[0].severity, "high")
        self.assertEqual(findings[0].confidence, "very_high")
        self.assertIn("exact results section", findings[0].evidence)
        self.assertEqual(findings[0].record_matched_sentences, [shared])

    def test_relationship_context_downweights_but_keeps_finding(self) -> None:
        shared = (
            "Participants received protocol therapy with standardized imaging assessments "
            "and longitudinal safety monitoring throughout the registered study period."
        )
        findings = detect_exact_text_reuse(
            [
                _record("A", "This subgroup analysis evaluated NCT12345678.", shared),
                _record("B", "This final analysis evaluated NCT12345678.", shared),
            ]
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("shared trial ID", findings[0].relationship_context)
        self.assertEqual(findings[0].severity, "low")

    def test_ignores_one_short_generic_sentence(self) -> None:
        findings = detect_exact_text_reuse(
            [
                _record("A", "Adults entered cohort alpha.", "Further studies are needed."),
                _record("B", "Children entered cohort beta.", "Further studies are needed."),
        ]
        )
        self.assertEqual(findings, [])

    def test_short_abstract_does_not_inflate_generic_block_coverage(self) -> None:
        shared = "Quantitative real time polymerase chain reaction was used to detect"
        findings = detect_exact_text_reuse(
            [
                ParsedRecord(
                    "long.xml",
                    record_id="long",
                    abstract_text=f"{'Distinct study wording ' * 40}{shared} gene expression.",
                ),
                ParsedRecord(
                    "short.xml",
                    record_id="short",
                    abstract_text=f"{shared} the pathway executed critical roles and may provide treatment targets.",
                ),
            ]
        )
        self.assertEqual(findings, [])

    def test_high_confidence_needs_sentences_or_coverage(self) -> None:
        self.assertEqual(_determine_confidence("multiple_uncommon_sentences", 3, 0.2), "high")
        self.assertEqual(_determine_confidence("substantial_shared_text", 0, 0.3), "high")

    def test_ignores_administrative_boilerplate_section(self) -> None:
        funding = (
            "This study was funded by the Example Oncology Foundation under grant number "
            "12345 with no role in study design or analysis."
        )
        findings = detect_exact_text_reuse(
            [
                _record("A", "Adults entered an independent lung cancer cohort.", funding),
                _record("B", "Children entered an unrelated breast cancer cohort.", funding),
            ]
        )
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
