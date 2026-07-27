from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from asco_integrity.detectors.numerical_contradiction import (
    detect_numerical_contradictions,
    extract_numerical_claims,
)
from asco_integrity.models import ParsedRecord
from scripts.detect_numerical_contradictions import CSV_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


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

    def test_repeatable_event_counts_can_exceed_patient_count(self) -> None:
        for sentence in (
            "There were 30 adverse events among 25 patients.",
            "There were 30 toxicities among 25 patients.",
            "There were 40 hospitalisations among 25 patients.",
            "There were 35 episodes among 20 participants.",
        ):
            with self.subTest(sentence=sentence):
                claims = extract_numerical_claims(_record(sentence))
                self.assertIn("repeatable_event", {claim.outcome_classification for claim in claims})
                self.assertEqual(detect_numerical_contradictions([_record(sentence)]), [])

    def test_one_per_person_counts_cannot_exceed_patient_count(self) -> None:
        for sentence in (
            "There were 30 responders among 25 patients.",
            "There were 26 deaths among 25 patients.",
            "There were 26 patients with an objective response among 25 patients.",
        ):
            with self.subTest(sentence=sentence):
                findings = detect_numerical_contradictions([_record(sentence)])
                self.assertEqual(
                    [finding.contradiction_type for finding in findings],
                    ["numerator_exceeds_denominator"],
                )
                self.assertEqual(findings[0].calculated_value, float(sentence.split()[2]))

    def test_negative_and_above_limit_absolute_percentages_trigger(self) -> None:
        for sentence, boundary in (
            ("The objective response rate was -5%.", 0.0),
            ("The prevalence was 105%.", 100.0),
            ("A total of 105% of patients responded.", 100.0),
        ):
            with self.subTest(sentence=sentence):
                finding = detect_numerical_contradictions([_record(sentence)])[0]
                self.assertEqual(finding.contradiction_type, "impossible_percentage")
                self.assertEqual(finding.calculated_value, boundary)

    def test_relative_and_intensity_percentages_remain_valid(self) -> None:
        for sentence in (
            "Change from baseline was -5%.",
            "Tumour burden decreased by 120%.",
            "A 135% relative increase was observed.",
            "Relative dose intensity was 105%.",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_numerical_contradictions([_record(sentence)]), [])

    def test_out_of_range_percentage_takes_precedence(self) -> None:
        findings = detect_numerical_contradictions([
            _record("8 of 20 patients responded (135%).")
        ])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].contradiction_type, "impossible_percentage")

    def test_negative_tolerance_is_rejected_for_direct_calls(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            detect_numerical_contradictions([], percentage_tolerance=-1)

    def test_calculated_value_contract(self) -> None:
        cases = (
            ("Responses occurred in 8 of 20 patients reported as 65%.", "count_percentage_mismatch", 40.0),
            ("There were 30 responders among 25 patients.", "numerator_exceeds_denominator", 30.0),
            ("The objective response rate was -5%.", "impossible_percentage", 0.0),
            ("Median age was 62 years, range 18–55 years.", "median_outside_range", None),
            ("The response rate was 55% (95% CI 62–48%).", "reversed_interval", None),
            (
                "Among 100 patients, mutually exclusive subgroups were 60 men and 50 women.",
                "exclusive_subgroups_exceed_total",
                110.0,
            ),
        )
        for sentence, contradiction_type, calculated_value in cases:
            with self.subTest(contradiction_type=contradiction_type):
                finding = next(
                    finding
                    for finding in detect_numerical_contradictions([_record(sentence)])
                    if finding.contradiction_type == contradiction_type
                )
                self.assertEqual(finding.calculated_value, calculated_value)

    def test_cli_writes_one_csv_row_per_finding(self) -> None:
        xml = """
        <article article-type="meeting-abstract">
          <front>
            <journal-meta><journal-title-group><journal-title>JCO</journal-title></journal-title-group></journal-meta>
            <article-meta>
              <article-id custom-type="abstract-id">NUM-CLI</article-id>
              <title-group><article-title>Numerical CLI test</article-title></title-group>
              <abstract><sec><title>Results</title><p>The objective response rate was -5%.</p></sec></abstract>
            </article-meta>
          </front>
        </article>
        """
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory) / "input"
            input_dir.mkdir()
            (input_dir / "record.xml").write_text(xml, encoding="utf-8")
            output_csv = Path(directory) / "findings.csv"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "detect_numerical_contradictions.py"),
                    "--input-dir", str(input_dir),
                    "--output-csv", str(output_csv),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            with output_csv.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, CSV_COLUMNS)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["contradiction_type"], "impossible_percentage")


if __name__ == "__main__":
    unittest.main()
