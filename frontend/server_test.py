import json
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from server import run_pipeline


class ServerTests(unittest.TestCase):
    def test_run_pipeline_wraps_a_single_xml_and_returns_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.xml"
            source.write_text("<article/>", encoding="utf-8")
            output = root / "output"
            expected = {"summary": {"total_submissions": 1}, "abstracts": []}

            def runner(command, **kwargs):
                input_dir = Path(command[command.index("--input-dir") + 1])
                pipeline_output = Path(command[command.index("--output-dir") + 1])
                self.assertEqual([path.name for path in input_dir.glob("*.xml")], ["input.xml"])
                pipeline_output.mkdir()
                (pipeline_output / "content_integrity_results.json").write_text(json.dumps(expected), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0)

            with mock.patch("server.write_editor_triage_workbook") as write_workbook:
                self.assertEqual(run_pipeline(source, output, runner), expected)
            write_workbook.assert_called_once_with(output / "Editor_Triage_Workbook.xlsx", expected)
            self.assertEqual(json.loads((output / "content_integrity_results.json").read_text()), expected)


if __name__ == "__main__":
    unittest.main()
