from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from asco_integrity.detectors.design_contradiction import (
    DesignValidationResult,
    build_study_design_profile,
    detect_design_contradictions,
    extract_study_design_claims,
)
from asco_integrity.models import ParsedRecord
from scripts.detect_design_contradictions import CSV_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


def _record(*sentences: str, title: str = "Synthetic design study") -> ParsedRecord:
    sections = [
        {"section": "Methods", "text": sentence}
        for sentence in sentences
    ]
    return ParsedRecord(
        "design.xml",
        record_id="DESIGN-1",
        title=title,
        abstract_text=" ".join(sentences),
        abstract_sections=sections,
    )


class DesignContradictionTests(unittest.TestCase):
    def test_retrospective_plus_prospectively_randomized(self) -> None:
        finding = detect_design_contradictions([
            _record("This retrospective study prospectively randomized patients to treatment.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "prospective_vs_retrospective")
        self.assertIn("retrospective", finding.sentence_1.lower() + finding.sentence_2.lower())

    def test_retrospective_analysis_of_randomized_trial_is_not_flagged(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("We conducted a retrospective analysis of a previously prospectively randomized trial.")
            ]),
            [],
        )

    def test_observational_plus_assigned_treatment(self) -> None:
        finding = detect_design_contradictions([
            _record("This observational study assigned participants to receive treatment A or treatment B.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "observational_vs_assigned_intervention")

    def test_single_arm_plus_placebo_arm(self) -> None:
        finding = detect_design_contradictions([
            _record("This single-arm study included a placebo arm and a treatment group.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "single_arm_vs_controlled_arms")

    def test_open_label_plus_double_blind(self) -> None:
        finding = detect_design_contradictions([
            _record("The study was described as open-label and double-blind.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "open_label_vs_blinded")

    def test_open_label_extension_after_blinded_period(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("A double-blind period was followed by an open-label extension.")
            ]),
            [],
        )

    def test_phase_2_plus_phase_3(self) -> None:
        finding = detect_design_contradictions([
            _record("The protocol describes a Phase II study and also identifies it as Phase III.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "phase_2_vs_phase_3")

    def test_seamless_phase_2_3(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("This was a seamless Phase II/III study.")
            ]),
            [],
        )

    def test_randomized_plus_nonrandomized(self) -> None:
        finding = detect_design_contradictions([
            _record("The trial was randomized but was also described as nonrandomized.")
        ])[0]
        self.assertEqual(finding.contradiction_type, "randomized_vs_nonrandomized")

    def test_single_arm_with_historical_control(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("This single-arm study used a historical control arm.")
            ]),
            [],
        )

    def test_separate_discovery_and_validation_cohorts(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record(
                    "The discovery cohort was observational.",
                    "The validation cohort was an interventional randomized trial.",
                )
            ]),
            [],
        )

    def test_title_and_methods_contradiction(self) -> None:
        finding = detect_design_contradictions([
            _record(
                "Medical records were reviewed retrospectively.",
                title="A prospective study of treatment outcomes",
            )
        ])[0]
        self.assertEqual(finding.contradiction_type, "prospective_vs_retrospective")
        self.assertEqual({finding.section_1, finding.section_2}, {"Title", "Methods"})

    def test_profile_contains_requested_attributes(self) -> None:
        profile = build_study_design_profile(_record(
            "This prospective interventional randomized multi-arm double-blind "
            "placebo-controlled Phase III cohort study used a registry."
        ))
        self.assertTrue({
            "time_direction", "study_type", "allocation", "arm_structure",
            "masking", "control_type", "data_source", "phase", "design_name",
        } <= profile.claims_by_attribute.keys())

    def test_optional_validator_annotates_without_creating_finding(self) -> None:
        class Validator:
            def validate(self, finding):
                return DesignValidationResult("uncertain", "The study component is unclear.")

        finding = detect_design_contradictions([
            _record("This study was open-label and double-blind.")
        ], validator=Validator())[0]
        self.assertEqual(finding.validation_status, "uncertain")
        self.assertEqual(finding.validation_reason, "The study component is unclear.")

    def test_subgroup_and_secondary_analyses_are_separate_components(self) -> None:
        for analysis in ("subgroup", "secondary"):
            with self.subTest(analysis=analysis):
                self.assertEqual(
                    detect_design_contradictions([
                        _record(
                            f"This observational {analysis} analysis of the parent trial "
                            "included participants who were randomly assigned to treatment."
                        )
                    ]),
                    [],
                )

    def test_prospective_collection_with_retrospective_analysis(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("Data were collected prospectively and analyzed retrospectively.")
            ]),
            [],
        )

    def test_single_arm_with_external_control(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("This single-arm study used an external control arm.")
            ]),
            [],
        )

    def test_separate_phase_portions(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("A Phase II portion was followed by a Phase III portion in separate study phases.")
            ]),
            [],
        )

    def test_cli_writes_record_level_csv(self) -> None:
        xml = """
        <article article-type="meeting-abstract">
          <front>
            <journal-meta><journal-title-group><journal-title>JCO</journal-title></journal-title-group></journal-meta>
            <article-meta>
              <article-id custom-type="abstract-id">DESIGN-CLI</article-id>
              <title-group><article-title>A prospective treatment study</article-title></title-group>
              <abstract><sec><title>Methods</title><p>Medical records were reviewed retrospectively.</p></sec></abstract>
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
                    str(ROOT / "scripts" / "detect_design_contradictions.py"),
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
            self.assertEqual(rows[0]["contradiction_type"], "prospective_vs_retrospective")
            self.assertEqual(rows[0]["matched_source_type"], "same_abstract")
            self.assertEqual(rows[0]["matched_source_id"], "DESIGN-CLI")

    def test_future_prospective_study_recommendation_is_not_current_design(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record(
                    "This was a retrospective cohort study.",
                    "Prospective studies are needed to confirm these findings.",
                )
            ]),
            [],
        )

    def test_generic_cohort_in_randomized_trial_is_not_flagged(self) -> None:
        self.assertEqual(
            detect_design_contradictions([
                _record("A cohort of 300 patients was enrolled in a randomized trial.")
            ]),
            [],
        )

    def test_observational_cohort_with_randomized_assignment_is_flagged(self) -> None:
        finding = detect_design_contradictions([
            _record("This observational retrospective cohort used randomized assignment.")
        ])[0]
        self.assertEqual(
            finding.contradiction_type,
            "observational_cohort_vs_randomized_allocation",
        )

    def test_negative_allocation_phrases_do_not_extract_randomized(self) -> None:
        for phrase in ("non randomized study", "not randomized", "without randomization"):
            with self.subTest(phrase=phrase):
                values = [
                    claim.value
                    for claim in extract_study_design_claims(_record(f"This was a {phrase}."))
                    if claim.attribute == "allocation"
                ]
                self.assertEqual(values, ["nonrandomized"])

    def test_future_design_statements_are_not_current_claims(self) -> None:
        examples = (
            (
                "This retrospective study reviewed records.",
                "A prospective randomized trial is warranted.",
            ),
            (
                "This was an open-label study.",
                "Future double-blind studies are required.",
            ),
            (
                "This was a single-arm uncontrolled study.",
                "A placebo-controlled trial is planned.",
            ),
        )
        for sentences in examples:
            with self.subTest(sentences=sentences):
                self.assertEqual(detect_design_contradictions([_record(*sentences)]), [])

    def test_unrelated_secondary_analysis_does_not_hide_masking_conflict(self) -> None:
        findings = detect_design_contradictions([
            _record(
                "The primary study was described as open-label and double-blind.",
                "A separate secondary analysis examined older patients.",
            )
        ])
        self.assertEqual(
            [finding.contradiction_type for finding in findings],
            ["open_label_vs_blinded"],
        )

    def test_unrelated_cohorts_do_not_hide_phase_conflict(self) -> None:
        findings = detect_design_contradictions([
            _record(
                "The primary study was described as Phase II and Phase III.",
                "A discovery cohort identified markers.",
                "A validation cohort confirmed them.",
            )
        ])
        self.assertEqual(
            [finding.contradiction_type for finding in findings],
            ["phase_2_vs_phase_3"],
        )

    def test_legitimate_mixed_designs_are_not_flagged(self) -> None:
        examples = (
            "Data were collected prospectively and reviewed retrospectively.",
            "This was an open-label trial with blinded independent radiology review.",
            (
                "Participants came from a randomized parent trial and entered an "
                "observational long-term follow-up study."
            ),
            "Data were entered prospectively into a registry and analysed retrospectively.",
            "A Phase II cohort was evaluated within a broader Phase III development programme.",
        )
        for sentence in examples:
            with self.subTest(sentence=sentence):
                self.assertEqual(detect_design_contradictions([_record(sentence)]), [])


if __name__ == "__main__":
    unittest.main()
