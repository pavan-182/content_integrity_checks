from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from content_integrity.pipeline import run_default_pipeline


ROOT = Path(__file__).resolve().parents[1]


class DashboardSheetTests(unittest.TestCase):
    def test_dashboard_tables_reconcile_with_source_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as output_dir:
            result = run_default_pipeline(
                input_dir=ROOT / "tests" / "fixtures" / "eval_corpus",
                tortured_dictionary_path=ROOT / "🤷_tortured.csv",
                output_dir=output_dir,
            )
            workbook = load_workbook(result.output_paths["workbook"], data_only=True)

        self.assertIn("Dashboard", workbook.sheetnames)
        dashboard = workbook["Dashboard"]
        self.assertFalse(dashboard.merged_cells.ranges)

        priority = self._block(dashboard, "Abstracts by review priority")
        checks = self._block(dashboard, "Findings by check type")
        cluster_summary = self._block(dashboard, "Template cluster summary")
        largest = self._block(dashboard, "Largest 10 template clusters")
        warnings = self._block(dashboard, "Parse failures and warnings by type")
        sections = self._block(dashboard, "Findings by abstract section")

        abstract_count = workbook["Abstract Summary"].max_row - 1
        finding_count = workbook["Integrity Findings"].max_row - 1
        warning_count = workbook["Parse Warnings"].max_row - 1
        cluster_sheet = workbook["Template Clusters"]
        headers = [cell.value for cell in cluster_sheet[1]]
        family_column = headers.index("family_id") + 1
        cluster_ids = {
            row[0].value
            for row in cluster_sheet.iter_rows(min_row=2, min_col=family_column, max_col=family_column)
            if row[0].value
        }

        self.assertEqual(sum(int(row["count"]) for row in priority), abstract_count)
        self.assertEqual(sum(int(row["count"]) for row in checks), finding_count)
        summary = {row["metric"]: int(row["count"]) for row in cluster_summary}
        self.assertEqual(summary["total_clusters"], len(cluster_ids))
        self.assertEqual(sum(value for key, value in summary.items() if key.startswith("size_")), len(cluster_ids))
        self.assertLessEqual(len(largest), 10)
        self.assertTrue({row["template_cluster_id"] for row in largest} <= cluster_ids)
        self.assertEqual(sum(int(row["count"]) for row in warnings), warning_count)
        self.assertEqual(sum(int(row["count"]) for row in sections), finding_count)
        self.assertIn("Title", {row["section"] for row in sections})
        self.assertTrue({"Background", "Methods", "Results", "Conclusions"} & {row["section"] for row in sections})

    @staticmethod
    def _block(ws, title: str) -> list[dict[str, object]]:
        title_row = next(row for row in range(1, ws.max_row + 1) if ws.cell(row, 1).value == title)
        headers = [cell.value for cell in ws[title_row + 1] if cell.value is not None]
        rows: list[dict[str, object]] = []
        for row in range(title_row + 2, ws.max_row + 1):
            values = [ws.cell(row, column).value for column in range(1, len(headers) + 1)]
            if all(value is None for value in values):
                break
            rows.append(dict(zip(headers, values)))
        return rows


if __name__ == "__main__":
    unittest.main()
