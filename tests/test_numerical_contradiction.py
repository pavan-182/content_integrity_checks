from __future__ import annotations

import unittest

from asco_integrity.detectors.numerical_contradiction import (
    detect_numerical_contradictions,
    extract_numerical_claims,
)
from asco_integrity.models import ParsedRecord


def _record(sentence: str, section: str = "Results") -> ParsedRecord:
    return ParsedRecord(
        "test.xml",
        record_id="TEST-1",
        title="Synthetic numerical check",
        abstract_text=sentence,
        abstract_sections=[{"section": section, "text": sentence}],
    )


class NumericalContradictionTests(unittest.TestCase):
    def test_correct_count_and_percentage(self) -> None:
        self.assertEqual(
            detect_numerical_contradictions([_record("Responses occurred in 8 of 20 patients (40%).")]),
            [],
        )

    def test_incorrect_count_and_percentage(self) -> None:
        finding = detect_numerical_contradictions([
            _record("Responses occurred in 8 of 20 patients reported as 65%.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "count_percentage_mismatch")
        self.assertEqual(finding.section, "Results")
        self.assertEqual(finding.source_sentence, "Responses occurred in 8 of 20 patients reported as 65%.")
        self.assertEqual(finding.calculated_value, 40.0)
        self.assertEqual(finding.difference, 25.0)

    def test_count_greater_than_denominator(self) -> None:
        findings = detect_numerical_contradictions([
            _record("There were 30 responses among 25 evaluable patients.")
        ])
        self.assertEqual(
            [finding.contradiction_type for finding in findings],
            ["numerator_exceeds_denominator"],
        )

    def test_impossible_response_rate(self) -> None:
        finding = detect_numerical_contradictions([
            _record("The objective response rate was 135%.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "impossible_percentage")
        self.assertEqual(finding.severity, "high")
        self.assertEqual(finding.confidence, "high")

    def test_relative_increase_above_100_is_valid(self) -> None:
        self.assertEqual(
            detect_numerical_contradictions([
                _record("Progression-free survival increased by 135% relative to baseline.")
            ]),
            [],
        )

    def test_median_outside_range(self) -> None:
        finding = detect_numerical_contradictions([
            _record("Median age was 62 years, range 18–55 years.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "median_outside_range")
        self.assertIn("Median age was 62 years", finding.source_sentence)

    def test_reversed_confidence_interval(self) -> None:
        finding = detect_numerical_contradictions([
            _record("The response rate was 55% (95% CI 62–48%).")
        ])[0]
        self.assertEqual(finding.contradiction_type, "reversed_interval")
        self.assertEqual(finding.reported_values, "lower_bound=62; upper_bound=48")

    def test_exclusive_subgroup_total_contradiction(self) -> None:
        finding = detect_numerical_contradictions([
            _record("Among 100 patients, mutually exclusive subgroups were 60 men and 50 women.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "exclusive_subgroups_exceed_total")
        self.assertEqual(finding.calculated_value, 110.0)

    def test_overlapping_subgroups_are_not_assumed_exclusive(self) -> None:
        self.assertEqual(
            detect_numerical_contradictions([
                _record(
                    "Among 100 patients, overlapping adverse-event groups included "
                    "60 with fatigue and 50 with nausea."
                )
            ]),
            [],
        )

    def test_rounding_tolerance(self) -> None:
        self.assertEqual(
            detect_numerical_contradictions([
                _record("Responses occurred in 2 of 3 patients (66.7%).")
            ], percentage_tolerance=0.1),
            [],
        )

    def test_different_populations_are_not_linked(self) -> None:
        self.assertEqual(
            detect_numerical_contradictions([
                _record(
                    "Among 20 enrolled patients, 8 responded. "
                    "Among 10 evaluable patients, the response rate was 65%."
                )
            ]),
            [],
        )

    def test_unrelated_numbers_are_not_linked(self) -> None:
        record = _record(
            "The study enrolled 20 patients across 8 centers. The response rate was 65%."
        )
        self.assertEqual(detect_numerical_contradictions([record]), [])
        self.assertTrue(extract_numerical_claims(record))


if __name__ == "__main__":
    unittest.main()
