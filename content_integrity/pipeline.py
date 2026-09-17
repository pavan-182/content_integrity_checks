from __future__ import annotations

import json
import logging
import os
import platform
import resource
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .aggregation.risk_engine import _risk_from_signals, severity_rank
from .checkpoint import (
    CHECKPOINT_VERSION,
    CheckpointStore,
    IncompatibleCheckpointError,
    atomic_write_json,
    code_sha256,
    file_sha256,
)
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
    DEFAULT_MAX_CONCURRENT_BATCHES,
    SemanticRunStats,
    detect_semantic_traces,
)
from .detectors.nonsense_candidate import NonsenseRunStats
from .models import Finding, OperationalIssue, ParsedRecord
from .rules import catalogue_metadata
from .detectors.design_contradiction import (
    PROMPT_VERSION as DESIGN_CONTRADICTION_PROMPT_VERSION,
    RULE_TABLE_VERSION as DESIGN_CONTRADICTION_RULE_TABLE_VERSION,
    LLMDesignContradictionValidator,
    detect_design_contradictions,
)
from .detectors.numerical_contradiction import (
    detect_numerical_contradictions,
)
from .detectors.unverifiable_trial import (
    CLINICAL_TRIALS_GOV,
    ClinicalTrialsGovClient,
    TrialVerificationResult,
    detect_unverifiable_trials,
)
from .enriched_reporting import (
    REPORT_VERSION,
    build_enriched_reports,
    directional_finding_rows,
)
from .editorial_scoring import SCORING_VERSION as EDITORIAL_SCORING_VERSION
from .entity_extraction import (
    VOCABULARY_VERSION as ENTITY_VOCABULARY_VERSION,
    ENTITY_PROMPT_VERSION,
    EntityExtractor,
)
from .family_clustering import FAMILY_VERSION as TEMPLATE_FAMILY_VERSION
from .pair_classification import CLASSIFIER_VERSION as TEMPLATE_PAIR_CLASSIFIER_VERSION
from .signal_validation import VALIDATION_VERSION as TEMPLATE_SIGNAL_VALIDATION_VERSION
from .template_clustering import (
    CONFIDENCE_RANK,
    PairFinding,
    cluster_template_findings,
    merge_pair_findings,
    other_record_id,
)
from .template_features import FEATURE_VERSION as TEMPLATE_FEATURE_VERSION, build_template_features
from .reporting import (
    AUTHORSHIP_CHECKS,
    build_content_integrity_frontend_json,
    build_integrated_content_integrity_json,
    _load_authorship_checks,
    _normalized_doi,
    write_workbook,
)
from .reconciliation import ReconciliationError, reconcile_outputs
from .validators import ContextValidator, LLMTraceValidator, apply_llm_trace_validation, build_gpt_oss_client
from .validators.context_validator import GatewayRequestError, TruncatedResponseError
from .validators.llm_trace_validator import PROMPT_VERSION as LLM_VALIDATION_PROMPT_VERSION
from .utils import dedupe_records, normalize_whitespace, to_pipe_string
from .xml_parser import discover_xml_files, parse_xml_records


logger = logging.getLogger(__name__)

RISK_INELIGIBLE_CHECK_TYPES = {"unsupported_registry_manual_verification"}
# Components that consume `comparable_records`; a record excluded there means none of these
# checks ran for it, so each must get its own operational issue (component names match what
# reporting.py's _component_failed() looks up) instead of silently reading as a clean pass.
COMPARABILITY_GATED_COMPONENTS = ("numerical_contradiction", "design_contradiction", "unverifiable_clinical_trial")


def _normalized_validation_status(finding: Finding) -> str:
    return finding.normalized_validation_status


def _is_risk_eligible_finding(finding: Finding) -> bool:
    return finding.active and finding.check_type not in RISK_INELIGIBLE_CHECK_TYPES


def _authorship_signal(
    record: ParsedRecord, authorship_checks_by_key: dict[str, dict[str, dict[str, str]]]
) -> tuple[str, list[str], list[str]]:
    """Reduce a record's authorship checks (final_json.json) to a risk severity plus the
    triggered check labels/evidence, using the same LOW=baseline convention as the xlsx
    'Why Flagged' text: only MEDIUM/HIGH levels are an actual signal."""
    checks = (
        authorship_checks_by_key.get(_normalized_doi(record.doi))
        or authorship_checks_by_key.get(record.record_id.lower())
        or {}
    )
    reasons: list[str] = []
    evidence: list[str] = []
    severity = "none"
    for check_name, label in AUTHORSHIP_CHECKS:
        level = str(checks.get(check_name, {}).get("level", "")).upper()
        if level not in {"MEDIUM", "HIGH"}:
            continue
        reasons.append(label)
        comment = checks.get(check_name, {}).get("comment", "")
        evidence.append(f"{label}: {comment}" if comment else label)
        if level == "HIGH":
            severity = "high"
        elif severity != "high":
            severity = "medium"
    return severity, reasons, evidence


@dataclass(slots=True)
class PipelineConfig:
    input_dir: Path
    output_dir: Path
    tortured_dictionary_path: Path
    authorship_json_path: Path | None = None
    dictionary_version: str = "wiley_tortured_seed_v1"
    validate_llm: bool = False
    detect_llm_semantic: bool = False
    detect_nonsense_candidates: bool = False
    verify_trials: bool = False
    llm_max_concurrency: int = DEFAULT_MAX_CONCURRENT_BATCHES
    offline: bool = False
    discard_checkpoint: bool = False


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
    operational_issues: list[OperationalIssue]
    output_paths: dict[str, Path]
    run_id: str = ""
    run_summary: dict[str, Any] = field(default_factory=dict)
    run_metrics: dict[str, Any] = field(default_factory=dict)


def _finding_sort_key(finding: Finding) -> tuple[str, str, str, str, int, int]:
    return (
        finding.record_id,
        finding.detector_type,
        finding.rule_id,
        finding.section_or_field,
        finding.severity,
        finding.finding_id,
    )


def _git_revision() -> tuple[str, bool]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], check=True, capture_output=True, text=True,
        ).stdout.strip())
        return sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return "unknown", False


def _input_manifest_checksum(paths: list[Path], root: Path) -> str:
    digest = sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


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
        review_reason=getattr(result, "review_reason", ""),
        calculated_value=getattr(result, "calculated_value", None),
        difference=getattr(result, "difference", None),
        tolerance=getattr(result, "tolerance", None),
        registry_name=getattr(result, "registry_name", ""),
        normalized_trial_id=getattr(result, "normalized_trial_id", ""),
        format_valid=getattr(result, "format_valid", None),
        verification_status=getattr(result, "verification_status", ""),
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
            "field_name": "trial_ids",
            "primary_xml_path": "abstract/title text matched as NCT########",
            "present_count": sum(1 for record in records if record.trial_ids),
            "present_pct": f"{sum(1 for record in records if record.trial_ids) / total * 100:.1f}%",
            "example_value": next((to_pipe_string(record.trial_ids) for record in records if record.trial_ids), ""),
            "useful_for_poc": "yes",
            "notes": "Captured from title and abstract text for provenance and later verification.",
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
    *,
    enriched_pair_rows: list[dict[str, object]] | None = None,
    enriched_abstract_rows: list[dict[str, object]] | None = None,
    authorship_checks_by_key: dict[str, dict[str, dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    llm_rules = llm_rules or built_in_llm_rules()
    authorship_checks_by_key = authorship_checks_by_key or {}
    finding_map: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        finding_map[finding.record_id].append(finding)
    pair_map: dict[str, list[PairFinding]] = defaultdict(list)
    for pair in pair_findings:
        pair_map[pair.record_id].append(pair)
        pair_map[pair.matched_record_id].append(pair)
    cluster_map: dict[str, dict[str, Any]] = {row["record_id"]: row for row in clusters if row["cluster_size"] >= 3}
    enriched_linked: dict[str, list[dict[str, object]]] = defaultdict(list)
    for pair in enriched_pair_rows or []:
        if pair["review_priority"] != "None":
            enriched_linked[str(pair["left_record_id"])].append(pair)
            enriched_linked[str(pair["right_record_id"])].append(pair)
    enriched_abstract_lookup = {
        str(row["record_id"]): row for row in enriched_abstract_rows or []
    }
    summary_rows: list[dict[str, Any]] = []
    for record in records:
        record_findings = finding_map.get(record.record_id, [])
        llm_findings = [finding for finding in record_findings if finding.detector_type == "llm_response_trace"]
        active_findings = [finding for finding in record_findings if finding.active]
        review_candidates = [
            finding
            for finding in record_findings
            if (
                finding.detector_type != "nonsense_candidate"
                and finding.review_candidate
                and finding.review_status != "supporting_only"
            )
        ]
        active_llm_findings = [
            finding for finding in llm_findings if finding.active
        ]
        llm_review_priority = llm_reviewer_priority(llm_findings, llm_rules)
        llm_risk_priority = llm_reviewer_priority(active_llm_findings, llm_rules)
        tortured_findings = [finding for finding in record_findings if finding.detector_type == "tortured_phrase"]
        tortured_status_counts = Counter(_normalized_validation_status(finding) for finding in tortured_findings)
        nonsense_findings = [finding for finding in record_findings if finding.detector_type == "nonsense_candidate"]
        numerical_findings = [finding for finding in record_findings if finding.detector_type == "numerical_contradiction"]
        design_findings = [finding for finding in record_findings if finding.detector_type == "design_contradiction"]
        trial_findings = [finding for finding in record_findings if finding.detector_type == "unverifiable_clinical_trial"]
        matched_pairs = pair_map.get(record.record_id, [])
        risk_pairs = [pair for pair in matched_pairs if pair.pair_classification == "possible_template_reuse"]
        related_pairs = [pair for pair in matched_pairs if pair.pair_classification != "possible_template_reuse"]
        cluster_row = cluster_map.get(record.record_id)
        template_flag = bool(risk_pairs)
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
            (pair.severity for pair in risk_pairs),
            key=severity_rank,
            default="none",
        )
        strongest_risk_pair = max(
            risk_pairs,
            key=lambda pair: (
                CONFIDENCE_RANK.get(pair.confidence, 0),
                severity_rank(pair.severity),
                pair.pair_id,
            ),
            default=None,
        )
        enriched_pairs = enriched_linked.get(record.record_id, [])
        enriched_risk_pairs = [
            pair for pair in enriched_pairs
            if pair["pair_class"] in {"possible_template_reuse", "possible_related_duplicate"}
        ]
        enriched_strongest = max(
            enriched_pairs,
            key=lambda pair: (
                {"None": 0, "Low": 1, "Medium": 2, "High": 3}[str(pair["review_priority"])],
                float(pair["editorial_score"]),
            ),
            default=None,
        )
        enriched_strongest_risk = max(
            enriched_risk_pairs,
            key=lambda pair: (
                {"None": 0, "Low": 1, "Medium": 2, "High": 3}[str(pair["review_priority"])],
                float(pair["editorial_score"]),
            ),
            default=None,
        )
        if enriched_pair_rows is not None:
            template_flag = bool(enriched_risk_pairs)
            template_severity = (
                str(enriched_strongest_risk["review_priority"]).lower()
                if enriched_strongest_risk else "none"
            )
        risk_eligible_non_llm_findings = [
            finding
            for finding in record_findings
            if finding.detector_type != "llm_response_trace" and _is_risk_eligible_finding(finding)
        ]
        authorship_severity, authorship_reasons, authorship_evidence = _authorship_signal(
            record, authorship_checks_by_key
        )
        authorship_flag = authorship_severity != "none"
        detector_types = {finding.detector_type for finding in risk_eligible_non_llm_findings}
        if llm_risk_priority != "None":
            detector_types.add("llm_response_trace")
        if template_flag:
            detector_types.add("template")
        if authorship_flag:
            detector_types.add("authorship")
        highest_severity = "none"
        for finding in risk_eligible_non_llm_findings:
            if severity_rank(finding.severity) > severity_rank(highest_severity):
                highest_severity = finding.severity
        if severity_rank(llm_risk_priority) > severity_rank(highest_severity):
            highest_severity = llm_risk_priority.lower()
        if template_flag and severity_rank(template_severity) > severity_rank(highest_severity):
            highest_severity = template_severity
        if authorship_flag and severity_rank(authorship_severity) > severity_rank(highest_severity):
            highest_severity = authorship_severity
        risk_finding_count = (
            len(risk_eligible_non_llm_findings)
            + (1 if llm_risk_priority != "None" else 0)
            + (1 if template_flag else 0)
            + (1 if authorship_flag else 0)
        )
        overall_risk = _risk_from_signals(
            highest_signal_severity=highest_severity,
            detector_types=detector_types,
            total_finding_count=risk_finding_count,
            template_cluster_flag=template_flag,
        )
        review_required = overall_risk != "None"
        if review_candidates or any(
            finding.check_type in RISK_INELIGIBLE_CHECK_TYPES for finding in active_findings
        ):
            review_required = True
        review_reason = ""
        if overall_risk != "None":
            review_reason = "Potential content integrity issue detected. Manual review recommended."
        elif review_required:
            review_reason = "An unconfirmed or manual-verification candidate requires editor review."
        summary_rows.append(
            {
                "record_id": record.record_id,
                "source_file": record.source_file,
                "title": record.title,
                "primary_author": record.primary_author,
                "doi": record.doi,
                "journal": record.journal,
                "publication_year": record.publication_year,
                "article_type": record.article_type,
                "authors": to_pipe_string(record.authors),
                "affiliations": to_pipe_string(record.affiliations),
                "keywords": to_pipe_string(record.keywords),
                "trial_ids": to_pipe_string(record.trial_ids),
                "schema_type": record.schema_type,
                "abstract_section_count": record.abstract_section_count,
                "structured_abstract": record.structured_abstract,
                "parse_status": record.parse_status,
                "parse_warnings": to_pipe_string([warning.warning_code for warning in record.parse_warnings]),
                "llm_trace_flag": "Yes" if active_llm_findings else "No",
                "llm_review_priority": llm_review_priority,
                # Review priority covers every trace including unvalidated ones; risk priority
                # counts only active findings and is what feeds total_finding_count above. Both
                # are surfaced so a "Low review priority, 0 findings" row reconciles.
                "llm_risk_priority": llm_risk_priority,
                "tortured_phrase_flag": "Yes" if any(finding.active for finding in tortured_findings) else "No",
                "nonsense_candidate_flag": "Yes" if any(finding.active for finding in nonsense_findings) else "No",
                "numerical_contradiction_flag": "Yes" if any(finding.active for finding in numerical_findings) else "No",
                "design_contradiction_flag": "Yes" if any(finding.active for finding in design_findings) else "No",
                "unverifiable_trial_flag": "Yes" if any(finding.active for finding in trial_findings) else "No",
                "authorship_flag": "Yes" if authorship_flag else "No",
                "authorship_review_priority": authorship_severity.title() if authorship_flag else "None",
                "authorship_reasons": to_pipe_string(authorship_reasons),
                "authorship_evidence": to_pipe_string(authorship_evidence),
                "template_cluster_flag": "Yes" if cluster_flag else "No",
                "template_flag": "Yes" if template_flag else "No",
                "related_or_companion_flag": "Yes" if related_pairs else "No",
                "template_confidence": strongest_risk_pair.confidence if strongest_risk_pair else "none",
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
                "strongest_pair_classification": strongest_pair.pair_classification if strongest_pair else "",
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
                "tortured_confirmed_count": tortured_status_counts["confirmed"],
                "tortured_rejected_count": tortured_status_counts["rejected"],
                "tortured_uncertain_count": tortured_status_counts["uncertain"],
                "tortured_validation_failed_count": tortured_status_counts["validation_failed"],
                "tortured_candidate_count": tortured_status_counts["candidate"],
                "tortured_not_validated_count": tortured_status_counts["not_validated"],
                "nonsense_candidate_count": len(nonsense_findings),
                "numerical_contradiction_count": len(numerical_findings),
                "design_contradiction_count": len(design_findings),
                "unverifiable_trial_count": len(trial_findings),
                "detected_finding_count": len(record_findings),
                "active_finding_count": len(active_findings),
                "review_candidate_count": len(review_candidates),
                "template_cluster_id": cluster_row["template_cluster_id"] if cluster_row else "",
                "template_cluster_size": cluster_row["cluster_size"] if cluster_row else 0,
                "total_finding_count": risk_finding_count,
                "highest_severity": highest_severity.title() if highest_severity != "none" else "None",
                "overall_content_risk": overall_risk,
                "review_required": "Yes" if review_required else "No",
                "review_reason": review_reason,
            }
        )
        if enriched_pair_rows is not None:
            abstract = enriched_abstract_lookup.get(record.record_id, {})
            strongest = enriched_strongest
            strongest_risk = enriched_strongest_risk
            summary_rows[-1].update({
                "template_flag": "Yes" if template_flag else "No",
                "related_or_companion_flag": "Yes" if any(
                    pair["pair_class"] in {
                        "possible_related_work", "possible_companion_analysis", "possible_related_duplicate",
                    }
                    for pair in enriched_pairs
                ) else "No",
                "template_confidence": str(strongest_risk["review_priority"]).lower() if strongest_risk else "none",
                "template_review_priority": strongest_risk["review_priority"] if strongest_risk else "None",
                "matched_abstract_count": len(enriched_pairs),
                "strongest_matched_record_id": (
                    strongest["right_record_id"] if strongest and strongest["left_record_id"] == record.record_id
                    else strongest["left_record_id"] if strongest else ""
                ),
                "strongest_matched_source_file": "",
                "strongest_matched_title": (
                    strongest["right_title"] if strongest and strongest["left_record_id"] == record.record_id
                    else strongest["left_title"] if strongest else ""
                ),
                "strongest_match_pair_id": strongest["pair_id"] if strongest else "",
                "strongest_match_supporting_types": strongest.get("supporting_evidence", "") if strongest else "",
                "strongest_match_sections": strongest.get("strongest_section", "") if strongest else "",
                "strongest_match_sentence_count": "",
                "strongest_match_shared_text_coverage": "",
                "strongest_match_original_text_similarity": strongest.get("original_body_similarity", "") if strongest else "",
                "strongest_match_masked_skeleton_similarity": strongest.get("masked_body_similarity", "") if strongest else "",
                "strongest_match_ngram_similarity": "",
                "strongest_match_high_value_section_similarity": strongest.get("strongest_masked_section_similarity", "") if strongest else "",
                "strongest_match_weighted_section_similarity": "",
                "strongest_match_variable_substitutions": strongest.get("likely_substitutions", "") if strongest else "",
                "strongest_match_relationship_context": strongest.get("context_interpretation", "") if strongest else "",
                "strongest_pair_classification": strongest["pair_class"] if strongest else "",
                "strongest_match_evidence_excerpt": strongest.get("direct_evidence", "") if strongest else "",
                "strongest_match_review_status": "candidate" if strongest else "",
                "primary_template_pattern": strongest.get("primary_evidence", "") if strongest else "",
                "matched_sections": strongest.get("strongest_section", "") if strongest else "",
                "template_cluster_flag": "Yes" if abstract.get("family_id") else "No",
                "template_family_id": abstract.get("family_id", ""),
                "template_family_size": abstract.get("family_size", 0),
                "template_family_confidence": (
                    "high" if float(abstract.get("family_edge_score", 0)) >= 0.85 else "medium"
                ) if abstract.get("family_id") else "none",
                "template_evidence_summary": strongest.get("rule_path", "") if strongest else "",
            })
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


class _RunLog(logging.LoggerAdapter):
    """Prefix every line with the run ID; lines carry IDs, stages, and categories, never text."""

    def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
        kwargs["extra"] = {**self.extra, **kwargs.get("extra", {})}
        return f"run={self.extra['run_id']} {msg}", kwargs


def _new_run_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def _peak_rss_mb() -> float:
    # ru_maxrss is kilobytes on Linux, the supported deployment platform.
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


class _RunMetrics:
    def __init__(self, log: _RunLog) -> None:
        self.log = log
        self.stages: list[dict[str, Any]] = []
        self.started = time.perf_counter()
        self.cpu_started = _cpu_seconds()

    @contextmanager
    def stage(self, name: str, issues: list[OperationalIssue] | None = None):
        """Time one stage (wall, CPU, peak RSS, new issues). A long run must not be silent."""
        start, cpu_start = time.perf_counter(), _cpu_seconds()
        issues_before = len(issues) if issues is not None else 0
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.stages.append({
                "stage": name,
                "wall_seconds": round(elapsed, 3),
                "cpu_seconds": round(_cpu_seconds() - cpu_start, 3),
                "peak_rss_mb": _peak_rss_mb(),
                "operational_issues_added": (len(issues) - issues_before) if issues is not None else 0,
            })
            # `extra` carries the timing structurally so a benchmark can collect it with a
            # logging handler instead of regexing the rendered message.
            self.log.info("%s: done in %.1fs", name, elapsed, extra={"stage": name, "seconds": elapsed})

    def restored(self, name: str) -> None:
        self.stages.append({"stage": name, "restored_from_checkpoint": True})
        self.log.info("%s: restored from checkpoint", name)


def _safe_message(exc: BaseException) -> str:
    # Gateway messages never contain request content. Other messages are truncated to one line;
    # they appear only in reports (which already carry evidence text), never in log lines.
    return (normalize_whitespace(str(exc)) or type(exc).__name__)[:200]


def _issue_from_exception(component: str, exc: BaseException, record: ParsedRecord | None = None) -> OperationalIssue:
    if isinstance(exc, GatewayRequestError):
        category, retryable, retries = exc.category, exc.retryable, max(exc.attempts - 1, 0)
    elif isinstance(exc, TruncatedResponseError):
        category, retryable, retries = "invalid_response", False, 0
    elif component == "gpt_oss_model":
        category, retryable, retries = "configuration", False, 0
    else:
        category, retryable, retries = "processing_error", False, 0
    return OperationalIssue(
        component=component,
        error_type=type(exc).__name__,
        message=_safe_message(exc),
        record_id=record.record_id if record else "",
        source_file=record.source_file if record else "",
        retry_count=retries,
        recoverable=retryable,
        error_category=category,
    )


def _run_detector(
    component: str,
    operation: Any,
    operational_issues: list[OperationalIssue],
    default: Any,
    log: _RunLog,
    record: ParsedRecord | None = None,
) -> Any:
    try:
        return operation()
    except Exception as exc:  # Detector/model boundaries must be visible in completed reports.
        issue = _issue_from_exception(component, exc, record)
        operational_issues.append(issue)
        log.warning("%s: failed record=%s category=%s (%s)", component, issue.record_id or "-", issue.error_category, issue.error_type)
        return default


def _run_corpus_detector(
    component: str,
    operation: Any,
    records: list[ParsedRecord],
    operational_issues: list[OperationalIssue],
    default: Any,
    log: _RunLog,
) -> Any:
    """Run a whole-batch detector without letting one record's failure remove it for everyone.

    `operation(subset)` runs the detector over a record subset. On failure each record is probed
    alone; records whose probe fails are excluded with their own issue and the detector reruns
    on the rest. Only a failure that no single record reproduces is reported run-wide.
    """
    try:
        return operation(records)
    except Exception as exc:
        failure: Exception = exc
        log.warning("%s: failed (%s); probing records to isolate the cause", component, type(exc).__name__)
    excluded: list[tuple[ParsedRecord, Exception]] = []
    for record in records:
        try:
            operation([record])
        except Exception as exc:
            excluded.append((record, exc))
    if excluded:
        for record, exc in excluded:
            operational_issues.append(_issue_from_exception(component, exc, record))
            log.warning("%s: excluded record=%s (%s)", component, record.record_id, type(exc).__name__)
        excluded_ids = {record.record_id for record, _ in excluded}
        try:
            return operation([record for record in records if record.record_id not in excluded_ids])
        except Exception as exc:
            failure = exc
    operational_issues.append(_issue_from_exception(component, failure))
    return default


def _collect_operational_issues(
    records: list[ParsedRecord],
    findings: list[Finding],
    trial_results: list[TrialVerificationResult],
    semantic_stats: SemanticRunStats,
    nonsense_stats: NonsenseRunStats,
    *,
    registry_lookup_requested: bool,
) -> list[OperationalIssue]:
    source_files = {record.record_id: record.source_file for record in records}
    issues = [
        OperationalIssue(
            component="xml_parser",
            error_type=warning.warning_code,
            message=warning.warning_message,
            record_id=record.record_id,
            source_file=record.source_file,
            recoverable=False,
            error_category="invalid_input",
        )
        for record in records
        if record.parse_status == "failed"
        for warning in record.parse_warnings
    ]
    issues.extend(
        OperationalIssue(
            component=finding.detector_type,
            error_type="validation_failed",
            message=finding.validation_reason or "Finding validation failed.",
            record_id=finding.record_id,
            source_file=finding.source_file,
            error_category="validation_failed",
        )
        for finding in findings
        if finding.validation_failed
    )
    issues.extend(
        OperationalIssue(
            component="llm_response_trace_semantic",
            error_type="model_failure",
            message="Semantic response-trace coverage is incomplete for this record.",
            record_id=record_id,
            source_file=source_files.get(record_id, ""),
            error_category=semantic_stats.failure_categories.get(record_id, "invalid_response"),
        )
        for record_id in semantic_stats.failed_record_ids
    )
    issues.extend(
        OperationalIssue(
            component="nonsense_candidate",
            error_type="model_failure",
            message="Nonsense candidate review is incomplete for at least one sentence in this record.",
            record_id=record_id,
            source_file=source_files.get(record_id, ""),
            error_category=nonsense_stats.failure_categories.get(record_id, "invalid_response"),
        )
        for record_id in dict.fromkeys(nonsense_stats.failed_sentence_record_ids)
    )
    issues.extend(
        OperationalIssue(
            component="clinical_trial_registry",
            error_type=result.operational_error_type or "registry_lookup_failed",
            message=result.operational_error or "Registry lookup failed.",
            record_id=result.record_id,
            source_file=result.source_file,
            error_category="registry_lookup_failed",
        )
        for result in trial_results
        # Without --verify-trials only cached registry responses are consulted; a miss means the
        # lookup was not requested, not that a check failed. Local trial checks still ran.
        if result.operational_error and registry_lookup_requested
    )
    return issues


def _duplicate_doi_issues(records: list[ParsedRecord]) -> list[OperationalIssue]:
    """Records sharing a normalized DOI cannot be joined by DOI; they are keyed by abstract ID."""
    counts = Counter(_normalized_doi(record.doi) for record in records if _normalized_doi(record.doi))
    return [
        OperationalIssue(
            component="record_identity",
            error_type="duplicate_doi",
            message="Another retained record has the same DOI; this record is keyed by abstract ID and needs identity review.",
            record_id=record.record_id,
            source_file=record.source_file,
            recoverable=False,
            error_category="duplicate_identifier",
        )
        for record in records
        if counts.get(_normalized_doi(record.doi), 0) > 1
    ]


def _dictionary_rows(llm_rules: list[dict[str, Any]], tortured_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(llm_rules)
    rows.extend(tortured_rules)
    return rows


@dataclass
class _RunState:
    """Everything later stages need; pickled at checkpoint boundaries (records are re-parsed)."""

    completed_stages: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    operational_issues: list[OperationalIssue] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    deterministic_candidate_count: int = 0
    comparable_record_ids: list[str] = field(default_factory=list)
    trial_results: list[TrialVerificationResult] = field(default_factory=list)
    semantic_stats: SemanticRunStats = field(default_factory=SemanticRunStats)
    nonsense_stats: NonsenseRunStats = field(default_factory=NonsenseRunStats)
    entity_inference_count: int = 0
    template_features: list[Any] = field(default_factory=list)
    exact_template_findings: list[Any] = field(default_factory=list)
    entity_template_findings: list[Any] = field(default_factory=list)
    enriched_pair_rows: list[dict[str, Any]] = field(default_factory=list)
    enriched_family_rows: list[dict[str, Any]] = field(default_factory=list)
    enriched_abstract_rows: list[dict[str, Any]] = field(default_factory=list)


STAGES = ("record_checks", "template_features", "template_pairs", "enriched_reports")
OUTPUT_FILES = {
    "content_integrity_json": "content_integrity_results.json",
    "workbook": "Editor_Triage_Workbook.xlsx",
    "run_metrics": "run_metrics.json",
    "run_summary": "run_summary.json",
}
CHECKPOINT_DIRECTORY = ".checkpoint"


def _records_sha256(records: list[ParsedRecord]) -> str:
    digest = sha256()
    for record in records:
        digest.update(json.dumps([record.record_id, record.source_file, record.parse_status, record.title, record.abstract_text]).encode())
    return digest.hexdigest()


def _checkpoint_fingerprint(config: PipelineConfig, input_sha256: str, records: list[ParsedRecord], llm_client: Any | None) -> dict[str, Any]:
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "code_sha256": code_sha256(),
        "input_manifest_sha256": input_sha256,
        "records_sha256": _records_sha256(records),
        "tortured_dictionary_sha256": file_sha256(config.tortured_dictionary_path),
        "dictionary_version": config.dictionary_version,
        "offline": config.offline,
        "validate_llm": config.validate_llm,
        "detect_llm_semantic": config.detect_llm_semantic,
        "detect_nonsense_candidates": config.detect_nonsense_candidates,
        "verify_trials": config.verify_trials,
        # Model responses depend on the deployment and model; credentials are never recorded.
        "gateway": f"{getattr(llm_client, 'base_url', '')}|{getattr(llm_client, 'model_name', '')}" if llm_client is not None else "none",
    }


def _host_environment() -> dict[str, Any]:
    memory_kb = next(
        (int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:")),
        0,
    ) if Path("/proc/meminfo").exists() else 0
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_total_mb": round(memory_kb / 1024),
    }


def _record_checks_stage(
    state: _RunState, config: PipelineConfig, records: list[ParsedRecord], llm_client: Any | None,
    llm_rules: list[Any], tortured_rules: list[Any], tortured_index: Any, metrics: _RunMetrics, log: _RunLog,
) -> None:
    issues = state.operational_issues
    findings = state.findings
    nonsense_detector = (
        NonsenseCandidateDetector(
            llm_client,
            rule_index=tortured_index,
            max_concurrent_batches=config.llm_max_concurrency,
        )
        if config.detect_nonsense_candidates and llm_client is not None
        else None
    )
    deterministic_candidates = []
    tortured_by_record: dict[str, list[Finding]] = {}
    with metrics.stage("deterministic_record_detectors", issues):
        for record in records:
            deterministic_candidates.extend(_run_detector(
                "llm_response_trace",
                lambda: detect_llm_trace_candidates(record, llm_rules),
                issues, [], log, record,
            ))
            tortured_findings = _run_detector(
                "tortured_phrase",
                lambda: detect_tortured_phrases(record, tortured_rules, tortured_index),
                issues, [], log, record,
            )
            tortured_by_record[record.record_id] = tortured_findings
            findings.extend(tortured_findings)
    state.deterministic_candidate_count = len(deterministic_candidates)

    if nonsense_detector:
        with metrics.stage("nonsense_candidates", issues):
            nonsense_findings, state.nonsense_stats = _run_detector(
                "nonsense_candidate",
                lambda: nonsense_detector.detect_records(records, tortured_by_record),
                issues, ([], NonsenseRunStats()), log,
            )
        findings.extend(nonsense_findings)

    semantic_candidates = []
    if config.detect_llm_semantic and llm_client is not None:
        with metrics.stage("llm_response_trace_semantic", issues):
            semantic_candidates, state.semantic_stats = _run_detector(
                "llm_response_trace_semantic",
                lambda: detect_semantic_traces(
                    llm_client, records, max_concurrent_batches=config.llm_max_concurrency,
                ),
                issues, ([], SemanticRunStats()), log,
            )
    llm_candidates = fuse_llm_trace_candidates([*deterministic_candidates, *semantic_candidates])
    llm_validator = LLMTraceValidator(llm_client) if config.validate_llm and llm_client is not None else None
    with metrics.stage("llm_response_trace_validation", issues):
        apply_llm_trace_validation(llm_candidates, llm_validator)
    findings.extend(candidate_to_finding(candidate) for candidate in llm_candidates)

    def _is_comparable(record: ParsedRecord) -> bool:
        return record.parse_status != "failed" and bool(record.title.strip() or record.abstract_text.strip())

    comparable_records = [record for record in records if _is_comparable(record)]
    state.comparable_record_ids = [record.record_id for record in comparable_records]
    issues.extend(
        OperationalIssue(
            component=component,
            error_type="record_excluded_parse_failed" if record.parse_status == "failed" else "record_excluded_no_text",
            message=(
                "This check did not run: XML parsing failed for this record."
                if record.parse_status == "failed"
                else "This check did not run: record has no usable title or abstract text."
            ),
            record_id=record.record_id,
            source_file=record.source_file,
            recoverable=False,
            error_category="invalid_input",
        )
        for record in records
        if not _is_comparable(record)
        for component in COMPARABILITY_GATED_COMPONENTS
    )
    with metrics.stage("numerical_contradiction", issues):
        numerical_results = _run_corpus_detector(
            "numerical_contradiction", detect_numerical_contradictions, comparable_records, issues, [], log,
        )
    design_validator = (
        LLMDesignContradictionValidator(llm_client)
        if config.validate_llm and llm_client is not None
        else None
    )
    with metrics.stage("design_contradiction", issues):
        design_results = _run_corpus_detector(
            "design_contradiction",
            lambda subset: detect_design_contradictions(subset, validator=design_validator),
            comparable_records, issues, [], log,
        )
    registry_client = ClinicalTrialsGovClient(
        cache_dir=config.output_dir / ".trial_registry_cache",
        offline_cache_only=not config.verify_trials,
    )
    with metrics.stage("unverifiable_clinical_trial", issues):
        state.trial_results = _run_corpus_detector(
            "unverifiable_clinical_trial",
            lambda subset: detect_unverifiable_trials(subset, registry_clients={CLINICAL_TRIALS_GOV: registry_client}),
            comparable_records, issues, [], log,
        )
    findings.extend(
        _integrated_finding(result)
        for result in [*numerical_results, *design_results, *state.trial_results]
        if result.check_triggered
    )

    ordered_findings = sorted(findings, key=_finding_sort_key)
    for index, finding in enumerate(ordered_findings, start=1):
        if not finding.finding_id:
            finding.finding_id = f"FND-{index:05d}"

    if config.validate_llm and ordered_findings:
        with metrics.stage("context_validation", issues):
            if llm_client is None:
                for finding in ordered_findings:
                    if (
                        finding.detector_type in {"tortured_phrase", "design_contradiction"}
                        or (
                            finding.detector_type == "llm_response_trace"
                            and finding.normalized_validation_status == "pending"
                        )
                    ):
                        finding.validation_status = "validation_failed"
                        finding.validation_reason = "Validation model was unavailable."
                        finding.review_status = "validation_failed"
            else:
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
                    if result.confidence is not None:
                        finding.confidence = result.confidence


def _template_features_stage(
    state: _RunState, config: PipelineConfig, records: list[ParsedRecord], extractor: EntityExtractor | None, log: _RunLog,
) -> None:
    def build(record: ParsedRecord) -> tuple[Any, list[OperationalIssue]]:
        issues: list[OperationalIssue] = []
        features = _run_detector(
            "shared_preprocessing", lambda: build_template_features(record, extractor=extractor), issues, None, log, record,
        )
        if features is None:
            # Model spans failed: keep deterministic features so the record stays comparable.
            features = _run_detector(
                "shared_preprocessing", lambda: build_template_features(record, use_model=False), issues, None, log, record,
            )
        return features, issues

    if extractor is not None and config.llm_max_concurrency > 1:
        # Threads wait on the gateway; the client bounds how many requests are actually in flight.
        with ThreadPoolExecutor(max_workers=config.llm_max_concurrency) as pool:
            results = list(pool.map(build, records))
    else:
        results = [build(record) for record in records]
    for features, issues in results:
        state.operational_issues.extend(issues)
        if features is not None:
            state.template_features.append(features)
    state.entity_inference_count += extractor.inference_count if extractor else 0


def run_pipeline(config: PipelineConfig, *, llm_client: Any | None = None) -> PipelineResult:
    """Run with a fresh client (or a caller-supplied client owned exclusively by this run).

    Resumes from a compatible checkpoint in the output directory (see content_integrity.checkpoint)
    and rejects an incompatible one unless `config.discard_checkpoint` is set. Reports are staged,
    reconciled from disk, and only then renamed over previous output; the checkpoint is removed
    after a successful promotion.
    """
    if config.offline and (llm_client is not None or config.validate_llm or config.detect_llm_semantic
                           or config.detect_nonsense_candidates or config.verify_trials):
        raise ValueError("offline cannot be combined with a client or network-enabled checks")
    run_id = _new_run_id()
    log = _RunLog(logger, {"run_id": run_id})
    metrics = _RunMetrics(log)
    started_at = datetime.now(timezone.utc)
    with metrics.stage("parse"):
        if not config.input_dir.is_dir():
            raise ValueError(f"Input directory does not exist or is not a directory: {config.input_dir}")
        xml_files = discover_xml_files(config.input_dir)
        if not xml_files:
            raise ValueError(f"Input directory contains no XML files: {config.input_dir}")
        parsed_records = [record for path in xml_files for record in parse_xml_records(path)]
        input_record_count = len(parsed_records)
        records, record_id_warnings = dedupe_records(parsed_records)
        input_sha256 = _input_manifest_checksum(xml_files, config.input_dir)
    log.info("parse: %d files -> %d input records -> %d retained records", len(xml_files), input_record_count, len(records))
    llm_rules = built_in_llm_rules()
    tortured_rules = load_tortured_rules(config.tortured_dictionary_path, config.dictionary_version)
    tortured_index = build_tortured_rule_index(tortured_rules)
    client_issues: list[OperationalIssue] = []
    # Normal runs use GPT-OSS for entity masking; offline runs explicitly use rules only.
    # Configuration failures remain visible while deterministic stages continue.
    if llm_client is None and not config.offline:
        llm_client = _run_detector(
            "gpt_oss_model",
            lambda: build_gpt_oss_client(
                cache_dir=config.output_dir / ".gpt_oss_cache",
                max_concurrent_requests=config.llm_max_concurrency,
            ),
            client_issues, None, log,
        )
    extractor = EntityExtractor(llm_client) if llm_client is not None else None

    checkpoint = CheckpointStore(config.output_dir / CHECKPOINT_DIRECTORY)
    fingerprint = _checkpoint_fingerprint(config, input_sha256, records, llm_client)
    if config.discard_checkpoint and checkpoint.exists():
        log.info("checkpoint: discarded on request")
        checkpoint.discard()
    loaded = checkpoint.load(fingerprint)
    state: _RunState = loaded[0] if loaded else _RunState()
    if loaded:
        log.info("checkpoint: resuming after %s from run(s) %s", ", ".join(state.completed_stages), ", ".join(state.run_ids))
    resumed_from = list(state.run_ids)
    state.run_ids.append(run_id)

    def complete_stage(name: str) -> None:
        state.completed_stages.append(name)
        with metrics.stage(f"checkpoint_{name}"):
            index = checkpoint.save(state, fingerprint, completed_stages=state.completed_stages, run_ids=state.run_ids)
        metrics.stages[-1]["checkpoint_bytes"] = index["state_bytes"]

    if "record_checks" in state.completed_stages:
        metrics.restored("record_checks")
    else:
        _record_checks_stage(state, config, records, llm_client, llm_rules, tortured_rules, tortured_index, metrics, log)
        complete_stage("record_checks")

    if "template_features" in state.completed_stages:
        metrics.restored("template_features")
    else:
        with metrics.stage("template_features", state.operational_issues):
            _template_features_stage(state, config, records, extractor, log)
        complete_stage("template_features")

    feature_by_id = {features.record_id: features for features in state.template_features}
    template_records = [record for record in records if record.record_id in feature_by_id]

    def subset_features(subset: list[ParsedRecord]) -> list[Any]:
        return [feature_by_id[record.record_id] for record in subset]

    if "template_pairs" in state.completed_stages:
        metrics.restored("template_pairs")
    else:
        with metrics.stage("exact_text_reuse", state.operational_issues):
            state.exact_template_findings = _run_corpus_detector(
                "exact_text_reuse",
                lambda subset: detect_exact_text_reuse(subset, features=subset_features(subset)),
                template_records, state.operational_issues, [], log,
            )
        with metrics.stage("entity_normalized_template", state.operational_issues):
            state.entity_template_findings = _run_corpus_detector(
                "entity_normalized_template",
                lambda subset: detect_entity_normalized_templates(subset, features=subset_features(subset)),
                template_records, state.operational_issues, [], log,
            )
        complete_stage("template_pairs")

    if "enriched_reports" in state.completed_stages:
        metrics.restored("enriched_reports")
    else:
        detector_pairs = [*state.exact_template_findings, *state.entity_template_findings]

        def enriched(subset: list[ParsedRecord]) -> tuple[list, list, list]:
            ids = {record.record_id for record in subset}
            return build_enriched_reports(
                subset,
                [item for item in detector_pairs if item.record_id in ids and item.matched_record_id in ids],
                subset_features(subset),
            )

        with metrics.stage("enriched_reports", state.operational_issues):
            state.enriched_pair_rows, state.enriched_family_rows, abstract_rows = _run_corpus_detector(
                "enriched_reports", enriched, template_records, state.operational_issues, ([], [], []), log,
            )
            # Records without template features still need an (empty) template row downstream.
            covered = {str(row["record_id"]) for row in abstract_rows}
            state.enriched_abstract_rows = [
                *abstract_rows,
                *(
                    {"report_version": REPORT_VERSION, "record_id": record.record_id, "source_file": record.source_file,
                     "title": record.title, "candidate_pair_count": 0, "finding_pair_count": 0,
                     "highest_review_priority": "None", "strongest_matched_record_id": "", "family_id": "",
                     "family_size": 0, "family_edge_score": 0.0, "family_member_status": "",
                     "reporting_note": "Template detection did not run for this record."}
                    for record in records if record.record_id not in covered
                ),
            ]
        log.info("enriched_reports: %d candidate pairs", len(state.enriched_pair_rows))
        complete_stage("enriched_reports")

    with metrics.stage("aggregation"):
        findings = state.findings
        pair_findings = merge_pair_findings(state.exact_template_findings, state.entity_template_findings)
        template_rows = cluster_template_findings(pair_findings, records)
        enriched_pair_rows = state.enriched_pair_rows
        reviewer_pair_rows = directional_finding_rows(enriched_pair_rows)
        field_inventory_rows, root_summary_rows = _inventory_rows(records)
        authorship_checks_by_key = _load_authorship_checks(config.authorship_json_path)
        abstract_summary_rows = _aggregate_findings(
            records,
            findings,
            [],
            [],
            llm_rules,
            enriched_pair_rows=enriched_pair_rows,
            enriched_abstract_rows=state.enriched_abstract_rows,
            authorship_checks_by_key=authorship_checks_by_key,
        )
        operational_issues = [
            *client_issues,
            *state.operational_issues,
            *_duplicate_doi_issues(records),
            *_collect_operational_issues(
                records, findings, state.trial_results, state.semantic_stats, state.nonsense_stats,
                registry_lookup_requested=config.verify_trials,
            ),
        ]
        reporting_findings = [finding for finding in findings if finding.detector_type != "nonsense_candidate"]
        reporting_operational_issues = [
            issue for issue in operational_issues if issue.component != "nonsense_candidate"
        ]
        findings_rows = _findings_rows(findings)
        titles_by_record = {record.record_id: record.title for record in records}
        for row in findings_rows:
            row["title"] = titles_by_record.get(row["record_id"], "")
        integrity_finding_rows = sorted(
            (row for row in findings_rows if row.get("detector_type") != "nonsense_candidate"),
            key=_finding_row_sort_key,
        )
        family_rows = _family_rows(template_rows)
        parse_warning_rows = _parse_warning_rows(records)
        source_files = {record.record_id: record.source_file for record in records}
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
                "source_file": source_files.get(record_id, ""),
                "record_id": record_id,
                "warning_code": "llm_semantic_batch_failed",
                "warning_message": "Semantic response-trace coverage is incomplete for this record.",
                "field_name": "abstract_text",
                "severity": "warning",
                "evidence_snippet": "",
                "schema_type": "",
            }
            for record_id in state.semantic_stats.failed_record_ids
        )
        dictionary_rows = _dictionary_rows([rule.to_dict() for rule in llm_rules], [rule.to_dict() for rule in tortured_rules])
    semantic_stats = state.semantic_stats
    comparable_record_count = len(state.comparable_record_ids)
    now = datetime.now(timezone.utc)
    commit_sha, worktree_dirty = _git_revision()
    catalogue_version, catalogue_checksum = catalogue_metadata()
    llm_findings = [finding for finding in findings if finding.detector_type == "llm_response_trace"]
    llm_call_stats = getattr(llm_client, "call_stats", None)
    skipped = [
        {"record_id": warning["record_id"], "source_file": warning["source_file"], "reason": warning["reason"]}
        for warning in record_id_warnings
        if warning["reason"] == "ingestion_duplicate"
    ]
    run_metadata_rows: list[tuple[str, Any]] = [
        ("run_id", run_id),
        ("resumed_from_run_ids", " | ".join(resumed_from)),
        ("run_date_utc", now.isoformat()),
        ("code_commit_sha", commit_sha),
        ("code_worktree_dirty", worktree_dirty),
        ("input_folder", str(config.input_dir)),
        ("input_manifest_sha256", input_sha256),
        ("output_folder", str(config.output_dir)),
        ("total_files", len(xml_files)),
        ("total_input_records", input_record_count),
        ("total_records", len(records)),
        ("ingestion_duplicate_count", sum(warning["reason"] == "ingestion_duplicate" for warning in record_id_warnings)),
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
        ("llm_semantic_model_id", semantic_stats.model_id),
        ("llm_semantic_prompt_version", semantic_stats.prompt_version),
        ("llm_semantic_batch_count", semantic_stats.batch_count),
        ("llm_semantic_batch_failure_count", semantic_stats.batch_failure_count),
        ("llm_semantic_request_count", semantic_stats.request_count),
        ("llm_semantic_retry_count", semantic_stats.retry_count),
        ("llm_semantic_failed_record_count", len(semantic_stats.failed_record_ids)),
        ("llm_semantic_max_concurrent_batches", semantic_stats.max_concurrent_batches),
        ("llm_deterministic_finding_count", state.deterministic_candidate_count),
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
        ("tortured_proximity_rule_count", sum(1 for rule in tortured_rules if rule.proximity)),
        (
            "tortured_confidence_basis",
            "llm_calibrated_probability_when_validated_else_heuristic_rule_strength"
            if config.validate_llm
            else "heuristic_rule_strength_not_calibrated_probability",
        ),
        ("dictionary_version", config.dictionary_version),
        ("tortured_dictionary_version", tortured_rules[0].dictionary_version if tortured_rules else ""),
        ("tortured_dictionary_path", str(config.tortured_dictionary_path)),
        ("pipeline_config", json.dumps({
            field.name: str(getattr(config, field.name)) if isinstance(getattr(config, field.name), Path) else getattr(config, field.name)
            for field in fields(config)
            if field.name != "detect_nonsense_candidates"
        }, sort_keys=True)),
        ("clinical_trial_registry_lookup", "enabled" if config.verify_trials else "local_checks_only"),
        ("enriched_report_version", REPORT_VERSION),
        ("enriched_pair_count", len(enriched_pair_rows)),
        ("template_candidate_pair_count", len(enriched_pair_rows)),
        ("template_final_pair_count", len(reviewer_pair_rows) // 2),
        ("entity_model_inference_count", state.entity_inference_count),
        ("entity_model_id", getattr(llm_client, "model_name", "")),
        ("entity_prompt_version", ENTITY_PROMPT_VERSION),
        ("template_finding_pair_count", len(reviewer_pair_rows) // 2),
        ("template_finding_directional_row_count", len(reviewer_pair_rows)),
        ("template_insufficient_evidence_count", sum(row["review_priority"] == "None" for row in enriched_pair_rows)),
        ("enriched_family_count", len(state.enriched_family_rows)),
        ("template_feature_version", TEMPLATE_FEATURE_VERSION),
        ("template_pair_classifier_version", TEMPLATE_PAIR_CLASSIFIER_VERSION),
        ("template_signal_validation_version", TEMPLATE_SIGNAL_VALIDATION_VERSION),
        ("template_family_version", TEMPLATE_FAMILY_VERSION),
        ("editorial_scoring_version", EDITORIAL_SCORING_VERSION),
        ("entity_vocabulary_version", ENTITY_VOCABULARY_VERSION),
        ("threshold_config_module", "content_integrity.thresholds"),
        ("design_contradiction_rule_table_version", DESIGN_CONTRADICTION_RULE_TABLE_VERSION),
        ("design_contradiction_prompt_version", DESIGN_CONTRADICTION_PROMPT_VERSION),
        ("comparable_record_count", comparable_record_count),
        ("records_excluded_from_numerical_design_trial_checks", len(records) - comparable_record_count),
        ("llm_gateway_request_count", llm_call_stats.request_count if llm_call_stats else 0),
        ("llm_gateway_success_count", llm_call_stats.success_count if llm_call_stats else 0),
        ("llm_gateway_failure_count", llm_call_stats.failure_count if llm_call_stats else 0),
        ("llm_gateway_retry_count", llm_call_stats.retry_count if llm_call_stats else 0),
        ("llm_gateway_cache_hit_count", llm_call_stats.cache_hits if llm_call_stats else 0),
        ("llm_gateway_total_latency_seconds", round(llm_call_stats.total_latency_seconds, 3) if llm_call_stats else 0.0),
        (
            "llm_gateway_avg_latency_seconds",
            round(llm_call_stats.total_latency_seconds / llm_call_stats.request_count, 3)
            if llm_call_stats and llm_call_stats.request_count
            else 0.0,
        ),
        ("llm_gateway_max_latency_seconds", round(llm_call_stats.max_latency_seconds, 3) if llm_call_stats else 0.0),
        ("operational_issue_count", len(reporting_operational_issues)),
        ("limitations", "Rule-based screening flags explicit LLM response traces, known tortured phrases, and repeated abstract skeletons; optional GPT-OSS stages only annotate candidates, and the pipeline does not detect AI-generated authorship."),
        ("excluded_scope", "AI-generated text detection"),
    ]

    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob(".staging-*"):
        # Staging from an interrupted run was never promoted; one writer per output directory.
        shutil.rmtree(stale, ignore_errors=True)
    staging = output_dir / f".staging-{run_id}"
    staging.mkdir()
    staged = {name: staging / filename for name, filename in OUTPUT_FILES.items()}
    try:
        with metrics.stage("write_outputs"):
            canonical_report = build_content_integrity_frontend_json(
                records=records,
                findings=reporting_findings,
                enriched_pair_rows=enriched_pair_rows,
                enriched_abstract_rows=state.enriched_abstract_rows,
                abstract_summary_rows=abstract_summary_rows,
                operational_issues=reporting_operational_issues,
                generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                git_revision=commit_sha,
                run_metadata=dict(run_metadata_rows),
                template_family_rows=state.enriched_family_rows,
            )
            reviewable_pair_count = len(canonical_report["template_pairs"])
            atomic_write_json(staged["content_integrity_json"], build_integrated_content_integrity_json(canonical_report))
            del canonical_report
            write_workbook(
                staged["workbook"],
                abstract_summary_rows=abstract_summary_rows,
                findings_rows=integrity_finding_rows,
                pair_rows=reviewer_pair_rows,
                operational_issue_rows=[issue.to_dict() for issue in reporting_operational_issues],
                run_metadata_rows=run_metadata_rows,
                authorship_checks_by_key=authorship_checks_by_key,
            )
        with metrics.stage("reconciliation"):
            reconciliation = reconcile_outputs(
                record_ids=[record.record_id for record in records],
                input_record_count=input_record_count,
                skipped=skipped,
                reportable_findings=reporting_findings,
                reviewable_pair_count=reviewable_pair_count,
                json_path=staged["content_integrity_json"],
                workbook_path=staged["workbook"],
            )
        wall_seconds = time.perf_counter() - metrics.started
        cpu_seconds = _cpu_seconds() - metrics.cpu_started
        run_metrics = {
            "run_id": run_id,
            "resumed_from_run_ids": resumed_from,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "host": _host_environment(),
            "code_commit_sha": commit_sha,
            "code_worktree_dirty": worktree_dirty,
            "model_mode": "offline" if config.offline else ("gateway" if llm_client is not None else "gateway_unavailable"),
            "configuration": {
                "offline": config.offline,
                "validate_llm": config.validate_llm,
                "detect_llm_semantic": config.detect_llm_semantic,
                "detect_nonsense_candidates": config.detect_nonsense_candidates,
                "verify_trials": config.verify_trials,
                "llm_max_concurrency": config.llm_max_concurrency,
                "gateway_limits": {
                    name: getattr(llm_client, name)
                    for name in ("max_concurrent_requests", "connect_timeout_seconds", "timeout_seconds",
                                 "request_deadline_seconds", "max_attempts", "backoff_seconds", "max_backoff_seconds",
                                 "circuit_failure_threshold", "circuit_cooldown_seconds")
                    if hasattr(llm_client, name)
                },
            },
            "totals": {
                "input_records": input_record_count,
                "retained_records": len(records),
                "wall_seconds": round(wall_seconds, 3),
                "cpu_seconds": round(cpu_seconds, 3),
                "average_cpu_cores_used": round(cpu_seconds / wall_seconds, 2) if wall_seconds else 0.0,
                "records_per_minute": round(len(records) / wall_seconds * 60, 1) if wall_seconds else 0.0,
                "peak_rss_mb": _peak_rss_mb(),
                "template_candidate_pairs": len(enriched_pair_rows),
                "template_prioritized_pairs": len(reviewer_pair_rows) // 2,
                "template_reviewable_pairs": reviewable_pair_count,
                "template_families": len(state.enriched_family_rows),
                "operational_issues": len(reporting_operational_issues),
            },
            "stages": metrics.stages,
            # Covers this invocation only; a resumed run reuses cached responses without calls.
            "gateway": llm_call_stats.summary() if llm_call_stats else None,
            "entity_model_inference_count": state.entity_inference_count,
            "estimated_cost": "not available: no gateway pricing configured",
        }
        atomic_write_json(staged["run_metrics"], run_metrics)
        run_summary = {
            "run_id": run_id,
            "status": "succeeded" if reconciliation["reconciled"] else "reconciliation_failed",
            "finished_at": run_metrics["finished_at"],
            **reconciliation,
            "template": {
                "candidate_pairs": len(enriched_pair_rows),
                "prioritized_pairs": len(reviewer_pair_rows) // 2,
                "reviewable_pairs": reviewable_pair_count,
                "families": len(state.enriched_family_rows),
                "family_members": sum(int(row.get("family_size") or 0) for row in state.enriched_family_rows),
            },
            "outputs": {
                name: {"file": OUTPUT_FILES[name], "sha256": file_sha256(staged[name]), "bytes": staged[name].stat().st_size}
                for name in ("content_integrity_json", "workbook", "run_metrics")
            },
        }
        atomic_write_json(staged["run_summary"], run_summary)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if not reconciliation["reconciled"]:
        failed = [check["name"] for check in reconciliation["checks"] if not check["passed"]]
        log.error("reconciliation failed (%s); previous outputs were kept and staging retained at %s", ", ".join(failed), staging)
        raise ReconciliationError(f"Reports did not reconcile ({', '.join(failed)}); inspect {staging}")
    # run_summary.json is renamed last and records the other files' hashes, so an interruption
    # between renames is detectable as a summary/file hash mismatch.
    output_paths = {name: output_dir / filename for name, filename in OUTPUT_FILES.items()}
    for name in OUTPUT_FILES:
        os.replace(staged[name], output_paths[name])
    shutil.rmtree(staging, ignore_errors=True)
    checkpoint.discard()
    records_summary = reconciliation["records"]
    log.info(
        "run complete: %d input records (%d completed, %d with findings, %d failed, %d skipped) in %.1fs",
        records_summary["total_input"], records_summary["completed"], records_summary["completed_with_findings"],
        records_summary["failed"], records_summary["skipped"], wall_seconds,
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
        operational_issues=operational_issues,
        output_paths=output_paths,
        run_id=run_id,
        run_summary=run_summary,
        run_metrics=run_metrics,
    )


def run_default_pipeline(
    input_dir: str | Path = "metadata_files",
    tortured_dictionary_path: str | Path = "🤷_tortured.csv",
    output_dir: str | Path = "outputs",
    authorship_json_path: str | Path | None = None,
    validate_llm: bool = False,
    detect_llm_semantic: bool = False,
    detect_nonsense_candidates: bool = False,
    verify_trials: bool = False,
    llm_max_concurrency: int = DEFAULT_MAX_CONCURRENT_BATCHES,
    offline: bool = False,
    discard_checkpoint: bool = False,
) -> PipelineResult:
    config = PipelineConfig(
        input_dir=Path(input_dir),
        output_dir=Path(output_dir),
        tortured_dictionary_path=Path(tortured_dictionary_path),
        authorship_json_path=Path(authorship_json_path) if authorship_json_path is not None else None,
        validate_llm=validate_llm,
        detect_llm_semantic=detect_llm_semantic,
        detect_nonsense_candidates=detect_nonsense_candidates,
        verify_trials=verify_trials,
        llm_max_concurrency=llm_max_concurrency,
        offline=offline,
        discard_checkpoint=discard_checkpoint,
    )
    return run_pipeline(config)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the ASCO content integrity screening POC.")
    parser.add_argument("--input-dir", default="metadata_files", help="Folder containing Wiley XML files.")
    parser.add_argument("--offline", action="store_true", help="Run deterministic checks without network or gateway configuration.")
    parser.add_argument("--tortured-dictionary", default="🤷_tortured.csv", help="Tortured phrase dictionary CSV.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for generated reports.")
    parser.add_argument(
        "--authorship-json",
        default=None,
        help="Frontend authorship JSON used for workbook projection.",
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
        "--verify-trials",
        action="store_true",
        help="Verify valid NCT identifiers against ClinicalTrials.gov; local trial-reference checks always run.",
    )
    parser.add_argument(
        "--llm-max-concurrency",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_BATCHES,
        help="Maximum GPT-OSS requests in flight at once, shared by every model stage.",
    )
    parser.add_argument(
        "--discard-checkpoint",
        action="store_true",
        help="Delete an existing checkpoint in the output directory and start from the beginning.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-stage progress logging.",
    )
    args = parser.parse_args(argv)
    if args.llm_max_concurrency < 1:
        parser.error("--llm-max-concurrency must be at least 1")

    # Only the CLI configures logging; importing the pipeline as a library must not.
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        result = run_default_pipeline(
            input_dir=args.input_dir,
            tortured_dictionary_path=args.tortured_dictionary,
            output_dir=args.output_dir,
            authorship_json_path=args.authorship_json,
            validate_llm=args.validate_llm,
            detect_llm_semantic=args.detect_llm_semantic,
            detect_nonsense_candidates=args.detect_nonsense_candidates,
            verify_trials=args.verify_trials,
            llm_max_concurrency=args.llm_max_concurrency,
            offline=args.offline,
            discard_checkpoint=args.discard_checkpoint,
        )
    except IncompatibleCheckpointError as exc:
        print(f"error: {exc} (use --discard-checkpoint)", file=sys.stderr)
        return 2
    except ReconciliationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(json.dumps({key: str(value) for key, value in result.output_paths.items()}, ensure_ascii=False, indent=2))
    return 0
