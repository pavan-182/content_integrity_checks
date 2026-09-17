"""Verify written reports against the run before they replace any previous output.

Output contract checked here (documented in docs/OPERATIONS.md):
- Every input record has exactly one terminal status: retained records appear once in the JSON
  and once in the workbook's All Abstracts sheet with completed, completed_with_findings, or
  failed; inputs dropped as exact ingestion duplicates are skipped and listed in run_summary.json.
  completed + completed_with_findings + failed + skipped == total input records.
- For each retained record the JSON and workbook agree on record status, processing status, and
  active finding count; the JSON active counts sum to the run's active reportable findings.
- Each reviewable template pair is listed under both of its abstracts in the JSON.
The files are read back from disk, so the checks cover what was written, not what was intended.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .models import Finding
from .reporting import RECORD_STATUSES


class ReconciliationError(RuntimeError):
    """Written reports do not reconcile; they were not promoted over previous output."""


def _json_rows(path: Path) -> dict[str, dict[str, Any]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    rows: dict[str, dict[str, Any]] = {}
    for key, entry in report.items():
        summary = entry["checks"][0]["result"]["supporting_data"][0]
        rows[str(entry["abstract_id"])] = {
            "key": key,
            "record_status": summary["record_status"],
            "processing_status": summary["processing_status"],
            "active_finding_count": int(summary["active_finding_count"]),
            "template_pair_count": len(summary["template_pair_ids"]),
            "failures": summary["failures"],
            "source_file": summary["source_file"],
        }
    return rows


def _workbook_rows(path: Path) -> tuple[list[dict[str, Any]], int]:
    with path.open("rb") as handle:
        workbook = load_workbook(handle, read_only=True, data_only=True)
        try:
            rows = workbook["All Abstracts"].iter_rows(values_only=True)
            headers = next(rows)
            abstracts = [dict(zip(headers, row)) for row in rows]
            detail_rows = max(workbook["Check Detail"].max_row - 1, 0)
        finally:
            workbook.close()
    return abstracts, detail_rows


def reconcile_outputs(
    *,
    record_ids: list[str],
    input_record_count: int,
    skipped: list[dict[str, str]],
    reportable_findings: list[Finding],
    reviewable_pair_count: int,
    json_path: Path,
    workbook_path: Path,
) -> dict[str, Any]:
    json_rows = _json_rows(json_path)
    workbook_rows, detail_row_count = _workbook_rows(workbook_path)
    workbook_by_id = {str(row["Abstract ID"]): row for row in workbook_rows}
    status_counts = Counter(row["record_status"] for row in json_rows.values())
    status_counts["skipped"] = len(skipped)
    expected_ids = set(record_ids)
    active_findings = [finding for finding in reportable_findings if finding.active]
    mismatched = sorted(
        record_id
        for record_id, row in json_rows.items()
        if record_id in workbook_by_id and (
            row["record_status"] != workbook_by_id[record_id]["Record Status"]
            or row["processing_status"] != workbook_by_id[record_id]["Processing Status"]
            or row["active_finding_count"] != int(workbook_by_id[record_id]["Finding Count"] or 0)
        )
    )
    checks = [
        ("status_totals_equal_input_records", sum(status_counts[status] for status in RECORD_STATUSES), input_record_count),
        ("json_abstracts_equal_retained_records", len(json_rows), len(record_ids)),
        ("workbook_abstracts_equal_retained_records", len(workbook_rows), len(record_ids)),
        ("workbook_check_detail_rows_equal_retained_records", detail_row_count, len(record_ids)),
        ("json_ids_match_retained_records", sorted(set(json_rows) ^ expected_ids), []),
        ("workbook_ids_match_retained_records", sorted(set(workbook_by_id) ^ expected_ids), []),
        ("workbook_ids_unique", len(workbook_by_id), len(workbook_rows)),
        ("json_workbook_record_mismatches", mismatched, []),
        ("json_active_findings_equal_run_active_findings", sum(row["active_finding_count"] for row in json_rows.values()), len(active_findings)),
        ("json_template_pair_links_equal_twice_reviewable_pairs", sum(row["template_pair_count"] for row in json_rows.values()), 2 * reviewable_pair_count),
    ]
    failures = [
        {"record_id": record_id, "source_file": row["source_file"], **failure}
        for record_id, row in sorted(json_rows.items())
        for failure in row["failures"]
    ]
    return {
        "reconciled": all(actual == expected for _, actual, expected in checks),
        "checks": [
            {"name": name, "passed": actual == expected, "actual": actual, "expected": expected}
            for name, actual, expected in checks
        ],
        "records": {
            "total_input": input_record_count,
            "completed": status_counts["completed"],
            "completed_with_findings": status_counts["completed_with_findings"],
            "failed": status_counts["failed"],
            "skipped": status_counts["skipped"],
            "in_json": len(json_rows),
            "in_workbook": len(workbook_rows),
        },
        "findings": {
            "reportable_detected_by_detector": dict(sorted(Counter(f.detector_type for f in reportable_findings).items())),
            "reportable_active_by_detector": dict(sorted(Counter(f.detector_type for f in active_findings).items())),
        },
        "failed_records": sorted({failure["record_id"] for failure in failures}),
        "failures_by_stage_and_category": dict(sorted(Counter(f"{item['stage']}:{item['error_category']}" for item in failures).items())),
        "failures": failures,
        "skipped": skipped,
    }
