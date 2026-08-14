from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from content_integrity.detectors.entity_normalized_template import (
    _could_reach_similarity,
    _representation,
    _rare_title_candidate_pairs,
    _section_candidate_pairs,
    _title_candidate_pairs,
    detect_entity_normalized_templates,
)
from content_integrity.models import ParsedRecord
from content_integrity.utils import text_tokens
from scripts.detect_entity_normalized_templates import main as cli_main


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
    def test_research_sponsor_boilerplate_is_removed(self) -> None:
        record = ParsedRecord("a.xml", record_id="A", abstract_text="Research Sponsor: None.")
        self.assertEqual(_representation(record).normalized, "")

    def test_local_similarity_bound_skips_only_impossible_matches(self) -> None:
        self.assertTrue(_could_reach_similarity("shared masked sentence", "shared masked sentence", 0.63))
        self.assertFalse(_could_reach_similarity("alpha beta gamma", "delta epsilon zeta", 0.63))

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

    def test_common_mimic_inhibitor_protocol_alone_does_not_trigger(self) -> None:
        records = [
            ParsedRecord(
                "A.xml",
                record_id="A",
                title="MicroRNA-708 targeting inhibits proliferation and promotes apoptosis in melanoma",
                abstract_text=(
                    "Melanoma biology was investigated through membrane signaling and tissue expression profiling. "
                    "B16 cells were assigned to normal blank negative control miR-708 mimics miR-708 inhibitors and "
                    "si-BAMBI groups. Western blotting quantified pathway activity while luciferase reporters "
                    "characterized downstream transcription. Histology evaluated tumor morphology and animal "
                    "survival across independent experiments."
                ),
            ),
            ParsedRecord(
                "B.xml",
                record_id="B",
                title="MicroRNA-182 targeting inhibits proliferation and promotes apoptosis in osteosarcoma",
                abstract_text=(
                    "Osteosarcoma progression was studied using functional phenotyping and a xenograft model. "
                    "Cultures included blank negative control miR-182 mimics miR-182 inhibitors and siRNA-HOXA9 "
                    "groups. CCK-8 flow cytometry and wound healing measured viability apoptosis and motility. "
                    "Imaging tracked tumor volume while statistical modeling assessed treatment differences over time."
                ),
            ),
        ]
        self.assertEqual(detect_entity_normalized_templates(records), [])

    def test_unstructured_abstract_uses_local_template_evidence(self) -> None:
        shared = (
            "We measured {gene} expression using quantitative polymerase chain reaction, "
            "then evaluated cell proliferation, apoptosis, migration, invasion, tumour growth, "
            "target binding, pathway activation, and rescue after treatment with {drug} in "
            "cultured cells and xenograft models."
        )
        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                abstract_text=f"{opening} {shared.format(gene=gene, drug=drug)} {ending}",
                abstract_sections=[],
            )
            for record_id, gene, drug, opening, ending in (
                (
                    "A", "EGFR", "osimertinib",
                    "A prospective lung cancer programme enrolled adults at regional hospitals.",
                    "The findings warrant a randomized clinical trial.",
                ),
                (
                    "B", "HER2", "trastuzumab",
                    "Archived breast tumour samples were obtained from a separate registry.",
                    "Future laboratory work should examine resistance mechanisms.",
                ),
            )
        ]
        finding = detect_entity_normalized_templates(records)[0]
        self.assertEqual(finding.match_type, "local_entity_substitution")
        self.assertIn("strongest local match", finding.evidence)

    def test_local_match_without_document_support_does_not_trigger(self) -> None:
        shared = (
            "We measured {gene} expression in tumour samples and quantified proliferation "
            "apoptosis migration invasion target binding pathway activation rescue response "
            "and durable growth suppression after treatment with {drug} across cultured "
            "cells xenograft models and independent validation cohorts using prespecified assays."
        )

        def unrelated_tail(prefix: str) -> str:
            return " ".join(
                f"{prefix}word{index} {prefix}detail{index} {prefix}finding{index} "
                f"{prefix}context{index} {prefix}analysis{index} {prefix}outcome{index}."
                for index in range(30)
            )

        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                abstract_text=shared.format(gene=gene, drug=drug) + " " + unrelated_tail(prefix),
            )
            for record_id, gene, drug, prefix in (
                ("A", "EGFR", "osimertinib", "alpha"),
                ("B", "HER2", "trastuzumab", "beta"),
            )
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

    def test_exact_methods_reuse_does_not_suppress_entity_template(self) -> None:
        records = _templated_pair()
        shared_methods = (
            "Adults entered a prospective multicentre study with independent radiographic "
            "review and prespecified longitudinal safety assessments."
        )
        for record in records:
            record.abstract_sections[1]["text"] = shared_methods
            record.abstract_text = " ".join(item["text"] for item in record.abstract_sections)
        finding = detect_entity_normalized_templates(records)[0]
        self.assertIn("Methods", finding.matched_sections)
        self.assertIn("drug: osimertinib -> trastuzumab", finding.variable_substitutions)

    def test_one_sided_previously_reported_wording_is_not_expected_relationship(self) -> None:
        records = _templated_pair()
        records[0].abstract_sections[0]["text"] = (
            "This previously reported study provided the rationale. "
            + records[0].abstract_sections[0]["text"]
        )
        records[0].abstract_text = " ".join(item["text"] for item in records[0].abstract_sections)
        finding = detect_entity_normalized_templates(records)[0]
        self.assertNotEqual(finding.severity, "low")
        self.assertIn("without pair-level confirmation", finding.relationship_context)

    def test_funding_boilerplate_is_removed_before_similarity(self) -> None:
        records = _templated_pair()
        for record, grant in zip(records, ("12345", "67890")):
            record.abstract_text += (
                f" This study was funded by the Example Oncology Foundation under grant "
                f"number {grant} with no role in study design or publication."
            )
        finding = detect_entity_normalized_templates(records)[0]
        self.assertEqual(finding.masked_skeleton_similarity, 1.0)
        self.assertNotIn("12345", finding.variable_substitutions)
        self.assertNotIn("67890", finding.variable_substitutions)

    def test_raw_word_count_does_not_replace_meaningful_skeleton_length(self) -> None:
        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                abstract_text=" ".join([phrase] * 5),
            )
            for record_id, phrase in (
                ("A", "EGFR osimertinib non-small-cell lung cancer 41% 245 p=0.01"),
                ("B", "HER2 trastuzumab breast cancer 56% 312 p=0.02"),
            )
        ]
        self.assertGreaterEqual(len(text_tokens(records[0].abstract_text)), 30)
        self.assertLess(_representation(records[0]).meaningful_word_count, 30)
        self.assertEqual(
            detect_entity_normalized_templates(records, maximum_placeholder_ratio=1.0),
            [],
        )

    def test_entity_normalized_sections_create_candidate_blocks(self) -> None:
        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                abstract_text=f"{background} {result}",
                abstract_sections=[
                    {"section": "Background", "text": background},
                    {"section": "Results", "text": result},
                ],
            )
            for record_id, background, result in (
                ("A", "A distinct prospective programme enrolled adults.", "Observed 41% response in lung cancer."),
                ("B", "Unrelated registry records supplied comparison data.", "Observed 56% response in breast cancer."),
            )
        ]
        representations = {record.record_id: _representation(record) for record in records}
        self.assertEqual(_section_candidate_pairs(representations), {("A", "B")})

    def test_masked_titles_create_candidate_pairs(self) -> None:
        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                title=title,
                abstract_text="Distinct body text with no shared scientific wording.",
            )
            for record_id, title in (
                ("A", "Expression of EGFR predicts survival in lung cancer"),
                ("B", "Expression of HER2 predicts survival in breast cancer"),
            )
        ]
        self.assertEqual(_title_candidate_pairs(records), {("A", "B")})

    def test_rare_raw_title_tokens_create_candidate_pairs(self) -> None:
        records = [
            ParsedRecord(
                f"{record_id}.xml",
                record_id=record_id,
                title=title,
                abstract_text="Unrelated abstract text.",
            )
            for record_id, title in (
                ("A", "Ras ERK1 signaling in colorectal cancer"),
                ("B", "Ras ERK1 signaling in gastric cancer"),
                ("C", "Independent melanoma treatment study"),
            )
        ]
        self.assertEqual(_rare_title_candidate_pairs(records), {("A", "B")})

    @patch("scripts.detect_entity_normalized_templates.write_csv")
    @patch("scripts.detect_entity_normalized_templates.detect_entity_normalized_templates")
    @patch("scripts.detect_entity_normalized_templates.parse_xml_records")
    @patch("scripts.detect_entity_normalized_templates.discover_xml_files")
    def test_cli_passes_custom_thresholds(
        self,
        discover,
        parse,
        detect,
        _write,
    ) -> None:
        discover.return_value = [Path("input.xml")]
        parse.return_value = _templated_pair()
        detect.return_value = []
        argv = [
            "detect_entity_normalized_templates.py",
            "--input-dir", "input",
            "--output-csv", "output.csv",
            "--masked-similarity-threshold", "0.8",
            "--original-support-threshold", "0.5",
            "--minimum-skeleton-words", "20",
            "--maximum-placeholder-ratio", "0.4",
            "--minimum-substitutions", "2",
            "--section-similarity-threshold", "0.82",
        ]
        with patch.object(sys, "argv", argv):
            self.assertEqual(cli_main(), 0)
        self.assertEqual(
            detect.call_args.kwargs,
            {
                "masked_similarity_threshold": 0.8,
                "original_support_threshold": 0.5,
                "minimum_skeleton_words": 20,
                "maximum_placeholder_ratio": 0.4,
                "minimum_substitutions": 2,
                "section_similarity_threshold": 0.82,
            },
        )


if __name__ == "__main__":
    unittest.main()
