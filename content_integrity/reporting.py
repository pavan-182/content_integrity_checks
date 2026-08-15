from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .utils import ensure_parent_dir, normalize_whitespace, to_pipe_string


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True)
THIN_FONT = Font(name="Calibri", size=11)
EDITOR_LABELS = (
    "not_reviewed",
    "no_issue",
    "acceptable_overlap",
    "unclear",
    "concerning_overlap",
    "duplicate_or_near_duplicate",
    "confirmed_response_residue",
    "legitimate_quotation",
    "legitimate_ai_discussion",
    "ordinary_academic_language",
    "false_positive",
    "new_pattern_confirmed",
    "uncertain",
)

CONTENT_CHECKS = (
    ("tortured_phrases", "Tortured Phrases", "tortured_phrase"),
    ("llm_response_trace", "LLM Response Trace", "llm_response_trace"),
    ("numerical_contradiction", "Numerical Contradiction", "numerical_contradiction"),
    ("design_contradiction", "Design Contradiction", "design_contradiction"),
    ("unverifiable_trial", "Unverifiable Clinical Trial", "unverifiable_clinical_trial"),
    ("templating", "Templating (Cross-Author)", "templating"),
)

FINDINGS_COLUMNS = [
    "finding_id",
    "record_id",
    "source_file",
    "detector_type",
    "check_type",
    "category",
    "matched_text",
    "expected_term",
    "evidence_snippet",
    "section_or_field",
    "severity",
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
    "exclusion_reason",
    "pair_id",
    "matched_record_id",
    "matched_source_file",
    "title",
    "matched_title",
    "primary_match_type",
    "supporting_match_types",
    "matched_sections",
    "matched_sentence_count",
    "shared_text_coverage",
    "high_value_section_similarity",
    "relationship_context",
    "pair_classification",
    "review_status",
    "editor_label",
    "editor_notes",
]

REVIEW_FINDINGS_COLUMNS = [
    "finding_id",
    "detector_type",
    "check_type",
    "record_id",
    "source_file",
    "matched_record_id",
    "title",
    "matched_title",
    "category",
    "matched_text",
    "evidence_snippet",
    "section_or_field",
    "matched_sentence_count",
    "shared_text_coverage",
    "relationship_context",
    "pair_classification",
    "severity",
    "confidence",
    "validation_status",
    "validation_reason",
    "validated_by",
    "rule_id",
    "review_status",
    "editor_label",
    "editor_notes",
]

PAIR_COLUMNS = [
    "pair_id",
    "record_id",
    "matched_record_id",
    "source_file",
    "matched_source_file",
    "title",
    "matched_title",
    "primary_match_type",
    "supporting_match_types",
    "confidence",
    "severity",
    "matched_sections",
    "matched_sentence_count",
    "shared_text_coverage",
    "original_text_similarity",
    "masked_skeleton_similarity",
    "ngram_similarity",
    "high_value_section_similarity",
    "weighted_section_similarity",
    "variable_substitutions",
    "relationship_context",
    "pair_classification",
    "evidence_excerpt",
    "review_status",
    "editor_label",
    "editor_notes",
]

FAMILY_COLUMNS = [
    "template_family_id",
    "member_count",
    "family_confidence",
    "representative_record_id",
    "template_pattern_type",
    "matched_sections",
    "edge_density",
    "median_pair_confidence",
    "changed_entity_types",
    "member_ids",
    "shared_skeleton_excerpt",
    "medoid_verification_passed",
]

DICTIONARY_COLUMNS = [
    "detector_type",
    "rule_id",
    "category",
    "pattern",
    "matched_phrase",
    "expected_term",
    "severity",
    "confidence",
    "confidence_basis",
    "rule_strength",
    "proximity",
    "retrieved_papers",
    "source",
    "dictionary_version",
]

WARNING_COLUMNS = [
    "source_file",
    "record_id",
    "warning_code",
    "warning_message",
    "field_name",
    "severity",
    "evidence_snippet",
    "schema_type",
]

OPERATIONAL_ISSUE_COLUMNS = [
    "component",
    "record_id",
    "source_file",
    "status",
    "error_type",
    "message",
    "retry_count",
    "recoverable",
]

ABSTRACT_SUMMARY_COLUMNS = [
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
    "trial_ids",
    "abstract_section_count",
    "structured_abstract",
    "parse_status",
    "parse_warnings",
    "llm_trace_flag",
    "llm_review_priority",
    "tortured_phrase_flag",
    "nonsense_candidate_flag",
    "numerical_contradiction_flag",
    "design_contradiction_flag",
    "unverifiable_trial_flag",
    "template_flag",
    "related_or_companion_flag",
    "template_confidence",
    "template_review_priority",
    "matched_abstract_count",
    "strongest_matched_record_id",
    "strongest_matched_source_file",
    "strongest_matched_title",
    "strongest_match_pair_id",
    "strongest_match_supporting_types",
    "strongest_match_sections",
    "strongest_match_sentence_count",
    "strongest_match_shared_text_coverage",
    "strongest_match_original_text_similarity",
    "strongest_match_masked_skeleton_similarity",
    "strongest_match_ngram_similarity",
    "strongest_match_high_value_section_similarity",
    "strongest_match_weighted_section_similarity",
    "strongest_match_variable_substitutions",
    "strongest_match_relationship_context",
    "strongest_pair_classification",
    "strongest_match_evidence_excerpt",
    "strongest_match_review_status",
    "primary_template_pattern",
    "matched_sections",
    "template_cluster_flag",
    "template_family_id",
    "template_family_size",
    "template_family_confidence",
    "template_evidence_summary",
    "llm_trace_count",
    "tortured_phrase_count",
    "tortured_confirmed_count",
    "tortured_rejected_count",
    "tortured_uncertain_count",
    "tortured_validation_failed_count",
    "tortured_candidate_count",
    "tortured_not_validated_count",
    "nonsense_candidate_count",
    "numerical_contradiction_count",
    "design_contradiction_count",
    "unverifiable_trial_count",
    "detected_finding_count",
    "active_finding_count",
    "review_candidate_count",
    "total_finding_count",
    "highest_severity",
    "overall_content_risk",
    "review_required",
    "review_reason",
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
    return value if isinstance(value, str) else str(value)


def _join_lines(parts: Iterable[str]) -> str:
    return " | ".join(part.strip() for part in parts if part and part.strip())


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


def _add_editor_label_validation(ws, columns: Sequence[str]) -> None:
    column = columns.index("editor_label") + 1
    validation = DataValidation(
        type="list",
        formula1=f'"{",".join(EDITOR_LABELS)}"',
        allow_blank=False,
    )
    ws.add_data_validation(validation)
    validation.add(f"{ws.cell(2, column).coordinate}:{ws.cell(max(ws.max_row, 2), column).coordinate}")


def _dashboard_block(ws, title: str, rows: list[dict[str, Any]], columns: list[str], start_row: int) -> int:
    ws.cell(row=start_row, column=1, value=title).font = Font(bold=True, size=12)
    end_row, _ = _write_table(ws, rows, columns, start_row=start_row + 1, auto_filter=False)
    return end_row + 2


def _dashboard_section(value: Any) -> str:
    section = normalize_whitespace(str(value)).lower()
    if section == "title":
        return "Title"
    for label in ("background", "methods", "results", "conclusions"):
        if label.rstrip("s") in section:
            return label.title()
    if section == "cross_document":
        return "Cross-document"
    return "Abstract"


def _write_dashboard(
    workbook: Workbook,
    abstract_summary_rows: list[dict[str, Any]],
    findings_rows: list[dict[str, Any]],
    cluster_rows: list[dict[str, Any]],
    parse_warning_rows: list[dict[str, Any]],
) -> None:
    ws = workbook.create_sheet("Dashboard")
    row = 1

    priority_counts = Counter(row["overall_content_risk"] for row in abstract_summary_rows)
    row = _dashboard_block(
        ws,
        "Abstracts by review priority",
        [{"review_priority": priority, "count": priority_counts[priority]} for priority in ("High", "Medium", "Low", "None")],
        ["review_priority", "count"],
        row,
    )

    check_names = {
        "llm_response_trace": "llm_trace",
        "tortured_phrase": "tortured_phrase",
        "template_cluster": "template",
        "nonsense_candidate": "nonsense_candidate",
    }
    check_counts = Counter(item.get("check_type") or check_names.get(item.get("detector_type", ""), item.get("detector_type", "")) for item in findings_rows)
    row = _dashboard_block(
        ws,
        "Findings by check type",
        [{"check_type": check_type, "count": count} for check_type, count in sorted(check_counts.items())],
        ["check_type", "count"],
        row,
    )

    clusters: dict[str, tuple[int, str]] = {}
    for item in cluster_rows:
        cluster_id = str(item.get("template_family_id") or item.get("family_id", ""))
        if not cluster_id:
            continue
        size = int(item.get("member_count") or item.get("family_size") or 0)
        representative = str(item.get("representative_record_id", ""))
        current = clusters.get(cluster_id)
        clusters[cluster_id] = (max(size, current[0] if current else 0), min(representative, current[1]) if current else representative)
    size_counts = Counter("5+" if size >= 5 else str(size) for size, _ in clusters.values())
    row = _dashboard_block(
        ws,
        "Template cluster summary",
        [
            {"metric": "total_clusters", "count": len(clusters)},
            *({"metric": f"size_{bucket}", "count": size_counts[bucket]} for bucket in ("2", "3", "4", "5+")),
        ],
        ["metric", "count"],
        row,
    )
    row = _dashboard_block(
        ws,
        "Largest 10 template clusters",
        [
            {"template_cluster_id": cluster_id, "cluster_size": size, "representative_record_id": representative}
            for cluster_id, (size, representative) in sorted(clusters.items(), key=lambda item: (-item[1][0], item[0]))[:10]
        ],
        ["template_cluster_id", "cluster_size", "representative_record_id"],
        row,
    )

    warning_counts = Counter(str(item.get("warning_code", "")) for item in parse_warning_rows)
    row = _dashboard_block(
        ws,
        "Parse failures and warnings by type",
        [{"warning_type": warning_type, "count": count} for warning_type, count in sorted(warning_counts.items())]
        or [{"warning_type": "NONE", "count": 0}],
        ["warning_type", "count"],
        row,
    )

    section_counts = Counter(_dashboard_section(item.get("section_or_field", "")) for item in findings_rows)
    _dashboard_block(
        ws,
        "Findings by abstract section",
        [{"section": section, "count": count} for section, count in sorted(section_counts.items())],
        ["section", "count"],
        row,
    )
    ws.freeze_panes = "A3"
    _auto_size_columns(ws)


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> Path:
    resolved = ensure_parent_dir(path)
    with resolved.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return resolved


def write_json(path: str | Path, data: Any) -> Path:
    resolved = ensure_parent_dir(path)
    with resolved.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return resolved


def _split_values(value: str, separator: str = " | ") -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(separator) if item.strip()]


def _split_semicolon_values(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(";") if item.strip()]


def _build_tortured_evidence(findings: list[dict[str, Any]]) -> str:
    return _join_lines(
        f"{finding.get('matched_phrase', '')} → {finding['expected_term']}"
        if finding.get("expected_term") else str(finding.get("matched_phrase", ""))
        for finding in findings
    )


def _build_llm_evidence(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return ""
    finding = findings[0]
    if finding.get("evidence_snippet"):
        return str(finding["evidence_snippet"])
    return f"Residual LLM response phrase detected: '{finding['matched_phrase']}'."


def _build_templating_evidence(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return ""
    return str(findings[0].get("matching_text_evidence") or "")


def _build_templating_reason(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return ""
    finding = findings[0]
    matched = finding.get("matched_abstract_id") or ""
    similarity = float(finding.get("strongest_section_similarity") or 0.0)
    section = str(finding.get("strongest_section") or "text").lower()
    author_group = "same author group" if finding.get("same_author_group") else "different author group"
    if matched and similarity:
        reason = f"{similarity:.0%} sentence-structure overlap with {matched}, {author_group}."
    elif matched:
        reason = f"Sentence-structure overlap with {matched}, {author_group}."
    else:
        reason = "Strong template reuse detected."
    return reason


def _template_evidence_pair(record_id: str, pair: dict[str, Any], record_lookup: dict[str, Any]) -> dict[str, Any]:
    matched_id = str(pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"])
    left_id, right_id = str(pair["left_record_id"]), str(pair["right_record_id"])
    left_text = str(pair.get("left_matched_text") or "")
    right_text = str(pair.get("right_matched_text") or "")
    if not left_text or not right_text:
        for part in str(pair.get("matching_text_evidence") or "").split(" || "):
            if part.startswith(f"{left_id}: "):
                left_text = part[len(left_id) + 2:]
            elif part.startswith(f"{right_id}: "):
                right_text = part[len(right_id) + 2:]
    if record_id == right_id:
        left_text, right_text = right_text, left_text
    same_authors = _same_author_group(record_lookup[record_id], record_lookup[matched_id])
    reason = _build_templating_reason([{
        "matched_abstract_id": matched_id,
        "strongest_section": pair.get("strongest_section", ""),
        "strongest_section_similarity": pair.get("strongest_masked_section_similarity", 0.0),
        "same_author_group": same_authors,
    }])
    return {
        "pair_id": pair["pair_id"],
        "submitted_abstract_id": record_id,
        "submitted_title": record_lookup[record_id].title,
        "matched_abstract_id": matched_id,
        "matched_title": record_lookup[matched_id].title,
        "submitted_evidence": left_text,
        "matched_evidence": right_text,
        "section": pair.get("strongest_section", ""),
        "similarity": pair.get("strongest_masked_section_similarity", 0.0),
        "same_author_group": same_authors,
        "reason": reason,
    }


def _is_high_confidence(value: Any) -> bool:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) >= 0.80
    return normalize_whitespace(str(value)).lower() in {"high", "very_high"}


def _same_author_group(left: Any, right: Any) -> bool:
    left_authors = {normalize_whitespace(value).lower() for value in getattr(left, "authors", []) if normalize_whitespace(value)}
    right_authors = {normalize_whitespace(value).lower() for value in getattr(right, "authors", []) if normalize_whitespace(value)}
    return bool(left_authors and right_authors and left_authors == right_authors)


def build_content_integrity_frontend_json(
    records: list[Any],
    findings: list[Any],
    enriched_pair_rows: list[dict[str, Any]],
    enriched_abstract_rows: list[dict[str, Any]],
    abstract_summary_rows: list[dict[str, Any]],
    operational_issues: list[Any],
    generated_at: str,
    git_revision: str,
) -> dict[str, Any]:
    record_lookup = {record.record_id: record for record in records}
    template_abstract_lookup = {row["record_id"]: row for row in enriched_abstract_rows}
    result_lookup = {str(row["record_id"]): row for row in abstract_summary_rows}
    if set(result_lookup) != {str(record.record_id) for record in records}:
        raise ValueError("Authoritative abstract results must cover every record exactly once.")
    operational_issue_rows = [
        issue.to_dict() if hasattr(issue, "to_dict") else dict(issue)
        for issue in operational_issues
    ]
    operational_by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for issue in operational_issue_rows:
        operational_by_record[str(issue.get("record_id", ""))].append(issue)
    findings_by_record: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        if finding.detector_type not in {
            "tortured_phrase",
            "llm_response_trace",
            "numerical_contradiction",
            "design_contradiction",
            "unverifiable_clinical_trial",
        }:
            continue
        findings_by_record[finding.record_id].append(finding)

    reviewable_pairs: list[dict[str, Any]] = [
        pair for pair in enriched_pair_rows
        if str(pair.get("review_priority", "None")) != "None"
        and str(pair.get("pair_class", "")) in {"possible_template_reuse", "possible_related_duplicate"}
    ]
    reviewed_pairs_by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in reviewable_pairs:
        reviewed_pairs_by_record[str(pair["left_record_id"])].append(pair)
        reviewed_pairs_by_record[str(pair["right_record_id"])].append(pair)

    def _pair_sort_key(pair: dict[str, Any], record_id: str) -> tuple[int, float, str]:
        priority_rank = {"High": 3, "Medium": 2, "Low": 1, "None": 0}
        return (
            -priority_rank.get(str(pair.get("review_priority", "None")), 0),
            -float(pair.get("editorial_score") or 0.0),
            str(pair.get("left_record_id") if str(pair.get("left_record_id")) != record_id else pair.get("right_record_id", "")),
        )

    abstracts: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda record: str(record.record_id)):
        record_id = str(record.record_id)
        tortured_findings = [
            finding for finding in findings_by_record.get(record_id, []) if finding.detector_type == "tortured_phrase"
        ]
        llm_findings = [
            finding for finding in findings_by_record.get(record_id, []) if finding.detector_type == "llm_response_trace"
        ]
        numerical_findings = [
            finding for finding in findings_by_record.get(record_id, []) if finding.detector_type == "numerical_contradiction"
        ]
        design_findings = [
            finding for finding in findings_by_record.get(record_id, []) if finding.detector_type == "design_contradiction"
        ]
        trial_findings = [
            finding for finding in findings_by_record.get(record_id, []) if finding.detector_type == "unverifiable_clinical_trial"
        ]
        template_pairs = reviewed_pairs_by_record.get(record_id, [])
        record_operational_issues = [
            *operational_by_record.get("", []),
            *operational_by_record.get(record_id, []),
        ]

        def _component_failed(*components: str) -> bool:
            return any(issue.get("component") in components for issue in record_operational_issues)

        result = result_lookup[record_id]
        flagged_tortured = result["tortured_phrase_flag"] == "Yes"
        flagged_llm = result["llm_trace_flag"] == "Yes"
        flagged_numerical = result["numerical_contradiction_flag"] == "Yes"
        flagged_design = result["design_contradiction_flag"] == "Yes"
        flagged_trial = result["unverifiable_trial_flag"] == "Yes"
        flagged_templating = result["template_flag"] == "Yes"
        flagged_checks: list[str] = []
        if flagged_tortured:
            flagged_checks.append("tortured_phrases")
        if flagged_llm:
            flagged_checks.append("llm_response_trace")
        if flagged_numerical:
            flagged_checks.append("numerical_contradiction")
        if flagged_design:
            flagged_checks.append("design_contradiction")
        if flagged_trial:
            flagged_checks.append("unverifiable_trial")
        if flagged_templating:
            flagged_checks.append("Templating (Cross-Author)")

        overall_risk = str(result["overall_content_risk"])
        review_required = result["review_required"] == "Yes"
        abstract_template_meta = template_abstract_lookup.get(record_id, {})
        template_family_id = abstract_template_meta.get("family_id", "")
        template_family_size = abstract_template_meta.get("family_size", 0) or 0
        ordered_template_pairs = sorted(template_pairs, key=lambda pair: _pair_sort_key(pair, record_id))

        abstract_findings: dict[str, Any] = {
            "abstract_id": record_id,
            "title": record.title,
            "corresponding_author": record.primary_author or None,
            "overall_risk": overall_risk,
            "overall_content_risk": overall_risk,
            "review_required": review_required,
            "review_reason": result["review_reason"],
            "why_flagged": result["review_reason"] or "No review required.",
            "flagged_checks": flagged_checks,
            "high_confidence_flags": 0,
            "corroborating_flags": 0,
            "operational_issues": record_operational_issues,
            "checks": {
                "tortured_phrases": {
                    "flagged": flagged_tortured,
                    "match_count": sum(finding.active for finding in tortured_findings),
                    "review_candidate": any(finding.review_candidate for finding in tortured_findings),
                    "operational_failure": any(finding.validation_failed for finding in tortured_findings) or _component_failed("tortured_phrase"),
                    "evidence": _build_tortured_evidence([
                        {
                            "finding_id": finding.finding_id,
                            "matched_phrase": finding.matched_text,
                            "expected_term": finding.expected_term,
                            "evidence_snippet": finding.evidence_snippet,
                            "section": finding.section_or_field,
                            "severity": finding.severity,
                            "confidence": finding.confidence,
                            "rule_id": finding.rule_id,
                            "validation_status": finding.validation_status or "not_validated",
                            "active": finding.active,
                            "review_candidate": finding.review_candidate,
                        }
                        for finding in tortured_findings
                    ]),
                    "findings": [
                        {
                            "finding_id": finding.finding_id,
                            "matched_phrase": finding.matched_text,
                            "expected_term": finding.expected_term,
                            "evidence_snippet": finding.evidence_snippet,
                            "section": finding.section_or_field,
                            "severity": finding.severity,
                            "confidence": finding.confidence,
                            "validation_status": finding.validation_status or "not_validated",
                            "validation_reason": finding.validation_reason or "",
                            "validated_by": finding.validated_by or "",
                            "rule_id": finding.rule_id,
                            "active": finding.active,
                            "review_candidate": finding.review_candidate,
                        }
                        for finding in tortured_findings
                    ],
                },
                "llm_response_trace": {
                    "flagged": flagged_llm,
                    "match_count": sum(finding.active for finding in llm_findings),
                    "review_candidate": any(finding.review_candidate for finding in llm_findings),
                    "operational_failure": any(finding.validation_failed for finding in llm_findings) or _component_failed("llm_response_trace", "llm_response_trace_semantic"),
                    "evidence": _build_llm_evidence([
                        {
                            "finding_id": finding.finding_id,
                            "category": finding.category,
                            "matched_phrase": finding.matched_text,
                            "evidence_snippet": finding.evidence_snippet,
                            "section": finding.section_or_field,
                            "severity": finding.severity,
                            "confidence": finding.confidence,
                            "rule_id": finding.rule_id,
                            "validation_status": finding.validation_status or "not_validated",
                            "active": finding.active,
                            "review_candidate": finding.review_candidate,
                        }
                        for finding in llm_findings
                    ]),
                    "findings": [
                        {
                            "finding_id": finding.finding_id,
                            "category": finding.category,
                            "matched_phrase": finding.matched_text,
                            "evidence_snippet": finding.evidence_snippet,
                            "section": finding.section_or_field,
                            "severity": finding.severity,
                            "confidence": finding.confidence,
                            "validation_status": finding.validation_status or "not_validated",
                            "validation_reason": finding.validation_reason or "",
                            "validated_by": finding.validated_by or "",
                            "rule_id": finding.rule_id,
                            "active": finding.active,
                            "review_candidate": finding.review_candidate,
                        }
                        for finding in llm_findings
                    ],
                },
                "numerical_contradiction": {
                    "flagged": flagged_numerical,
                    "match_count": sum(finding.active for finding in numerical_findings),
                    "review_candidate": any(finding.review_candidate for finding in numerical_findings),
                    "operational_failure": any(finding.validation_failed for finding in numerical_findings) or _component_failed("numerical_contradiction"),
                    "evidence": _join_lines(finding.evidence_snippet for finding in numerical_findings),
                    "findings": [finding.to_dict() for finding in numerical_findings],
                },
                "design_contradiction": {
                    "flagged": flagged_design,
                    "match_count": sum(finding.active for finding in design_findings),
                    "review_candidate": any(finding.review_candidate for finding in design_findings),
                    "operational_failure": any(finding.validation_failed for finding in design_findings) or _component_failed("design_contradiction"),
                    "evidence": _join_lines(finding.evidence_snippet for finding in design_findings),
                    "findings": [finding.to_dict() for finding in design_findings],
                },
                "unverifiable_trial": {
                    "flagged": flagged_trial,
                    "match_count": sum(finding.active for finding in trial_findings),
                    "review_candidate": any(finding.review_candidate for finding in trial_findings),
                    "operational_failure": any(finding.validation_failed for finding in trial_findings) or _component_failed("unverifiable_clinical_trial", "clinical_trial_registry"),
                    "evidence": _join_lines(finding.evidence_snippet for finding in trial_findings),
                    "findings": [finding.to_dict() for finding in trial_findings],
                },
                "templating": {
                    "label": "Templating (Cross-Author)",
                    "flagged": flagged_templating,
                    "match_count": len(ordered_template_pairs),
                    "review_candidate": False,
                    "operational_failure": _component_failed("exact_text_reuse", "entity_normalized_template"),
                    "evidence": _build_templating_evidence([
                        {
                            "matched_abstract_id": pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"],
                            "strongest_section": pair.get("strongest_section", ""),
                            "strongest_section_similarity": pair.get("strongest_masked_section_similarity", 0.0),
                            "matching_text_evidence": pair.get("matching_text_evidence", ""),
                            "same_author_group": _same_author_group(record_lookup[record_id], record_lookup[str(pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"])]),
                        }
                        for pair in ordered_template_pairs
                    ]),
                    "reason": _build_templating_reason([
                        {
                            "matched_abstract_id": pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"],
                            "strongest_section": pair.get("strongest_section", ""),
                            "strongest_section_similarity": pair.get("strongest_masked_section_similarity", 0.0),
                            "same_author_group": _same_author_group(record_lookup[record_id], record_lookup[str(pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"])]),
                        }
                        for pair in ordered_template_pairs
                    ]),
                    "evidence_pairs": [
                        _template_evidence_pair(record_id, pair, record_lookup)
                        for pair in ordered_template_pairs
                    ],
                    "template_family_id": template_family_id,
                    "family_size": template_family_size,
                    "highest_review_priority": ordered_template_pairs[0]["review_priority"] if ordered_template_pairs else "None",
                    "findings": [
                        {
                            "pair_id": pair["pair_id"],
                            "matched_abstract_id": pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"],
                            "matched_title": pair["right_title"] if str(pair["left_record_id"]) == record_id else pair["left_title"],
                            "pair_class": pair["pair_class"],
                            "confidence": pair.get("confidence", ""),
                            "review_priority": pair["review_priority"],
                            "editorial_score": pair["editorial_score"],
                            "primary_evidence": pair["primary_evidence"],
                            "supporting_evidence": _split_values(str(pair.get("supporting_evidence", ""))),
                            "direct_evidence": pair.get("direct_evidence", ""),
                            "evidence": pair.get("matching_text_evidence", ""),
                            "reason": _build_templating_reason([{
                                "matched_abstract_id": pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"],
                                "strongest_section": pair.get("strongest_section", ""),
                                "strongest_section_similarity": pair.get("strongest_masked_section_similarity", 0.0),
                                "same_author_group": _same_author_group(record_lookup[record_id], record_lookup[str(pair["right_record_id"] if str(pair["left_record_id"]) == record_id else pair["left_record_id"])]),
                            }]),
                            "strongest_section": pair.get("strongest_section", ""),
                            "masked_body_similarity": pair.get("masked_body_similarity", 0.0),
                            "original_body_similarity": pair.get("original_body_similarity", 0.0),
                            "strongest_section_similarity": pair.get("strongest_masked_section_similarity", 0.0),
                            "likely_substitutions": _split_semicolon_values(str(pair.get("likely_substitutions", ""))),
                            "context_interpretation": pair.get("context_interpretation", ""),
                        }
                        for pair in ordered_template_pairs
                    ],
                },
            },
        }
        abstract_findings["high_confidence_flags"] = sum((
            any(finding.active and _is_high_confidence(finding.confidence) for finding in tortured_findings),
            any(finding.active and _is_high_confidence(finding.confidence) for finding in llm_findings),
            any(finding.active and _is_high_confidence(finding.confidence) for finding in numerical_findings),
            any(finding.active and _is_high_confidence(finding.confidence) for finding in design_findings),
            any(finding.active and _is_high_confidence(finding.confidence) for finding in trial_findings),
            flagged_templating and any(_is_high_confidence(pair.get("confidence", "")) for pair in ordered_template_pairs),
        ))
        abstracts.append(abstract_findings)

    total_submissions = len(abstracts)
    high_risk = sum(1 for item in abstracts if item["overall_risk"] == "High")
    moderate_risk = sum(1 for item in abstracts if item["overall_risk"] == "Medium")
    low_risk = sum(1 for item in abstracts if item["overall_risk"] == "Low")
    no_risk = sum(1 for item in abstracts if item["overall_risk"] == "None")
    requires_editor_judgement = sum(bool(item["review_required"]) for item in abstracts)
    return {
        "run": {
            "generated_at": generated_at,
            "git_revision": git_revision,
            "report_version": "content-integrity-frontend-v1",
        },
        "summary": {
            "total_submissions": total_submissions,
            "high_risk": high_risk,
            "moderate_risk": moderate_risk,
            "low_risk": low_risk,
            "no_risk": no_risk,
            "requires_editor_judgement": requires_editor_judgement,
            "cleared_without_manual_review": total_submissions - requires_editor_judgement,
        },
        "abstracts": abstracts,
        "operational_issues": operational_issue_rows,
    }


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
    pair_rows: list[dict[str, Any]],
    cluster_rows: list[dict[str, Any]],
    dictionary_rows: list[dict[str, Any]],
    parse_warning_rows: list[dict[str, Any]],
    operational_issue_rows: list[dict[str, Any]],
    run_metadata_rows: list[tuple[str, Any]],
    pair_columns: Sequence[str] = PAIR_COLUMNS,
    cluster_columns: Sequence[str] = FAMILY_COLUMNS,
) -> Path:
    resolved = ensure_parent_dir(path)
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    _write_dashboard(workbook, abstract_summary_rows, findings_rows, cluster_rows, parse_warning_rows)

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
    _write_table(ws, abstract_summary_rows, ABSTRACT_SUMMARY_COLUMNS, start_row=1)
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
    _add_editor_label_validation(ws, FINDINGS_COLUMNS)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Template Pair Evidence
    ws = workbook.create_sheet("Template Pairs")
    _write_table(ws, pair_rows, pair_columns, start_row=1)
    if "editor_label" in pair_columns:
        _add_editor_label_validation(ws, pair_columns)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Template Clusters
    ws = workbook.create_sheet("Template Clusters")
    _write_table(ws, cluster_rows, cluster_columns, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Pattern Dictionary
    ws = workbook.create_sheet("Pattern Dictionary")
    _write_table(ws, dictionary_rows, DICTIONARY_COLUMNS, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Parse Warnings
    ws = workbook.create_sheet("Parse Warnings")
    _write_table(ws, parse_warning_rows, WARNING_COLUMNS, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    ws = workbook.create_sheet("Operational Issues")
    _write_table(ws, operational_issue_rows, OPERATIONAL_ISSUE_COLUMNS, start_row=1)
    ws.freeze_panes = "A2"
    _auto_size_columns(ws)

    # Run Metadata
    ws = workbook.create_sheet("Run Metadata")
    _write_key_value_block(ws, run_metadata_rows, start_row=1)
    _auto_size_columns(ws)

    workbook.save(resolved)
    return resolved


def write_editor_triage_workbook(path: str | Path, report: dict[str, Any]) -> Path:
    """Write the content-only, editor-facing workbook consumed by the frontend."""
    resolved = ensure_parent_dir(path)
    workbook = Workbook()
    workbook.remove(workbook.active)
    abstracts = report["abstracts"]
    summary = report["summary"]
    checks = tuple((check_id, label) for check_id, label, _ in CONTENT_CHECKS)

    dashboard = workbook.create_sheet("Dashboard")
    dashboard.merge_cells("A1:H1")
    dashboard["A1"] = "Editor Triage — Submission Overview"
    for columns, label, value, color in (
        ("A:B", "Total Submissions", summary["total_submissions"], "D9E1F2"),
        ("C:D", "No Active Risk", summary.get("no_risk", summary.get("low_risk", 0)), "C6E0B4"),
        ("E:F", "Medium Risk", summary["moderate_risk"], "FFE699"),
        ("G:H", "High Risk", summary["high_risk"], "F8CBAD"),
    ):
        left, right = columns.split(":")
        dashboard.merge_cells(f"{left}3:{right}3")
        dashboard.merge_cells(f"{left}4:{right}5")
        dashboard[f"{left}3"] = label
        dashboard[f"{left}4"] = value
        dashboard[f"{left}4"].fill = PatternFill("solid", fgColor=color)
    total = summary["total_submissions"] or 1
    for row, label, value in (
        (7, "Requires Editor Judgement (Moderate + High)", summary["requires_editor_judgement"]),
        (8, "Share of Submissions Cleared Without Manual Review (Low ÷ Total)", summary["cleared_without_manual_review"] / total),
        (9, "Share Requiring Editor Judgement (Moderate + High ÷ Total)", summary["requires_editor_judgement"] / total),
    ):
        dashboard.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3)
        dashboard.merge_cells(start_row=row, start_column=4, end_row=row, end_column=6)
        dashboard.cell(row, 1, label)
        dashboard.cell(row, 4, value)
        if row > 7:
            dashboard.cell(row, 4).number_format = "0%"

    queue_columns = [
        "Rank", "Abstract ID", "Title (short)", "Corresponding Author",
        "Why Flagged (plain English)", "High-Confidence Flags", "Corroborating Flags",
    ]
    for risk in ("High", "Medium", "Low", "None"):
        sheet = workbook.create_sheet(f"{risk} Risk Queue")
        sheet.append(queue_columns)
        for rank, abstract in enumerate((item for item in abstracts if item["overall_risk"] == risk), 1):
            sheet.append([rank, abstract["abstract_id"], abstract["title"], abstract["corresponding_author"], abstract["why_flagged"], abstract["high_confidence_flags"], abstract["corroborating_flags"]])

    all_abstracts = workbook.create_sheet("All Abstracts")
    all_abstracts.append([
        "Abstract ID", "Title (short)", "Corresponding Author", "Overall Risk",
        "Review Required", "Review Reason", "Why Flagged (plain English)",
        "High-Confidence Flags", "Corroborating Flags",
        *(label for _, label in checks),
    ])
    for abstract in abstracts:
        check_results = abstract["checks"]
        all_abstracts.append([
            abstract["abstract_id"], abstract["title"], abstract["corresponding_author"],
            abstract["overall_risk"], "Yes" if abstract.get("review_required") else "No",
            abstract.get("review_reason", ""), abstract["why_flagged"], abstract["high_confidence_flags"],
            abstract["corroborating_flags"],
            *("Y" if check_results.get(key, {"flagged": False})["flagged"] else "N" for key, _ in checks),
        ])

    operational = workbook.create_sheet("Operational Issues")
    operational.append([column.replace("_", " ").title() for column in OPERATIONAL_ISSUE_COLUMNS])
    for issue in report.get("operational_issues", []):
        operational.append([issue.get(column, "") for column in OPERATIONAL_ISSUE_COLUMNS])

    template_evidence = workbook.create_sheet("Template Evidence")
    template_evidence.append([
        "Pair ID", "Submitted Abstract ID", "Submitted Title", "Matched Abstract ID", "Matched Title",
        "Reason", "Section", "Similarity", "Author Group", "Submitted Matched Text", "Matched Abstract Text",
    ])
    for abstract in abstracts:
        for pair in abstract["checks"].get("templating", {}).get("evidence_pairs", []):
            template_evidence.append([
                pair["pair_id"], pair["submitted_abstract_id"], pair["submitted_title"],
                pair["matched_abstract_id"], pair["matched_title"], pair["reason"], pair["section"],
                pair["similarity"], "Same author group" if pair["same_author_group"] else "Different author group",
                pair["submitted_evidence"], pair["matched_evidence"],
            ])

    detail = workbook.create_sheet("Check Detail")
    detail.append(
        ["Abstract ID", "Title (short)", "Corresponding Author"]
        + [value for key, label in checks for value in (
            f"{label} - Flag", f"{label} - Evidence", *((f"{label} - Reason",) if key == "templating" else ()),
        )]
    )
    for abstract in abstracts:
        check_results = abstract["checks"]
        detail.append([
            abstract["abstract_id"], abstract["title"], abstract["corresponding_author"],
            *(value for key, _ in checks for value in (
                "Y" if check_results.get(key, {"flagged": False})["flagged"] else "N",
                (
                    f"See Template Evidence ({len(check_results.get(key, {}).get('evidence_pairs', []))} pair(s))."
                    if key == "templating" and check_results.get(key, {}).get("evidence_pairs")
                    else check_results.get(key, {"flagged": False, "evidence": ""}).get("evidence") or None
                ),
                *((check_results.get(key, {}).get("reason") or None,) if key == "templating" else ()),
            )),
        ])

    how = workbook.create_sheet("How This Works")
    how.merge_cells("A1:F1")
    how["A1"] = "Editor Triage Workbook — How This Works"
    how.merge_cells("A3:F3")
    how["A3"] = "Open Dashboard for the overview, then High Risk Queue and Moderate Risk Queue for your worklists. All Abstracts is the full master list."
    how.merge_cells("A5:F5")
    how["A5"] = "Content Integrity Checks"
    descriptions = {
        "Tortured Phrases": "Known paraphrase-evasion patterns detected in the abstract text.",
        "LLM Response Trace": "Residual AI-assistant response language detected in the abstract text.",
        "Numerical Contradiction": "Conflicting numeric claims detected within the abstract text.",
        "Design Contradiction": "Conflicting study-design claims detected within the abstract text.",
        "Unverifiable Clinical Trial": "Trial-registration details could not be verified locally or against a registry.",
        "Templating (Cross-Author)": "Strong structural overlap with another submission was detected.",
    }
    for row, (label, description) in enumerate(descriptions.items(), 6):
        how.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        how.merge_cells(start_row=row, start_column=3, end_row=row, end_column=6)
        how.cell(row, 1, label)
        how.cell(row, 3, description)
    how.merge_cells("A13:F13")
    how["A13"] = "Risk Tiers"
    for row, label, description in (
        (14, "High", "The authoritative aggregation result assigned High content risk."),
        (15, "None", "No active content-integrity finding affected overall risk."),
    ):
        how.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        how.merge_cells(start_row=row, start_column=3, end_row=row, end_column=6)
        how.cell(row, 1, label)
        how.cell(row, 3, description)

    thin = Side(style="thin", color="D9E2F3")
    for sheet in workbook.worksheets:
        sheet.sheet_view.showGridLines = False
        for row in sheet.iter_rows():
            for cell in row:
                cell.font = Font(name="Arial", size=10, bold=cell.font.bold, color=cell.font.color)
                cell.alignment = Alignment(vertical="center", wrap_text=True, horizontal=cell.alignment.horizontal)

    dashboard["A1"].font = Font(name="Arial", size=14, bold=True, color="1F4E78")
    for column in "ABCDEFGH":
        dashboard.column_dimensions[column].width = 13
    dashboard.column_dimensions["A"].width = 26
    for column in (1, 3, 5, 7):
        dashboard.cell(3, column).alignment = Alignment(horizontal="center", vertical="bottom", wrap_text=True)
        dashboard.cell(3, column).font = Font(name="Arial", size=10, color="595959")
        dashboard.cell(4, column).alignment = Alignment(horizontal="center", vertical="center")
        dashboard.cell(4, column).font = Font(name="Arial", size=22, bold=True)
    for row in range(7, 10):
        dashboard.cell(row, 1).font = Font(name="Arial", size=10, bold=True)
        dashboard.cell(row, 4).font = Font(name="Arial", size=12, bold=True)
        dashboard.cell(row, 4).alignment = Alignment(horizontal="center")

    table_widths = {
        "High Risk Queue": [7, 13, 30, 16, 40, 14, 13],
        "Medium Risk Queue": [7, 13, 30, 16, 40, 14, 13],
        "Low Risk Queue": [7, 13, 30, 16, 40, 14, 13],
        "None Risk Queue": [7, 13, 30, 16, 40, 14, 13],
        "All Abstracts": [13, 30, 16, 12, 13, 40, 40, 12, 13, *([13] * len(checks))],
        "Template Evidence": [18, 18, 30, 18, 30, 46, 14, 12, 20, 60, 60],
        "Operational Issues": [24, 18, 24, 12, 24, 60, 12, 12],
    }
    for name, widths in table_widths.items():
        sheet = workbook[name]
        for cell in sheet[1]:
            cell.fill = HEADER_FILL
            cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[chr(64 + index)].width = width
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.border = Border(bottom=thin)
        sheet.freeze_panes = "D2" if name == "All Abstracts" else "A2"
        sheet.auto_filter.ref = sheet.dimensions

    detail.freeze_panes = "D2"
    detail.auto_filter.ref = detail.dimensions
    for index, cell in enumerate(detail[1], 1):
        cell.fill = PatternFill("solid", fgColor="8EA9DB" if index <= 3 else "C0504D")
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        detail.column_dimensions[cell.column_letter].width = 13 if index == 1 else 26 if index == 2 else 15 if index == 3 else 9 if "Flag" in str(cell.value) else 34
    for row in detail.iter_rows(min_row=2):
        for cell in row:
            cell.border = Border(bottom=thin)

    how["A1"].font = Font(name="Arial", size=14, bold=True, color="1F4E78")
    for row in (5, 13):
        how.cell(row, 1).font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        how.cell(row, 1).fill = HEADER_FILL
    for column, width in zip("ABCDEF", (24, 16, 13, 13, 13, 13)):
        how.column_dimensions[column].width = width

    workbook.save(resolved)
    return resolved
