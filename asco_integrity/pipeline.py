from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aggregation.risk_engine import _risk_from_signals, severity_rank
from .detectors import (
    built_in_llm_rules,
    build_tortured_rule_index,
    candidate_to_finding,
    detect_exact_text_reuse,
    detect_entity_normalized_templates,
    detect_llm_trace_candidates,
    NonsenseCandidateDetector,
    detect_tortured_phrases,
    load_tortured_rules,
)
from .detectors.llm_trace_fusion import fuse_llm_trace_candidates, llm_reviewer_priority
from .detectors.llm_trace_semantic import (
    PROMPT_VERSION as SEMANTIC_PROMPT_VERSION,
    SemanticRunStats,
    detect_semantic_traces,
)
from .models import Finding, ParsedRecord
from .rules import catalogue_metadata
from .detectors.design_contradiction import (
    DesignContradictionFinding,
    LLMDesignContradictionValidator,
    detect_design_contradictions,
)
from .detectors.numerical_contradiction import (
    NumericalContradictionFinding,
    detect_numerical_contradictions,
)
from .detectors.unverifiable_trial import (
    CLINICAL_TRIALS_GOV,
    ClinicalTrialsGovClient,
    TrialVerificationResult,
    detect_unverifiable_trials,
)
from .template_clustering import (
    CONFIDENCE_RANK,
    PairFinding,
    cluster_template_findings,
    merge_pair_findings,
    other_record_id,
)
from .reporting import (
    DICTIONARY_COLUMNS,
    FAMILY_COLUMNS,
    FINDINGS_COLUMNS,
    PAIR_COLUMNS,
    REVIEW_FINDINGS_COLUMNS,
    WARNING_COLUMNS,
    write_csv,
    write_jsonl,
    write_workbook,
)
from .validators import ContextValidator, LLMTraceValidator, apply_llm_trace_validation, build_gpt_oss_client
from .validators.llm_trace_validator import PROMPT_VERSION as LLM_VALIDATION_PROMPT_VERSION
from .utils import dedupe_records, normalize_whitespace, to_pipe_string
from .xml_parser import discover_xml_files, parse_xml_records


@dataclass(slots=True)
class PipelineConfig:
    input_dir: Path
    output_dir: Path
    tortured_dictionary_path: Path
    legacy_similarity_threshold: float = 0.88
    dictionary_version: str = "wiley_tortured_seed_v1"
    validate_llm: bool = False
    detect_llm_semantic: bool = False
    detect_nonsense_candidates: bool = False
    compare_legacy_template_clustering: bool = False
    verify_trials: bool = False


@dataclass(slots=True)
class PipelineResult:
    xml_files: list[Path]
    records: list[ParsedRecord]
    findings: list[Finding]
    template_rows: list[dict[str, Any]]
    template_family_rows: list[dict[str, Any]]
    pair_findings: list[PairFinding]
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


def _integrated_finding(result: Any) -> Finding:
    detail = getattr(result, "contradiction_type", "") or getattr(result, "finding_type", "")
    matched_text = (
        getattr(result, "reported_values", "")
        or getattr(result, "raw_trial_id", "")
        or " vs ".join(filter(None, (getattr(result, "value_1", ""), getattr(result, "value_2", ""))))
        or detail
    )
    return Finding(
        finding_id=result.finding_id,
        record_id=result.record_id,
        source_file=result.source_file,
        detector_type=result.check_type,
        check_type=detail or result.check_type,
        category=result.check_type,
        matched_text=matched_text,
        evidence_snippet=result.evidence,
        section_or_field=(
            getattr(result, "section", "")
            or getattr(result, "section_1", "")
            or getattr(result, "source_section", "")
        ),
        severity=result.severity,
        confidence=result.confidence,
        rule_id=f"{result.check_type}:{detail or result.check_type}",
        validation_status=getattr(result, "validation_status", ""),
        validation_reason=getattr(result, "validation_reason", ""),
        review_status=result.review_status,
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
    pair_findings: list[PairFinding],
    clusters: list[dict[str, Any]],
    llm_rules: list[Any] | None = None,
) -> list[dict[str, Any]]:
    llm_rules = llm_rules or built_in_llm_rules()
    finding_map: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        finding_map[finding.record_id].append(finding)
    pair_map: dict[str, list[PairFinding]] = defaultdict(list)
    for pair in pair_findings:
        pair_map[pair.record_id].append(pair)
        pair_map[pair.matched_record_id].append(pair)
    cluster_map: dict[str, dict[str, Any]] = {row["record_id"]: row for row in clusters if row["cluster_size"] >= 3}
    summary_rows: list[dict[str, Any]] = []
    for record in records:
        record_findings = finding_map.get(record.record_id, [])
        llm_findings = [finding for finding in record_findings if finding.detector_type == "llm_response_trace"]
        llm_priority = llm_reviewer_priority(llm_findings, llm_rules)
        tortured_findings = [finding for finding in record_findings if finding.detector_type == "tortured_phrase"]
        nonsense_findings = [finding for finding in record_findings if finding.detector_type == "nonsense_candidate"]
        numerical_findings = [finding for finding in record_findings if finding.detector_type == "numerical_contradiction"]
        design_findings = [finding for finding in record_findings if finding.detector_type == "design_contradiction"]
        trial_findings = [finding for finding in record_findings if finding.detector_type == "unverifiable_clinical_trial"]
        matched_pairs = pair_map.get(record.record_id, [])
        cluster_row = cluster_map.get(record.record_id)
        template_flag = bool(matched_pairs)
        cluster_flag = cluster_row is not None
        strongest_pair = max(
            matched_pairs,
            key=lambda pair: (
                CONFIDENCE_RANK.get(pair.confidence, 0),
                severity_rank(pair.severity),
                pair.pair_id,
            ),
            default=None,
        )
        template_severity = max(
            (pair.severity for pair in matched_pairs),
            key=severity_rank,
            default="none",
        )
        non_llm_findings = [
            finding for finding in record_findings if finding.detector_type != "llm_response_trace"
        ]
        detector_types = {finding.detector_type for finding in non_llm_findings}
        if llm_priority != "None":
            detector_types.add("llm_response_trace")
        if template_flag:
            detector_types.add("template")
        highest_severity = "none"
        for finding in non_llm_findings:
            if severity_rank(finding.severity) > severity_rank(highest_severity):
                highest_severity = finding.severity
        if severity_rank(llm_priority) > severity_rank(highest_severity):
            highest_severity = llm_priority.lower()
        if strongest_pair:
            if severity_rank(strongest_pair.severity) > severity_rank(highest_severity):
                highest_severity = strongest_pair.severity
        overall_risk = _risk_from_signals(
            highest_signal_severity=highest_severity,
            detector_types=detector_types,
            total_finding_count=len(non_llm_findings) + (1 if llm_priority != "None" else 0) + (1 if template_flag else 0),
            template_cluster_flag=template_flag,
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
                "llm_review_priority": llm_priority,
                "tortured_phrase_flag": "Yes" if tortured_findings else "No",
                "nonsense_candidate_flag": "Yes" if nonsense_findings else "No",
                "numerical_contradiction_flag": "Yes" if numerical_findings else "No",
                "design_contradiction_flag": "Yes" if design_findings else "No",
                "unverifiable_trial_flag": "Yes" if trial_findings else "No",
                "template_cluster_flag": "Yes" if cluster_flag else "No",
                "template_flag": "Yes" if template_flag else "No",
                "template_confidence": strongest_pair.confidence if strongest_pair else "none",
                "template_review_priority": template_severity.title() if template_flag else "None",
                "matched_abstract_count": len({other_record_id(pair, record.record_id) for pair in matched_pairs}),
                "strongest_matched_record_id": other_record_id(strongest_pair, record.record_id) if strongest_pair else "",
                "strongest_matched_source_file": (
                    strongest_pair.matched_source_file
                    if strongest_pair and strongest_pair.record_id == record.record_id
                    else strongest_pair.source_file if strongest_pair else ""
                ),
                "strongest_matched_title": (
                    strongest_pair.matched_title
                    if strongest_pair and strongest_pair.record_id == record.record_id
                    else strongest_pair.title if strongest_pair else ""
                ),
                "strongest_match_pair_id": strongest_pair.pair_id if strongest_pair else "",
                "strongest_match_supporting_types": (
                    strongest_pair.supporting_match_types if strongest_pair else []
                ),
                "strongest_match_sections": strongest_pair.matched_sections if strongest_pair else [],
                "strongest_match_sentence_count": strongest_pair.matched_sentence_count if strongest_pair else 0,
                "strongest_match_shared_text_coverage": strongest_pair.shared_text_coverage if strongest_pair else "",
                "strongest_match_original_text_similarity": strongest_pair.original_text_similarity if strongest_pair else "",
                "strongest_match_masked_skeleton_similarity": strongest_pair.masked_skeleton_similarity if strongest_pair else "",
                "strongest_match_ngram_similarity": strongest_pair.ngram_similarity if strongest_pair else "",
                "strongest_match_high_value_section_similarity": strongest_pair.high_value_section_similarity if strongest_pair else "",
                "strongest_match_weighted_section_similarity": strongest_pair.weighted_section_similarity if strongest_pair else "",
                "strongest_match_variable_substitutions": strongest_pair.variable_substitutions if strongest_pair else "",
                "strongest_match_relationship_context": strongest_pair.relationship_context if strongest_pair else "",
                "strongest_match_evidence_excerpt": strongest_pair.evidence_excerpt if strongest_pair else "",
                "strongest_match_review_status": strongest_pair.review_status if strongest_pair else "",
                "primary_template_pattern": strongest_pair.primary_match_type if strongest_pair else "",
                "matched_sections": " | ".join(sorted({section for pair in matched_pairs for section in pair.matched_sections})),
                "template_family_id": cluster_row["template_cluster_id"] if cluster_row else "",
                "template_family_size": cluster_row["cluster_size"] if cluster_row else 0,
                "template_family_confidence": cluster_row["family_confidence"] if cluster_row else "none",
                "template_evidence_summary": strongest_pair.evidence if strongest_pair else "",
                "llm_trace_count": len(llm_findings),
                "tortured_phrase_count": len(tortured_findings),
                "nonsense_candidate_count": len(nonsense_findings),
                "numerical_contradiction_count": len(numerical_findings),
                "design_contradiction_count": len(design_findings),
                "unverifiable_trial_count": len(trial_findings),
                "template_cluster_id": cluster_row["template_cluster_id"] if cluster_row else "",
                "template_cluster_size": cluster_row["cluster_size"] if cluster_row else 0,
                "total_finding_count": len(record_findings) + (1 if template_flag else 0),
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
        item["confidence"] = round(finding.confidence, 3) if isinstance(finding.confidence, float) else finding.confidence
        item["editor_label"] = "not_reviewed"
        item["editor_notes"] = ""
        rows.append(item)
    return rows


def _pair_finding_rows(pair_findings: list[PairFinding]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pair_findings:
        for side in (False, True):
            rows.append(
                {
                    "finding_id": f"TPL-PAIR-{len(rows) + 1:05d}",
                    "pair_id": pair.pair_id,
                    "record_id": pair.matched_record_id if side else pair.record_id,
                    "matched_record_id": pair.record_id if side else pair.matched_record_id,
                    "source_file": pair.matched_source_file if side else pair.source_file,
                    "matched_source_file": pair.source_file if side else pair.matched_source_file,
                    "title": pair.matched_title if side else pair.title,
                    "matched_title": pair.title if side else pair.matched_title,
                    "detector_type": "template_pair",
                    "check_type": pair.primary_match_type,
                    "primary_match_type": pair.primary_match_type,
                    "category": "template",
                    "matched_text": pair.primary_match_type,
                    "expected_term": "",
                    "evidence_snippet": pair.evidence_excerpt,
                    "evidence_excerpt": pair.evidence_excerpt,
                    "section_or_field": "cross_document",
                    "severity": pair.severity,
                    "confidence": pair.confidence,
                    "validation_status": "",
                    "validation_reason": "",
                    "validated_by": "",
                    "rule_id": pair.pair_id,
                    "template_pattern_type": pair.primary_match_type,
                    "supporting_match_types": pair.supporting_match_types,
                    "matched_sections": pair.matched_sections,
                    "matched_sentence_count": pair.matched_sentence_count,
                    "shared_text_coverage": round(pair.shared_text_coverage, 3),
                    "original_text_similarity": round(pair.original_text_similarity, 3),
                    "masked_skeleton_similarity": round(pair.masked_skeleton_similarity, 3),
                    "ngram_similarity": round(pair.ngram_similarity, 3),
                    "high_value_section_similarity": round(pair.high_value_section_similarity, 3),
                    "weighted_section_similarity": round(pair.weighted_section_similarity, 3),
                    "variable_substitutions": pair.variable_substitutions,
                    "relationship_context": pair.relationship_context,
                    "review_status": pair.review_status,
                    "editor_label": "not_reviewed",
                    "editor_notes": "",
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


def _family_rows(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in clusters:
        if row["cluster_size"] >= 3:
            grouped[row["template_cluster_id"]].append(row)
    rows: list[dict[str, Any]] = []
    for family_id, members in sorted(grouped.items()):
        representative = members[0]
        rows.append(
            {
                "template_family_id": family_id,
                "member_count": representative["cluster_size"],
                "family_confidence": representative["family_confidence"],
                "representative_record_id": representative["representative_record_id"],
                "template_pattern_type": representative["template_pattern_type"],
                "matched_sections": representative["matched_sections"],
                "edge_density": representative["edge_density"],
                "median_pair_confidence": representative["median_pair_confidence"],
                "changed_entity_types": representative["changed_entity_types"],
                "member_ids": sorted(member["record_id"] for member in members),
                "shared_skeleton_excerpt": representative["shared_skeleton_excerpt"],
                "medoid_verification_passed": representative["medoid_verification_passed"],
            }
        )
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
    records = [record for path in xml_files for record in parse_xml_records(path)]
    records, record_id_warnings = dedupe_records(records)
    llm_rules = built_in_llm_rules()
    tortured_rules = load_tortured_rules(config.tortured_dictionary_path)
    tortured_index = build_tortured_rule_index(tortured_rules)
    llm_client = (
        build_gpt_oss_client()
        if (config.detect_nonsense_candidates or config.detect_llm_semantic or config.validate_llm)
        else None
    )
    nonsense_detector = NonsenseCandidateDetector(llm_client) if config.detect_nonsense_candidates else None

    findings: list[Finding] = []
    deterministic_candidates = []
    for record in records:
        deterministic_candidates.extend(detect_llm_trace_candidates(record, llm_rules))
        tortured_findings = detect_tortured_phrases(record, tortured_rules, tortured_index)
        findings.extend(tortured_findings)
        if nonsense_detector:
            findings.extend(nonsense_detector.detect(record, tortured_findings))

    semantic_candidates = []
    semantic_stats = SemanticRunStats()
    if config.detect_llm_semantic and llm_client is not None:
        semantic_candidates, semantic_stats = detect_semantic_traces(llm_client, records)
    llm_candidates = fuse_llm_trace_candidates([*deterministic_candidates, *semantic_candidates])
    llm_validator = LLMTraceValidator(llm_client) if config.validate_llm and llm_client is not None else None
    apply_llm_trace_validation(llm_candidates, llm_validator)
    findings.extend(candidate_to_finding(candidate) for candidate in llm_candidates)

    comparable_records = [
        record
        for record in records
        if record.parse_status != "failed" and (record.title.strip() or record.abstract_text.strip())
    ]
    numerical_results = detect_numerical_contradictions(comparable_records)
    design_validator = LLMDesignContradictionValidator(llm_client) if config.validate_llm else None
    design_results = detect_design_contradictions(comparable_records, validator=design_validator)
    trial_results = detect_unverifiable_trials(
        comparable_records,
        registry_clients={
            CLINICAL_TRIALS_GOV: ClinicalTrialsGovClient(
                cache_dir=config.output_dir / ".trial_registry_cache",
                offline_cache_only=not config.verify_trials,
            )
        },
    )
    findings.extend(
        _integrated_finding(result)
        for result in [*numerical_results, *design_results, *trial_results]
        if result.check_triggered
    )

    ordered_findings = sorted(findings, key=_finding_sort_key)
    for index, finding in enumerate(ordered_findings, start=1):
        if not finding.finding_id:
            finding.finding_id = f"FND-{index:05d}"

    if config.validate_llm and ordered_findings:
        if llm_client is None:
            llm_client = build_gpt_oss_client()
        validator = ContextValidator(client=llm_client)
        for finding in ordered_findings:
            if finding.detector_type == "llm_response_trace":
                continue
            if finding.detector_type not in validator.applies_to:
                continue
            result = validator.validate(finding)
            finding.validation_status = result.status
            finding.validation_reason = result.reason
            finding.validated_by = f"{result.model_id}:{result.prompt_version}"

    exact_template_findings = detect_exact_text_reuse(records)
    entity_template_findings = detect_entity_normalized_templates(records)
    pair_findings = merge_pair_findings(exact_template_findings, entity_template_findings)
    template_rows = cluster_template_findings(pair_findings, records)
    field_inventory_rows, root_summary_rows = _inventory_rows(records)
    abstract_summary_rows = _aggregate_findings(records, findings, pair_findings, template_rows, llm_rules)
    findings_rows = _findings_rows(findings)
    titles_by_record = {record.record_id: record.title for record in records}
    for row in findings_rows:
        row["title"] = titles_by_record.get(row["record_id"], "")
    template_finding_rows = _pair_finding_rows(pair_findings)
    integrity_finding_rows = sorted(findings_rows + template_finding_rows, key=_finding_row_sort_key)
    family_rows = _family_rows(template_rows)
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
    parse_warning_rows.extend(
        {
            "source_file": next(
                (record.source_file for record in records if record.record_id == record_id),
                "",
            ),
            "record_id": record_id,
            "warning_code": "llm_semantic_batch_failed",
            "warning_message": "Semantic response-trace coverage is incomplete for this record.",
            "field_name": "abstract_text",
            "severity": "warning",
            "evidence_snippet": "",
            "schema_type": "",
        }
        for record_id in semantic_stats.failed_record_ids
    )
    dictionary_rows = _dictionary_rows([rule.to_dict() for rule in llm_rules], [rule.to_dict() for rule in tortured_rules])
    now = datetime.now(timezone.utc)
    catalogue_version, catalogue_checksum = catalogue_metadata()
    llm_findings = [finding for finding in findings if finding.detector_type == "llm_response_trace"]
    run_metadata_rows: list[tuple[str, Any]] = [
        ("run_date_utc", now.isoformat()),
        ("input_folder", str(config.input_dir)),
        ("output_folder", str(config.output_dir)),
        ("total_files", len(xml_files)),
        ("parsed_successfully", sum(1 for record in records if record.parse_status == "parsed")),
        ("parsed_with_warnings", sum(1 for record in records if record.parse_status == "parsed_with_warnings")),
        ("failed_files", sum(1 for record in records if record.parse_status == "failed")),
        ("llm_rule_count", len(llm_rules)),
        ("llm_trace_preprocessing_version", "lossless_trace_blocks_v1"),
        ("llm_trace_lossless_blocks_enabled", True),
        ("llm_trace_preprocessing_fallback_count", sum(record.trace_preprocessing_fallback for record in records)),
        ("llm_rule_catalogue_version", catalogue_version),
        ("llm_rule_catalogue_checksum", catalogue_checksum),
        ("llm_deterministic_rule_count", len(llm_rules)),
        ("llm_semantic_enabled", config.detect_llm_semantic),
        ("llm_semantic_model_id", getattr(llm_client, "model_name", "") if config.detect_llm_semantic else ""),
        ("llm_semantic_prompt_version", SEMANTIC_PROMPT_VERSION),
        ("llm_semantic_batch_count", semantic_stats.batch_count),
        ("llm_semantic_batch_failure_count", semantic_stats.batch_failure_count),
        ("llm_deterministic_finding_count", len(deterministic_candidates)),
        ("llm_semantic_variant_count", sum(finding.check_type == "semantic_variant" for finding in llm_findings)),
        ("llm_novel_candidate_count", sum(finding.check_type == "novel_pattern_candidate" for finding in llm_findings)),
        ("llm_validation_enabled", config.validate_llm),
        ("llm_validation_model_id", getattr(llm_client, "model_name", "") if config.validate_llm else ""),
        ("llm_validation_prompt_version", LLM_VALIDATION_PROMPT_VERSION),
        ("llm_confirmed_count", sum(finding.validation_status == "confirmed" for finding in llm_findings)),
        ("llm_rejected_count", sum(finding.validation_status == "rejected" for finding in llm_findings)),
        ("llm_uncertain_count", sum(finding.validation_status == "uncertain" for finding in llm_findings)),
        ("llm_supporting_only_count", sum(finding.review_status == "supporting_only" for finding in llm_findings)),
        ("tortured_rule_count", len(tortured_rules)),
        ("dictionary_version", config.dictionary_version),
        ("tortured_dictionary_path", str(config.tortured_dictionary_path)),
        ("legacy_similarity_threshold", config.legacy_similarity_threshold),
        ("nonsense_candidate_detection", "enabled" if config.detect_nonsense_candidates else "disabled"),
        ("clinical_trial_registry_lookup", "enabled" if config.verify_trials else "local_checks_only"),
        ("limitations", "Rule-based screening flags explicit LLM response traces, known tortured phrases, and repeated abstract skeletons; optional GPT-OSS stages only annotate candidates, and the pipeline does not detect AI-generated authorship."),
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
            "excluded_sections",
            "parse_status",
            "parse_warnings",
        ],
    )
    output_paths["findings_csv"] = write_csv(
        output_dir / "integrity_findings.csv",
        integrity_finding_rows,
        REVIEW_FINDINGS_COLUMNS,
    )
    output_paths["detailed_findings_csv"] = write_csv(
        output_dir / "detailed_findings.csv",
        integrity_finding_rows,
        FINDINGS_COLUMNS,
    )
    output_paths["numerical_contradictions_csv"] = write_csv(
        output_dir / "numerical_contradictions.csv",
        [result.to_dict() for result in numerical_results],
        [field.name for field in fields(NumericalContradictionFinding)],
    )
    output_paths["design_contradictions_csv"] = write_csv(
        output_dir / "design_contradictions.csv",
        [result.to_dict() for result in design_results],
        [field.name for field in fields(DesignContradictionFinding)],
    )
    output_paths["trial_verification_csv"] = write_csv(
        output_dir / "trial_verification.csv",
        [result.to_dict() for result in trial_results],
        [field.name for field in fields(TrialVerificationResult)],
    )
    output_paths["template_pairs_csv"] = write_csv(
        output_dir / "template_pair_findings.csv",
        template_finding_rows,
        PAIR_COLUMNS,
    )
    if config.compare_legacy_template_clustering:
        from .template_detection import cluster_templates
        output_paths["legacy_template_comparison_jsonl"] = write_jsonl(
            output_dir / "legacy_template_comparison.jsonl",
            [row.to_dict() for row in cluster_templates(records, similarity_threshold=config.legacy_similarity_threshold)],
        )
    output_paths["clusters_csv"] = write_csv(
        output_dir / "template_clusters.csv",
        family_rows,
        FAMILY_COLUMNS,
    )
    output_paths["dictionary_csv"] = write_csv(
        output_dir / "pattern_dictionary.csv",
        dictionary_rows,
        DICTIONARY_COLUMNS,
    )
    output_paths["warnings_csv"] = write_csv(
        output_dir / "parse_warnings.csv",
        parse_warning_rows,
        WARNING_COLUMNS,
    )
    output_paths["metadata_json"] = write_jsonl(output_dir / "run_metadata.jsonl", [{"key": key, "value": value} for key, value in run_metadata_rows])
    output_paths["workbook"] = write_workbook(
        output_dir / "content_integrity_screening_poc.xlsx",
        inventory_rows=field_inventory_rows,
        root_summary_rows=root_summary_rows,
        abstract_summary_rows=abstract_summary_rows,
        findings_rows=integrity_finding_rows,
        pair_rows=template_finding_rows,
        cluster_rows=family_rows,
        dictionary_rows=dictionary_rows,
        parse_warning_rows=parse_warning_rows,
        run_metadata_rows=run_metadata_rows,
    )
    return PipelineResult(
        xml_files=xml_files,
        records=records,
        findings=findings,
        template_rows=template_rows,
        template_family_rows=family_rows,
        pair_findings=pair_findings,
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
    legacy_similarity_threshold: float = 0.88,
    validate_llm: bool = False,
    detect_llm_semantic: bool = False,
    detect_nonsense_candidates: bool = False,
    compare_legacy_template_clustering: bool = False,
    verify_trials: bool = False,
    similarity_threshold: float | None = None,
) -> PipelineResult:
    if similarity_threshold is not None:
        legacy_similarity_threshold = similarity_threshold
    config = PipelineConfig(
        input_dir=Path(input_dir),
        output_dir=Path(output_dir),
        tortured_dictionary_path=Path(tortured_dictionary_path),
        legacy_similarity_threshold=legacy_similarity_threshold,
        validate_llm=validate_llm,
        detect_llm_semantic=detect_llm_semantic,
        detect_nonsense_candidates=detect_nonsense_candidates,
        compare_legacy_template_clustering=compare_legacy_template_clustering,
        verify_trials=verify_trials,
    )
    return run_pipeline(config)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the ASCO content integrity screening POC.")
    parser.add_argument("--input-dir", default="WILEY_LIVE_PREFLIGHT_metadata_files", help="Folder containing Wiley XML files.")
    parser.add_argument("--tortured-dictionary", default="🤷_tortured.csv", help="Tortured phrase dictionary CSV.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for generated reports.")
    parser.add_argument(
        "--legacy-similarity-threshold",
        type=float,
        default=0.88,
        help="Legacy threshold used only with --compare-legacy-template-clustering.",
    )
    parser.add_argument(
        "--detect-llm-semantic",
        action="store_true",
        help="Run opt-in semantic discovery for known variants and novel LLM response residue.",
    )
    parser.add_argument(
        "--validate-llm",
        action="store_true",
        help="Run the GPT-OSS 20B context validator on tortured_phrase and llm_response_trace findings.",
    )
    parser.add_argument(
        "--detect-nonsense-candidates",
        action="store_true",
        help="Run the opt-in GPT-OSS sentence-level nonsense candidate detector.",
    )
    parser.add_argument(
        "--compare-legacy-template-clustering",
        action="store_true",
        help="Write legacy clustering results to an internal comparison file only.",
    )
    parser.add_argument(
        "--verify-trials",
        action="store_true",
        help="Verify valid NCT identifiers against ClinicalTrials.gov; local trial-reference checks always run.",
    )
    args = parser.parse_args(argv)

    result = run_default_pipeline(
        input_dir=args.input_dir,
        tortured_dictionary_path=args.tortured_dictionary,
        output_dir=args.output_dir,
        legacy_similarity_threshold=args.legacy_similarity_threshold,
        validate_llm=args.validate_llm,
        detect_llm_semantic=args.detect_llm_semantic,
        detect_nonsense_candidates=args.detect_nonsense_candidates,
        compare_legacy_template_clustering=args.compare_legacy_template_clustering,
        verify_trials=args.verify_trials,
    )
    print(json.dumps({key: str(value) for key, value in result.output_paths.items()}, ensure_ascii=False, indent=2))
    return 0
