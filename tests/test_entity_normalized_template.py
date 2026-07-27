from __future__ import annotations

import unittest

from asco_integrity.detectors.entity_normalized_template import (
    detect_entity_normalized_templates,
)
from asco_integrity.models import ParsedRecord


def _record(
    record_id: str,
    *,
    background: str,
    methods: str,
    results: str,
    conclusions: str,
    **kwargs,
) -> ParsedRecord:
    sections = [
        {"section": "Background", "text": background},
        {"section": "Methods", "text": methods},
        {"section": "Results", "text": results},
        {"section": "Conclusions", "text": conclusions},
    ]
    return ParsedRecord(
        source_file=f"{record_id}.xml",
        record_id=record_id,
        title=f"Study {record_id}",
        abstract_text=" ".join(item["text"] for item in sections),
        abstract_sections=sections,
        **kwargs,
    )


def _templated_pair(**kwargs) -> list[ParsedRecord]:
    common = {
        "background": (
            "Targeted therapy can improve outcomes in advanced {disease} carrying {gene} "
            "alterations despite resistance to standard treatment."
        ),
        "methods": (
            "We prospectively treated {number} adults with {drug} and assessed radiographic "
            "response using prespecified independent review criteria."
        ),
        "results": (
            "At data cutoff, {percent} of patients achieved an objective response and the "
            "median follow-up showed durable clinical benefit with manageable toxicity."
        ),
        "conclusions": (
            "{drug} demonstrated clinically meaningful activity in {gene}-positive {disease} "
            "and supports further evaluation in randomized studies."
        ),
    }
    left_values = {
        "drug": "osimertinib",
        "gene": "EGFR",
        "disease": "non-small-cell lung cancer",
        "number": "245",
        "percent": "41%",
    }
    right_values = {
        "drug": "trastuzumab",
        "gene": "HER2",
        "disease": "breast cancer",
        "number": "312",
        "percent": "56%",
    }
    left_values.update(kwargs.pop("left_values", {}))
    right_values.update(kwargs.pop("right_values", {}))
    return [
        _record("A", **{section: text.format(**left_values) for section, text in common.items()}, **kwargs),
        _record("B", **{section: text.format(**right_values) for section, text in common.items()}, **kwargs),
    ]


class EntityNormalizedTemplateTests(unittest.TestCase):
    def test_same_structure_with_different_drug_and_cancer_triggers(self) -> None:
        findings = detect_entity_normalized_templates(_templated_pair())
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].match_type, "exact_masked_skeleton")
        self.assertEqual(findings[0].severity, "high")
        self.assertIn("drug: osimertinib -> trastuzumab", findings[0].variable_substitutions)
        self.assertIn(
            "disease: non-small-cell lung cancer -> breast cancer",
            findings[0].variable_substitutions,
        )

    def test_different_genes_numbers_and_outcomes_trigger(self) -> None:
        finding = detect_entity_normalized_templates(_templated_pair())[0]
        self.assertEqual(finding.masked_skeleton_similarity, 1.0)
        self.assertIn("gene: EGFR -> HER2", finding.variable_substitutions)
        self.assertIn("number: 245 -> 312", finding.variable_substitutions)
        self.assertIn("percent: 41% -> 56%", finding.variable_substitutions)
        self.assertIn("Results", finding.matched_sections)

    def test_exact_original_duplicate_has_exact_reuse_precedence(self) -> None:
        records = _templated_pair()
        records[1].abstract_text = records[0].abstract_text
        records[1].abstract_sections = records[0].abstract_sections
        self.assertEqual(detect_entity_normalized_templates(records), [])

    def test_same_topic_independently_written_does_not_trigger(self) -> None:
        records = [
            _record(
                "A",
                background="EGFR drives a subset of advanced lung cancer and creates a therapeutic target.",
                methods="Investigators enrolled adults prospectively and administered osimertinib once daily.",
                results="Responses occurred in 41% of participants with acceptable treatment-related toxicity.",
                conclusions="The observed activity warrants a controlled trial of this targeted strategy.",
            ),
            _record(
                "B",
                background="Breast tumors with HER2 amplification remain sensitive to receptor blockade.",
                methods="Registry records were reviewed retrospectively after trastuzumab exposure.",
                results="Among 312 eligible cases, survival varied by prior treatment and disease burden.",
                conclusions="Real-world outcomes support individualized sequencing of available therapies.",
            ),
        ]
        self.assertEqual(detect_entity_normalized_templates(records), [])

    def test_shared_common_methods_only_does_not_trigger(self) -> None:
        shared_methods = (
            "Patients provided written informed consent and outcomes were summarized using "
            "standard descriptive statistics with prespecified subgroup analyses."
        )
        records = [
            _record(
                "A",
                background="This prospective study examined lung cancer response to targeted therapy.",
                methods=shared_methods,
                results="EGFR inhibition produced durable tumor regression in a molecularly selected cohort.",
                conclusions="The findings support confirmatory evaluation of osimertinib.",
            ),
            _record(
                "B",
                background="This registry study described supportive care use in breast cancer.",
                methods=shared_methods,
                results="Symptom burden varied with age, treatment setting, and baseline functional status.",
                conclusions="Supportive interventions should be tailored to individual needs.",
            ),
        ]
        self.assertEqual(detect_entity_normalized_templates(records), [])

    def test_same_registered_trial_triggers_with_low_severity(self) -> None:
        records = _templated_pair()
        for record, percentage in zip(records, ("41%", "56%")):
            record.abstract_text = f"NCT12345678 subgroup analysis. {record.abstract_text}"
            record.abstract_sections[0]["text"] += f" NCT12345678 included a {percentage} response cohort."
        finding = detect_entity_normalized_templates(records)[0]
        self.assertEqual(finding.severity, "low")
        self.assertIn("shared trial ID: NCT12345678", finding.relationship_context)

    def test_short_overmasked_text_does_not_trigger(self) -> None:
        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                abstract_text=text,
            )
            for record_id, text in (
                ("A", "EGFR osimertinib lung cancer 41% 245 p=0.01 NCT12345678."),
                ("B", "HER2 trastuzumab breast cancer 56% 312 p=0.02 NCT87654321."),
            )
        ]
        self.assertEqual(detect_entity_normalized_templates(records), [])

    def test_similar_results_and_conclusions_across_unrelated_studies_trigger(self) -> None:
        records = _templated_pair()
        records[0].abstract_sections[0]["text"] = (
            "This subgroup analysis asks whether molecular selection improves lung cancer care."
        )
        records[1].abstract_sections[0]["text"] = (
            "This final analysis describes a separately funded breast cancer programme."
        )
        records[0].abstract_sections[1]["text"] = (
            "A prospective multicentre trial enrolled previously treated adults."
        )
        records[1].abstract_sections[1]["text"] = (
            "Electronic records from community clinics formed a retrospective cohort."
        )
        for record in records:
            record.abstract_text = " ".join(item["text"] for item in record.abstract_sections)
        finding = detect_entity_normalized_templates(records)[0]
        self.assertEqual(finding.match_type, "shared_high_value_sections")
        self.assertEqual(finding.severity, "high")
        self.assertNotIn("declared related", finding.relationship_context)
        self.assertEqual(finding.matched_sections, ["Conclusions", "Results"])

    def test_same_entity_values_are_not_substitutions(self) -> None:
        records = _templated_pair(
            right_values={
                "drug": "osimertinib",
                "gene": "EGFR",
                "disease": "non-small-cell lung cancer",
                "number": "245",
                "percent": "41%",
            }
        )
        records[1].abstract_text += " Additional interpretation was planned."
        self.assertEqual(detect_entity_normalized_templates(records), [])

    def test_administrative_boilerplate_does_not_trigger(self) -> None:
        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                abstract_text=(
                    f"This study was funded by the Example Oncology Foundation under grant "
                    f"number {grant} with no role in study design, analysis, or publication."
                ),
            )
            for record_id, grant in (("A", "12345"), ("B", "67890"))
        ]
        self.assertEqual(detect_entity_normalized_templates(records), [])


if __name__ == "__main__":
    unittest.main()
