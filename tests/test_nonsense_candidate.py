from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from content_integrity.detectors.nonsense_candidate import NonsenseCandidateDetector
from content_integrity.models import ParsedRecord
from content_integrity.pipeline import run_default_pipeline
from content_integrity.xml_parser import parse_xml


ROOT = Path(__file__).resolve().parents[1]


class StubClient:
    def __init__(self) -> None:
        self.sentences: list[str] = []

    def complete(self, *, system: str, user: str, max_tokens: int, temperature: float) -> str:
        sentence = json.loads(user)["sentence"]
        self.sentences.append(sentence)
        planted = {
            "ERBB2 directly metabolized pembrolizumab": "directly metabolized pembrolizumab",
            "BRCA1 physically swallowed nivolumab": "physically swallowed nivolumab",
            "PIK3CA digested trastuzumab": "digested trastuzumab",
        }
        phrase = next((value for marker, value in planted.items() if marker in sentence), "")
        return json.dumps(
            {
                "understandable": not bool(phrase),
                "suspected_phrase": phrase,
                "explanation": "The entities are connected by wording that has no coherent biomedical meaning." if phrase else "The sentence is understandable.",
                "confidence": "high" if phrase else "medium",
            }
        )


class NonsenseCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = StubClient()
        self.detector = NonsenseCandidateDetector(self.client)

    def test_clean_sentences_do_not_create_findings(self) -> None:
        records = [
            ParsedRecord("a.xml", record_id="clean-1", abstract_sections=[{"section": "Results", "text": "ERBB2 expression predicted response to trastuzumab in patients with breast cancer."}]),
            ParsedRecord("b.xml", record_id="clean-2", abstract_sections=[{"section": "Methods", "text": "Participants completed symptom questionnaires before each scheduled clinic visit."}]),
            ParsedRecord("c.xml", record_id="clean-3", abstract_sections=[{"section": "Background", "text": "Background:"}]),
        ]

        self.assertTrue(all(not self.detector.detect(record) for record in records))
        self.assertTrue(any("ERBB2" in sentence for sentence in self.client.sentences))

    def test_three_eval_corpus_candidates_are_annotated_low_severity(self) -> None:
        paths = sorted((ROOT / "tests" / "fixtures" / "eval_corpus" / "positives").glob("nonsense_candidate_*.xml"))
        findings = [self.detector.detect(parse_xml(path)) for path in paths]

        self.assertEqual(len(findings), 3)
        self.assertTrue(all(len(items) == 1 for items in findings))
        self.assertTrue(all(items[0].check_type == "nonsense_candidate" for items in findings))
        self.assertTrue(all(items[0].severity == "low" and items[0].confidence > 0 for items in findings))
        self.assertTrue(all(items[0].evidence_snippet for items in findings))

    def test_multiple_candidates_remain_marked_for_review(self) -> None:
        record = ParsedRecord(
            "sample.xml",
            record_id="two-candidates",
            abstract_sections=[{
                "section": "Results",
                "text": (
                    "ERBB2 directly metabolized pembrolizumab into a survival receptor during tumor growth. "
                    "BRCA1 physically swallowed nivolumab and converted the antibody into cellular memory."
                ),
            }],
        )
        findings = self.detector.detect(record)

        self.assertEqual(len(findings), 2)
        self.assertTrue(all(finding.validation_status == "candidate" for finding in findings))

    def test_model_failure_is_not_silently_treated_as_no_candidate(self) -> None:
        class BrokenClient:
            def complete(self, **kwargs):
                return "not-json"

        detector = NonsenseCandidateDetector(BrokenClient())
        record = ParsedRecord(
            "sample.xml",
            record_id="broken-model",
            abstract_sections=[{
                "section": "Results",
                "text": "ERBB2 directly metabolized pembrolizumab into a survival receptor during tumor growth.",
            }],
        )

        with self.assertRaisesRegex(RuntimeError, "model failed"):
            detector.detect(record)

    def test_pipeline_flag_is_opt_in(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "eval_corpus" / "positives" / "nonsense_candidate_01.xml"
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory) / "input"
            input_dir.mkdir()
            (input_dir / fixture.name).write_bytes(fixture.read_bytes())
            default_result = run_default_pipeline(input_dir, ROOT / "🤷_tortured.csv", Path(directory) / "default")
            with patch("content_integrity.pipeline.build_gpt_oss_client", return_value=StubClient()):
                enabled_result = run_default_pipeline(
                    input_dir,
                    ROOT / "🤷_tortured.csv",
                    Path(directory) / "enabled",
                    detect_nonsense_candidates=True,
                )
            with enabled_result.output_paths["detailed_findings_csv"].open(newline="", encoding="utf-8") as handle:
                detailed_rows = list(csv.DictReader(handle))

        self.assertFalse(any(item.detector_type == "nonsense_candidate" for item in default_result.findings))
        self.assertTrue(any(item.detector_type == "nonsense_candidate" for item in enabled_result.findings))
        summary = enabled_result.abstract_summary_rows[0]
        self.assertEqual(summary["nonsense_candidate_count"], 1)
        self.assertEqual(summary["total_finding_count"], 0)
        self.assertEqual(summary["highest_severity"], "None")
        self.assertEqual(summary["overall_content_risk"], "None")
        self.assertEqual(summary["review_required"], "Yes")
        self.assertTrue(
            any(
                row["detector_type"] == "nonsense_candidate"
                and row["validation_status"] == "candidate"
                for row in detailed_rows
            )
        )


if __name__ == "__main__":
    unittest.main()
