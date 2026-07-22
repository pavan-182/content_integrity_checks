from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregation.risk_engine import _risk_from_signals, severity_rank
from .detectors import (
    built_in_llm_rules,
    build_tortured_rule_index,
    cluster_templates,
    detect_llm_trace,
    detect_tortured_phrases,
    load_tortured_rules,
)
from .models import Finding, ParseWarning, ParsedRecord, TemplateClusterMember
from .reporting import FINDINGS_COLUMNS, write_csv, write_jsonl, write_workbook
from .validators import ContextValidator, build_gpt_oss_client
from .utils import dedupe_records, normalize_whitespace, to_pipe_string
from .xml_parser import discover_xml_files, parse_wiley_xml_records


@dataclass(slots=True)
class PipelineConfig:
    input_dir: Path
    output_dir: Path
    tortured_dictionary_path: Path
    similarity_threshold: float = 0.88
    dictionary_version: str = "wiley_tortured_seed_v1"
    validate_llm: bool = False


@dataclass(slots=True)
class PipelineResult:
    xml_files: list[Path]
    records: list[ParsedRecord]
    findings: list[Finding]
    template_rows: list[TemplateClusterMember]
    field_inventory_rows: list[dict[str, Any]]
    root_summary_rows: list[tuple[str, Any]]
    abstract_summary_rows: list[dict[str, Any]]
    parse_warning_rows: list[dict[str, Any]]
    dictionary_rows: list[dict[str, Any]]
    run_metadata_rows: list[tuple[str, Any]]
    output_paths: dict[str, Path]


def _finding_sort_key(finding: Finding) -> tuple[str, str, str, str, int, int]:
    return (
        finding.record_id,
        finding.detector_type,
        finding.rule_id,
        finding.section_or_field,
        finding.severity,
        finding.finding_id,
    )


def _inventory_rows(records: list[ParsedRecord]) -> tuple[list[dict[str, Any]], list[tuple[str, Any]]]:
    total = len(records)
    root_counts = Counter(record.schema_type for record in records)
    structured_count = sum(1 for record in records if record.structured_abstract)
    parse_status_counts = Counter(record.parse_status for record in records)
    field_rows = [
        {
            "field_name": "record_id",
            "primary_xml_path": "article-meta/article-id[@pub-id-type='manuscript|submission-id'] | @ms_no | @tracking_no",
            "present_count": sum(1 for record in records if record.record_id),
            "present_pct": f"{sum(1 for record in records if record.record_id) / total * 100:.1f}%",
            "example_value": next((record.record_id for record in records if record.record_id), ""),
            "useful_for_poc": "yes",
            "notes": "Primary record key used for workbook joins.",
        },
        {
            "field_name": "doi",
            "primary_xml_path": "article-id[@pub-id-type='doi'] | article_id[@id_type='doi']",
            "present_count": sum(1 for record in records if record.doi),
            "present_pct": f"{sum(1 for record in records if record.doi) / total * 100:.1f}%",
            "example_value": next((record.doi for record in records if record.doi), ""),
            "useful_for_poc": "yes",
            "notes": "Sparse in this corpus; values are preserved as observed.",
        },
        {
            "field_name": "title",
            "primary_xml_path": "article-meta/title-group/article-title | article_title",
            "present_count": sum(1 for record in records if record.title),
            "present_pct": f"{sum(1 for record in records if record.title) / total * 100:.1f}%",
            "example_value": next((record.title for record in records if record.title), ""),
            "useful_for_poc": "yes",
            "notes": "Primary text source for matching and reporting.",
        },
        {
            "field_name": "abstract_text",
            "primary_xml_path": "article-meta/abstract | article/abstract",
            "present_count": sum(1 for record in records if record.abstract_text),
            "present_pct": f"{sum(1 for record in records if record.abstract_text) / total * 100:.1f}%",
            "example_value": next((record.abstract_text[:160] for record in records if record.abstract_text), ""),
            "useful_for_poc": "yes",
            "notes": "Primary input for detectors and template clustering.",
        },
        {
            "field_name": "abstract_sections",
            "primary_xml_path": "structured abstract sections or fallback Abstract section",
            "present_count": sum(1 for record in records if record.abstract_sections),
            "present_pct": f"{sum(1 for record in records if record.abstract_sections) / total * 100:.1f}%",
            "example_value": next(
                (
                    " | ".join(section["section"] for section in record.abstract_sections[:4])
                    for record in records
                    if record.abstract_sections
                ),
                "",
            ),
            "useful_for_poc": "yes",
            "notes": f"Fallback Abstract section is populated for all records; explicit structured headings detected in {structured_count}/{total} records.",
        },
        {
            "field_name": "keywords",
            "primary_xml_path": "kwd-group/kwd | attr_type[@name='Keywords']/attribute[@name]",
            "present_count": sum(1 for record in records if record.keywords),
            "present_pct": f"{sum(1 for record in records if record.keywords) / total * 100:.1f}%",
            "example_value": next((to_pipe_string(record.keywords[:5]) for record in records if record.keywords), ""),
            "useful_for_poc": "yes",
            "notes": "Useful as metadata context, not a primary detector input.",
        },
        {
            "field_name": "authors",
            "primary_xml_path": "contrib-group/contrib | author_list/author",
            "present_count": sum(1 for record in records if record.authors),
            "present_pct": f"{sum(1 for record in records if record.authors) / total * 100:.1f}%",
            "example_value": next((to_pipe_string(record.authors[:3]) for record in records if record.authors), ""),
            "useful_for_poc": "yes",
            "notes": "Used as metadata context for template clusters.",
        },
        {
            "field_name": "affiliations",
            "primary_xml_path": "aff | affiliation | profile_affiliation | current_profile_affiliation",
            "present_count": sum(1 for record in records if record.affiliations),
            "present_pct": f"{sum(1 for record in records if record.affiliations) / total * 100:.1f}%",
            "example_value": next((to_pipe_string(record.affiliations[:2]) for record in records if record.affiliations), ""),
            "useful_for_poc": "yes",
            "notes": "Used as metadata context for template clusters.",
        },
        {
            "field_name": "journal",
            "primary_xml_path": "journal-title | full_journal_title | publisher_name",
            "present_count": sum(1 for record in records if record.journal),
            "present_pct": f"{sum(1 for record in records if record.journal) / total * 100:.1f}%",
            "example_value": next((record.journal for record in records if record.journal), ""),
            "useful_for_poc": "yes",
            "notes": "Useful for editorial context and cluster filtering.",
        },
        {
            "field_name": "article_type",
            "primary_xml_path": "@article-type | publication_type | subject",
            "present_count": sum(1 for record in records if record.article_type),
            "present_pct": f"{sum(1 for record in records if record.article_type) / total * 100:.1f}%",
            "example_value": next((record.article_type for record in records if record.article_type), ""),
            "useful_for_poc": "yes",
            "notes": "Useful for segmentation and context in the workbook.",
        },
        {
            "field_name": "publication_year",
            "primary_xml_path": "history/date[@date-type='accepted']/year | pub-date/year | export_date",
            "present_count": sum(1 for record in records if record.publication_year),
            "present_pct": f"{sum(1 for record in records if record.publication_year) / total * 100:.1f}%",
            "example_value": next((record.publication_year for record in records if record.publication_year), ""),
            "useful_for_poc": "yes",
            "notes": "Derived from explicit manuscript dates when possible.",
        },
        {
            "field_name": "body_text",
            "primary_xml_path": "article/body | article/content",
            "present_count": 0,
            "present_pct": "0.0%",
            "example_value": "",
            "useful_for_poc": "no",
            "notes": "No usable article body text was observed in this corpus; internal email bodies were excluded from matching.",
        },
    ]
    root_summary_rows = [
        ("total_files", total),
        ("parsed_successfully", parse_status_counts.get("parsed", 0)),
        ("parsed_with_warnings", parse_status_counts.get("parsed_with_warnings", 0)),
        ("failed_files", parse_status_counts.get("failed", 0)),
        ("structured_abstract_records", structured_count),
    ]
    root_summary_rows.extend((f"root_element_{key}", value) for key, value in sorted(root_counts.items()))
    return field_rows, root_summary_rows


def _aggregate_findings(
    records: list[ParsedRecord],
    findings: list[Finding],
    clusters: list[TemplateClusterMember],
) -> list[dict[str, Any]]:
    finding_map: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        finding_map[finding.record_id].append(finding)
    cluster_map: dict[str, TemplateClusterMember] = {row.record_id: row for row in clusters}
    summary_rows: list[dict[str, Any]] = []
    for record in records:
        record_findings = finding_map.get(record.record_id, [])
        llm_findings = [finding for finding in record_findings if finding.detector_type == "llm_response_trace"]
        tortured_findings = [finding for finding in record_findings if finding.detector_type == "tortured_phrase"]
        cluster_row = cluster_map.get(record.record_id)
        cluster_flag = cluster_row is not None and cluster_row.cluster_severity != "excluded"
        detector_types = {finding.detector_type for finding in record_findings}
        if cluster_flag:
            detector_types.add("template_cluster")
        highest_severity = "none"
        for finding in record_findings:
            if severity_rank(finding.severity) > severity_rank(highest_severity):
                highest_severity = finding.severity
        if cluster_flag and severity_rank(cluster_row.cluster_severity) > severity_rank(highest_severity):
            highest_severity = cluster_row.cluster_severity
        overall_risk = _risk_from_signals(
            highest_signal_severity=highest_severity,
            detector_types=detector_types,
            total_finding_count=len(record_findings) + (1 if cluster_flag else 0),
            template_cluster_flag=cluster_flag,
        )
        review_required = overall_risk != "None"
        review_reason = ""
        if review_required:
            review_reason = "Potential content integrity issue detected. Manual review recommended."
        summary_rows.append(
            {
                "record_id": record.record_id,
                "source_file": record.source_file,
                "title": record.title,
                "doi": record.doi,
                "journal": record.journal,
                "publication_year": record.publication_year,
                "article_type": record.article_type,
                "authors": to_pipe_string(record.authors),
                "affiliations": to_pipe_string(record.affiliations),
                "keywords": to_pipe_string(record.keywords),
                "schema_type": record.schema_type,
                "abstract_section_count": record.abstract_section_count,
                "structured_abstract": record.structured_abstract,
                "parse_status": record.parse_status,
                "parse_warnings": to_pipe_string([warning.warning_code for warning in record.parse_warnings]),
                "llm_trace_flag": "Yes" if llm_findings else "No",
                "tortured_phrase_flag": "Yes" if tortured_findings else "No",
                "template_cluster_flag": "Yes" if cluster_flag else "No",
                "llm_trace_count": len(llm_findings),
                "tortured_phrase_count": len(tortured_findings),
                "template_cluster_id": cluster_row.template_cluster_id if cluster_row else "",
                "template_cluster_size": cluster_row.cluster_size if cluster_row else 0,
                "template_cluster_similarity_score": cluster_row.similarity_score if cluster_row else "",
                "total_finding_count": len(record_findings) + (1 if cluster_flag else 0),
                "highest_severity": highest_severity.title() if highest_severity != "none" else "None",
                "overall_content_risk": overall_risk,
                "review_required": "Yes" if review_required else "No",
                "review_reason": review_reason,
            }
        )
    return summary_rows


def _findings_rows(findings: list[Finding]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, finding in enumerate(sorted(findings, key=_finding_sort_key), start=1):
        if not finding.finding_id:
            finding.finding_id = f"FND-{index:05d}"
        item = finding.to_dict()
        item["signal_strength"] = round(finding.signal_strength, 3)
        item["confidence"] = round(finding.confidence, 3)
        rows.append(item)
    return rows


def _template_finding_rows(clusters: list[TemplateClusterMember]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, cluster in enumerate((row for row in clusters if row.cluster_severity != "excluded"), start=1):
        rows.append(
            {
                "finding_id": f"TPL-FND-{index:05d}",
                "record_id": cluster.record_id,
                "source_file": cluster.source_file,
                "detector_type": "template_cluster",
                "category": "template_cluster",
                "matched_text": cluster.template_pattern_type,
                "expected_term": "",
                "evidence_snippet": cluster.shared_skeleton_excerpt,
                "section_or_field": "cross_document",
                "severity": cluster.cluster_severity,
                "confidence": round(cluster.similarity_score, 3),
                "validation_status": "",
                "validation_reason": "",
                "validated_by": "",
                "rule_id": cluster.template_cluster_id,
                "template_cluster_id": cluster.template_cluster_id,
                "cluster_size": cluster.cluster_size,
                "similar_record_ids": cluster.similar_record_ids,
                "similarity_score": round(cluster.similarity_score, 3),
                "cluster_severity": cluster.cluster_severity,
                "shared_skeleton_excerpt": cluster.shared_skeleton_excerpt,
                "metadata_context": cluster.metadata_context,
                "template_pattern_type": cluster.template_pattern_type,
                "original_text_similarity": cluster.original_text_similarity,
                "masked_skeleton_similarity": cluster.masked_skeleton_similarity,
                "ngram_similarity": cluster.ngram_similarity,
                "weighted_section_similarity": cluster.weighted_section_similarity,
                "section_similarities": cluster.section_similarities,
                "variable_substitutions": cluster.variable_substitutions,
                "cluster_cohesion": cluster.cluster_cohesion,
                "cluster_edge_density": cluster.cluster_edge_density,
                "supporting_connections": cluster.supporting_connections,
                "review_explanation": cluster.review_explanation,
                "exclusion_reason": cluster.exclusion_reason,
            }
        )
    return rows


def _finding_row_sort_key(row: dict[str, Any]) -> tuple[str, str, str, str, int, str]:
    return (
        str(row.get("record_id", "")),
        str(row.get("detector_type", "")),
        str(row.get("rule_id", "")),
        str(row.get("section_or_field", "")),
        severity_rank(str(row.get("severity", ""))),
        str(row.get("finding_id", "")),
    )


def _cluster_rows(records: list[ParsedRecord], clusters: list[TemplateClusterMember]) -> list[dict[str, Any]]:
    record_lookup = {record.record_id: record for record in records}
    rows: list[dict[str, Any]] = []
    for cluster in clusters:
        record = record_lookup.get(cluster.record_id)
        row = cluster.to_dict()
        row["title"] = record.title if record else ""
        row["journal"] = record.journal if record else ""
        row["publication_year"] = record.publication_year if record else ""
        row["article_type"] = record.article_type if record else ""
        rows.append(row)
    return rows


def _parse_warning_rows(records: list[ParsedRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        for warning in record.parse_warnings:
            row = warning.to_dict()
            row["source_file"] = record.source_file
            row["record_id"] = record.record_id
            row["schema_type"] = record.schema_type
            rows.append(row)
    return rows


def _dictionary_rows(llm_rules: list[dict[str, Any]], tortured_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(llm_rules)
    rows.extend(tortured_rules)
    return rows


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    xml_files = discover_xml_files(config.input_dir)
    records = [record for path in xml_files for record in parse_wiley_xml_records(path)]
    records, record_id_warnings = dedupe_records(records)
    llm_rules = built_in_llm_rules()
    tortured_rules = load_tortured_rules(config.tortured_dictionary_path)
    tortured_index = build_tortured_rule_index(tortured_rules)

    findings: list[Finding] = []
    for record in records:
        llm_findings = detect_llm_trace(record, llm_rules)
        tortured_findings = detect_tortured_phrases(record, tortured_rules, tortured_index)
        findings.extend(llm_findings)
        findings.extend(tortured_findings)

    ordered_findings = sorted(findings, key=_finding_sort_key)
    for index, finding in enumerate(ordered_findings, start=1):
        if not finding.finding_id:
            finding.finding_id = f"FND-{index:05d}"

    if config.validate_llm and ordered_findings:
        validator = ContextValidator(client=build_gpt_oss_client())
        record_lookup = {record.record_id: record for record in records}
        for finding in ordered_findings:
            if finding.detector_type not in validator.applies_to:
                continue
            record = record_lookup.get(finding.record_id)
            if record is None:
                continue
            result = validator.validate(finding, record)
            finding.validation_status = result.status
            finding.validation_reason = result.reason
            finding.validated_by = f"{result.model_id}:{result.prompt_version}"

    template_rows = cluster_templates(records, similarity_threshold=config.similarity_threshold)
    field_inventory_rows, root_summary_rows = _inventory_rows(records)
    abstract_summary_rows = _aggregate_findings(records, findings, template_rows)
    findings_rows = _findings_rows(findings)
    template_finding_rows = _template_finding_rows(template_rows)
    integrity_finding_rows = sorted(findings_rows + template_finding_rows, key=_finding_row_sort_key)
    cluster_rows = _cluster_rows(records, template_rows)
    parse_warning_rows = _parse_warning_rows(records)
    parse_warning_rows.extend(
        {
            "source_file": warning["source_file"],
            "record_id": warning["record_id"],
            "warning_code": warning["reason"],
            "warning_message": warning["action"],
            "field_name": "record_id",
            "severity": "warning",
            "evidence_snippet": "",
            "schema_type": "",
        }
        for warning in record_id_warnings
    )
    dictionary_rows = _dictionary_rows([rule.to_dict() for rule in llm_rules], [rule.to_dict() for rule in tortured_rules])
    now = datetime.now(timezone.utc)
    run_metadata_rows: list[tuple[str, Any]] = [
        ("run_date_utc", now.isoformat()),
        ("input_folder", str(config.input_dir)),
        ("output_folder", str(config.output_dir)),
        ("total_files", len(xml_files)),
        ("parsed_successfully", sum(1 for record in records if record.parse_status == "parsed")),
        ("parsed_with_warnings", sum(1 for record in records if record.parse_status == "parsed_with_warnings")),
        ("failed_files", sum(1 for record in records if record.parse_status == "failed")),
        ("llm_rule_count", len(llm_rules)),
        ("tortured_rule_count", len(tortured_rules)),
        ("dictionary_version", config.dictionary_version),
        ("tortured_dictionary_path", str(config.tortured_dictionary_path)),
        ("similarity_threshold", config.similarity_threshold),
        ("limitations", "Deterministic POC that flags explicit LLM response traces, known tortured phrases, and repeated abstract skeletons; it does not detect AI-generated authorship."),
        ("excluded_scope", "AI-generated text detection"),
    ]

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, Path] = {}
    output_paths["parsed_jsonl"] = write_jsonl(output_dir / "parsed_records.jsonl", [record.to_dict() for record in records])
    output_paths["parsed_csv"] = write_csv(
        output_dir / "parsed_records.csv",
        [record.to_dict() for record in records],
        [
            "source_file",
            "schema_type",
            "record_id",
            "doi",
            "title",
            "abstract_text",
            "keywords",
            "authors",
            "affiliations",
            "journal",
            "article_type",
            "publication_year",
            "raw_text",
            "parse_status",
            "parse_warnings",
        ],
    )
    output_paths["findings_csv"] = write_csv(
        output_dir / "integrity_findings.csv",
        integrity_finding_rows,
        FINDINGS_COLUMNS,
    )
    output_paths["clusters_csv"] = write_csv(
        output_dir / "template_clusters.csv",
        cluster_rows,
        [
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
        ],
    )
    output_paths["dictionary_csv"] = write_csv(
        output_dir / "pattern_dictionary.csv",
        dictionary_rows,
        [
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
        ],
    )
    output_paths["warnings_csv"] = write_csv(
        output_dir / "parse_warnings.csv",
        parse_warning_rows,
        [
            "source_file",
            "record_id",
            "warning_code",
            "warning_message",
            "field_name",
            "severity",
            "evidence_snippet",
            "schema_type",
        ],
    )
    output_paths["metadata_json"] = write_jsonl(output_dir / "run_metadata.jsonl", [{"key": key, "value": value} for key, value in run_metadata_rows])
    output_paths["workbook"] = write_workbook(
        output_dir / "content_integrity_screening_poc.xlsx",
        inventory_rows=field_inventory_rows,
        root_summary_rows=root_summary_rows,
        abstract_summary_rows=abstract_summary_rows,
        findings_rows=integrity_finding_rows,
        cluster_rows=cluster_rows,
        dictionary_rows=dictionary_rows,
        parse_warning_rows=parse_warning_rows,
        run_metadata_rows=run_metadata_rows,
    )
    return PipelineResult(
        xml_files=xml_files,
        records=records,
        findings=findings,
        template_rows=template_rows,
        field_inventory_rows=field_inventory_rows,
        root_summary_rows=root_summary_rows,
        abstract_summary_rows=abstract_summary_rows,
        parse_warning_rows=parse_warning_rows,
        dictionary_rows=dictionary_rows,
        run_metadata_rows=run_metadata_rows,
        output_paths=output_paths,
    )


def run_default_pipeline(
    input_dir: str | Path = "WILEY_LIVE_PREFLIGHT_metadata_files",
    tortured_dictionary_path: str | Path = "🤷_tortured.csv",
    output_dir: str | Path = "outputs",
    similarity_threshold: float = 0.88,
    validate_llm: bool = False,
) -> PipelineResult:
    config = PipelineConfig(
        input_dir=Path(input_dir),
        output_dir=Path(output_dir),
        tortured_dictionary_path=Path(tortured_dictionary_path),
        similarity_threshold=similarity_threshold,
        validate_llm=validate_llm,
    )
    return run_pipeline(config)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the ASCO content integrity screening POC.")
    parser.add_argument("--input-dir", default="WILEY_LIVE_PREFLIGHT_metadata_files", help="Folder containing Wiley XML files.")
    parser.add_argument("--tortured-dictionary", default="🤷_tortured.csv", help="Tortured phrase dictionary CSV.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for generated reports.")
    parser.add_argument("--similarity-threshold", type=float, default=0.88, help="Template clustering similarity threshold.")
    parser.add_argument(
        "--validate-llm",
        action="store_true",
        help="Run the GPT-OSS 20B context validator on tortured_phrase and llm_response_trace findings.",
    )
    args = parser.parse_args(argv)

    result = run_default_pipeline(
        input_dir=args.input_dir,
        tortured_dictionary_path=args.tortured_dictionary,
        output_dir=args.output_dir,
        similarity_threshold=args.similarity_threshold,
        validate_llm=args.validate_llm,
    )
    print(json.dumps({key: str(value) for key, value in result.output_paths.items()}, ensure_ascii=False, indent=2))
    return 0
