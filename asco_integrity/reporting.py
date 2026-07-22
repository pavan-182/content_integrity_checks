from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .models import Finding, ParsedRecord, TemplateClusterMember
from .utils import ensure_parent_dir, normalize_whitespace, to_pipe_string


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
THIN_FONT = Font(name="Calibri", size=11)

FINDINGS_COLUMNS = [
    "finding_id",
    "record_id",
    "source_file",
    "detector_type",
    "category",
    "matched_text",
    "expected_term",
    "evidence_snippet",
    "section_or_field",
    "severity",
    "signal_strength",
    "confidence",
    "validation_status",
    "validation_reason",
    "validated_by",
    "rule_id",
    "template_cluster_id",
    "cluster_size",
    "similar_record_ids",
    "similarity_score",
    "cluster_severity",
    "shared_skeleton_excerpt",
    "metadata_context",
    "template_pattern_type",
    "original_text_similarity",
    "masked_skeleton_similarity",
    "ngram_similarity",
    "weighted_section_similarity",
    "section_similarities",
    "variable_substitutions",
    "cluster_cohesion",
    "cluster_edge_density",
    "supporting_connections",
    "review_explanation",
    "exclusion_reason",
]


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(item, dict) for item in value):
            if all({"section", "text"}.issubset(item.keys()) for item in value):
                parts = []
                for item in value:
                    section = normalize_whitespace(item.get("section", ""))
                    text = normalize_whitespace(item.get("text", ""))
                    if section and text:
                        parts.append(f"{section}: {text}")
                    elif text:
                        parts.append(text)
                if parts:
                    return " || ".join(parts)
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return " | ".join(normalize_whitespace(str(item)) for item in value if normalize_whitespace(str(item)))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return normalize_whitespace(str(value))


def _write_table(
    ws,
    rows: Sequence[dict[str, Any]],
    columns: Sequence[str],
    start_row: int = 1,
    start_col: int = 1,
    auto_filter: bool = True,
) -> tuple[int, int]:
    row_idx = start_row
    col_idx = start_col
    for offset, column in enumerate(columns, start=col_idx):
        cell = ws.cell(row=row_idx, column=offset, value=column)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for row in rows:
        row_idx += 1
        for offset, column in enumerate(columns, start=col_idx):
            value = _stringify(row.get(column, ""))
            cell = ws.cell(row=row_idx, column=offset, value=value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.font = THIN_FONT
    if auto_filter and rows:
        ws.auto_filter.ref = f"{ws.cell(start_row, start_col).coordinate}:{ws.cell(row=row_idx, column=col_idx + len(columns) - 1).coordinate}"
    return row_idx, col_idx + len(columns) - 1


def _write_key_value_block(ws, items: Sequence[tuple[str, Any]], start_row: int = 1, title: str | None = None) -> int:
    row = start_row
    if title:
        cell = ws.cell(row=row, column=1, value=title)
        cell.font = Font(bold=True)
        row += 1
    for key, value in items:
        ws.cell(row=row, column=1, value=key).font = Font(bold=True)
        ws.cell(row=row, column=2, value=_stringify(value))
        row += 1
    return row


def _auto_size_columns(ws, max_width: int = 60) -> None:
    widths: dict[str, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            text = str(cell.value)
            widths[cell.column_letter] = max(widths.get(cell.column_letter, 0), min(len(text), max_width))
    for column_letter, width in widths.items():
        ws.column_dimensions[column_letter].width = max(12, min(width + 2, max_width))


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    resolved = ensure_parent_dir(path)
    with resolved.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return resolved


def write_csv(path: str | Path, rows: Sequence[dict[str, Any]], columns: Sequence[str]) -> Path:
    resolved = ensure_parent_dir(path)
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _stringify(row.get(column, "")) for column in columns})
    return resolved


def write_workbook(
    path: str | Path,
    *,
    inventory_rows: list[dict[str, Any]],
    root_summary_rows: list[dict[str, Any]],
    abstract_summary_rows: list[dict[str, Any]],
    findings_rows: list[dict[str, Any]],
    cluster_rows: list[dict[str, Any]],
    dictionary_rows: list[dict[str, Any]],
    parse_warning_rows: list[dict[str, Any]],
    run_metadata_rows: list[tuple[str, Any]],
) -> Path:
    resolved = ensure_parent_dir(path)
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    # Data Inventory sheet
    ws = workbook.create_sheet("Data Inventory")
    metrics = [item for item in root_summary_rows if not item[0].startswith("root_element_")]
    root_elements = [item for item in root_summary_rows if item[0].startswith("root_element_")]
    ws.cell(row=1, column=1, value="metric").fill = HEADER_FILL
    ws.cell(row=1, column=1).font = HEADER_FONT
    ws.cell(row=1, column=2, value="value").fill = HEADER_FILL
    ws.cell(row=1, column=2).font = HEADER_FONT
    row = 2
    for metric, value in metrics:
        ws.cell(row=row, column=1, value=metric).font = Font(bold=True)
        ws.cell(row=row, column=2, value=_stringify(value))
        row += 1
    row += 1
    ws.cell(row=row, column=1, value="root_element").fill = HEADER_FILL
    ws.cell(row=row, column=1).font = HEADER_FONT
    ws.cell(row=row, column=2, value="count").fill = HEADER_FILL
    ws.cell(row=row, column=2).font = HEADER_FONT
    row += 1
    for root_metric, value in root_elements:
        ws.cell(row=row, column=1, value=root_metric.replace("root_element_", "")).font = THIN_FONT
        ws.cell(row=row, column=2, value=value).font = THIN_FONT
        row += 1
    row += 1
    field_columns = [
        "field_name",
        "primary_xml_path",
        "present_count",
        "present_pct",
        "example_value",
        "useful_for_poc",
        "notes",
    ]
    _write_table(ws, inventory_rows, field_columns, start_row=row)
    ws.freeze_panes = f"A{row+1}"
    _auto_size_columns(ws)

    # Abstract Summary
    ws = workbook.create_sheet("Abstract Summary")
    summary_columns = [
        "record_id",
        "source_file",
        "schema_type",
        "title",
        "doi",
        "journal",
        "publication_year",
        "article_type",
        "authors",
        "affiliations",
        "keywords",
        "abstract_section_count",
        "structured_abstract",
        "parse_status",
        "parse_warnings",
        "llm_trace_flag",
        "tortured_phrase_flag",
        "template_cluster_flag",
        "llm_trace_count",
        "tortured_phrase_count",
        "template_cluster_id",
        "template_cluster_size",
        "template_cluster_similarity_score",
        "total_finding_count",
        "highest_severity",
        "overall_content_risk",
        "review_required",
        "review_reason",
    ]
    _write_table(ws, abstract_summary_rows, summary_columns, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Integrity Findings
    ws = workbook.create_sheet("Integrity Findings")
    _write_table(
        ws,
        findings_rows
        or [
            {
                "finding_id": "",
                "record_id": "",
                "source_file": "",
                "detector_type": "",
                "category": "",
                "matched_text": "",
                "expected_term": "",
                "evidence_snippet": "No integrity findings detected in this run.",
                "section_or_field": "",
                "severity": "",
                "confidence": "",
                "validation_status": "",
                "validation_reason": "",
                "validated_by": "",
                "rule_id": "",
                "template_cluster_id": "",
                "cluster_size": "",
                "similar_record_ids": "",
                "similarity_score": "",
                "cluster_severity": "",
                "shared_skeleton_excerpt": "No integrity findings detected in this run.",
                "metadata_context": "",
            }
        ],
        FINDINGS_COLUMNS,
        start_row=1,
    )
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Template Clusters
    ws = workbook.create_sheet("Template Clusters")
    cluster_columns = [
        "template_cluster_id",
        "cluster_size",
        "record_id",
        "source_file",
        "similar_record_ids",
        "similarity_score",
        "cluster_severity",
        "shared_skeleton_excerpt",
        "metadata_context",
        "template_pattern_type",
        "original_text_similarity",
        "masked_skeleton_similarity",
        "ngram_similarity",
        "weighted_section_similarity",
        "section_similarities",
        "variable_substitutions",
        "cluster_cohesion",
        "cluster_edge_density",
        "supporting_connections",
        "review_explanation",
        "exclusion_reason",
        "title",
        "journal",
        "publication_year",
        "article_type",
    ]
    _write_table(ws, cluster_rows or [{"template_cluster_id": "", "cluster_size": "", "record_id": "", "source_file": "", "similar_record_ids": "", "similarity_score": "", "cluster_severity": "", "shared_skeleton_excerpt": "No template clusters detected in this run.", "metadata_context": "", "title": "", "journal": "", "publication_year": "", "article_type": ""}], cluster_columns, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Pattern Dictionary
    ws = workbook.create_sheet("Pattern Dictionary")
    dictionary_columns = [
        "detector_type",
        "rule_id",
        "category",
        "pattern",
        "matched_phrase",
        "expected_term",
        "severity",
        "confidence",
        "retrieved_papers",
        "source",
    ]
    _write_table(ws, dictionary_rows, dictionary_columns, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Parse Warnings
    ws = workbook.create_sheet("Parse Warnings")
    warning_columns = [
        "source_file",
        "record_id",
        "warning_code",
        "warning_message",
        "field_name",
        "severity",
        "evidence_snippet",
        "schema_type",
    ]
    _write_table(ws, parse_warning_rows or [{"source_file": "", "record_id": "", "warning_code": "NONE", "warning_message": "No parse warnings observed in this run.", "field_name": "", "severity": "info", "evidence_snippet": "", "schema_type": ""}], warning_columns, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Run Metadata
    ws = workbook.create_sheet("Run Metadata")
    _write_key_value_block(ws, run_metadata_rows, start_row=1)
    _auto_size_columns(ws)

    workbook.save(resolved)
    return resolved
