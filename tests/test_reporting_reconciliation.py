"""The compact editor workbook must remain a projection of canonical JSON state."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from content_integrity.pipeline import run_default_pipeline


EXPECTED_SHEETS = [
    "Dashboard",
    "High Risk Queue",
    "Moderate Risk Queue",
    "Low Risk Queue",
    "All Abstracts",
    "Check Detail",
    "How This Works",
]


def _write(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(textwrap.dedent(content).strip(), encoding="utf-8")
    return path


def _article(record_id: str, title: str, abstract: str) -> str:
    return f"""
    <article article-type="Original Research"><front>
      <journal-meta><journal-title-group><journal-title>Test Journal</journal-title></journal-title-group></journal-meta>
      <article-meta><article-id pub-id-type="manuscript">{record_id}</article-id>
      <article-title>{title}</article-title><abstract><p>{abstract}</p></abstract>
      <history><date date-type="accepted"><year>2026</year></date></history></article-meta>
    </front></article>
    """


def _rows(ws) -> list[dict[str, object]]:
    values = ws.iter_rows(values_only=True)
    headers = next(values)
    return [{key: "" if value is None else value for key, value in zip(headers, row)} for row in values]


def _submission(report: dict, abstract_id: str) -> dict:
    return next(item for item in report.values() if item["abstract_id"] == abstract_id)


def _check(submission: dict, name: str) -> dict:
    return next(item for item in submission["checks"] if item["check_name"] == name)


class ReportingReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        input_dir = root / "xmls"
        input_dir.mkdir()
        template = (
            "Background: Patients with advanced disease entered this prospective study. "
            "Methods: The primary endpoint was independently assessed response rate. "
            "Results: Treatment produced durable responses and improved survival in the enrolled population. "
            "Conclusion: These findings support additional controlled investigation."
        )
        _write(input_dir, "a.xml", _article("TPL-A", "Template A", template))
        _write(input_dir, "b.xml", _article("TPL-B", "Template B", template))
        _write(input_dir, "t.xml", _article("TP-1", "Tortured", "The model uses a nervous network."))
        _write(input_dir, "c.xml", _article("CLEAN-1", "Clean", "Nothing notable here."))
        dictionary = _write(root, "dict.csv", 'Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers\n"nervous network","neural network","1"\n')
        with patch("content_integrity.pipeline.detect_numerical_contradictions", side_effect=RuntimeError("detector unavailable")):
            cls.result = run_default_pipeline(input_dir, dictionary, root / "output")
        cls.report = json.loads(cls.result.output_paths["content_integrity_json"].read_text())
        cls.workbook = load_workbook(cls.result.output_paths["workbook"], read_only=True, data_only=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.workbook.close()
        cls._tmp.cleanup()

    def test_workbook_has_reference_triage_structure(self) -> None:
        self.assertEqual(self.workbook.sheetnames, EXPECTED_SHEETS)
        self.assertEqual(self.result.output_paths["workbook"].name, "Editor_Triage_Workbook.xlsx")
        self.assertEqual(set(self.result.output_paths), {"content_integrity_json", "workbook"})

    def test_all_abstracts_reconciles_risk_and_review(self) -> None:
        excel = {row["Abstract ID"]: row for row in _rows(self.workbook["All Abstracts"])}
        self.assertEqual(set(excel), {item["abstract_id"] for item in self.report.values()})
        for abstract in self.report.values():
            row = excel[abstract["abstract_id"]]
            summary = _check(abstract, "content_integrity_summary")["result"]["supporting_data"][0]
            self.assertEqual(row["Overall Risk"].upper(), summary["overall_content_risk"])
            self.assertEqual(row["Review Required"], "Yes" if summary["review_required"] else "No")

    def test_check_detail_contains_finding_pair_and_operational_evidence(self) -> None:
        detail = {row["Abstract ID"]: row for row in _rows(self.workbook["Check Detail"])}
        self.assertEqual(detail["TP-1"]["Tortured Phrases - Flag"], "Y")
        self.assertIn("nervous network", detail["TP-1"]["Tortured Phrases - Evidence"])
        self.assertEqual(detail["TPL-A"]["Templating (Cross-Author) - Flag"], "Y")
        self.assertIn("TPL-B", detail["TPL-A"]["Templating (Cross-Author) - Evidence"])
        self.assertTrue(all(row["Operational Issues - Flag"] == "Y" for row in detail.values()))
        self.assertTrue(all("numerical_contradiction" in row["Operational Issues - Evidence"] for row in detail.values()))

    def test_dashboard_and_queues_reconcile_with_master_rows(self) -> None:
        master = _rows(self.workbook["All Abstracts"])
        queued = {
            "High": _rows(self.workbook["High Risk Queue"]),
            "Medium": _rows(self.workbook["Moderate Risk Queue"]),
            "Low": _rows(self.workbook["Low Risk Queue"]),
        }
        self.assertEqual(len(master), sum(len(rows) for rows in queued.values()))
        self.assertEqual({row["Abstract ID"] for row in queued["High"]}, {row["Abstract ID"] for row in master if row["Overall Risk"] == "High"})
        self.assertEqual({row["Abstract ID"] for row in queued["Medium"]}, {row["Abstract ID"] for row in master if row["Overall Risk"] == "Medium"})


class RejectedFindingReconciliationTests(unittest.TestCase):
    def test_rejected_finding_is_inactive_in_json_and_unflagged_in_workbook(self) -> None:
        class RejectingClient:
            def complete(self, **_kwargs) -> str:
                return json.dumps({"status": "rejected", "reason": "Legitimate terminology."})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "xmls"
            input_dir.mkdir()
            _write(input_dir, "sample.xml", _article("REJ-1", "Validator", "The model uses a nervous network."))
            dictionary = _write(root, "dict.csv", 'Fingerprint - Tortured Phrase,Expected Text,Nb Retrieved Papers\n"nervous network","neural network","1"\n')
            with patch("content_integrity.pipeline.build_gpt_oss_client", return_value=RejectingClient()):
                result = run_default_pipeline(input_dir, dictionary, root / "output", validate_llm=True)
            report = json.loads(result.output_paths["content_integrity_json"].read_text())
            finding = _check(_submission(report, "REJ-1"), "tortured_phrases")["result"]["supporting_data"][0]
            self.assertEqual(finding["validation_status"], "rejected")
            self.assertFalse(finding["active"])
            workbook = load_workbook(result.output_paths["workbook"], read_only=True, data_only=True)
            try:
                detail = _rows(workbook["Check Detail"])[0]
                master = _rows(workbook["All Abstracts"])[0]
                self.assertEqual(detail["Tortured Phrases - Flag"], "N")
                self.assertIn("[rejected]", detail["Tortured Phrases - Evidence"])
                self.assertEqual(master["Overall Risk"], "None")
            finally:
                workbook.close()


if __name__ == "__main__":
    unittest.main()
