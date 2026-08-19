from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from hashlib import sha256
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
    model_inference_count,
    reset_model_inference_count,
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
    write_json,
    write_workbook,
)
from .validators import ContextValidator, LLMTraceValidator, apply_llm_trace_validation, build_gpt_oss_client
from .validators.llm_trace_validator import PROMPT_VERSION as LLM_VALIDATION_PROMPT_VERSION
from .utils import dedupe_records, normalize_whitespace, to_pipe_string
from .xml_parser import discover_xml_files, parse_xml_records


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
    authorship_json_path: Path = Path("outputs/frontend_run/final_json.json")
    dictionary_version: str = "wiley_tortured_seed_v1"
    validate_llm: bool = False
    detect_llm_semantic: bool = False
    detect_nonsense_candidates: bool = False
    verify_trials: bool = False
    llm_max_concurrency: int = DEFAULT_MAX_CONCURRENT_BATCHES


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
                    "pair_classification": pair.pair_classification,
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


def _run_detector(
    component: str,
    operation: Any,
    operational_issues: list[OperationalIssue],
    default: Any,
    record: ParsedRecord | None = None,
) -> Any:
    try:
        return operation()
    except Exception as exc:  # Detector/model boundaries must be visible in completed reports.
        operational_issues.append(OperationalIssue(
            component=component,
            error_type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
            record_id=record.record_id if record else "",
            source_file=record.source_file if record else "",
        ))
        return default


def _collect_operational_issues(
    records: list[ParsedRecord],
    findings: list[Finding],
    trial_results: list[TrialVerificationResult],
    semantic_stats: SemanticRunStats,
    nonsense_stats: NonsenseRunStats,
) -> list[OperationalIssue]:
    issues = [
        OperationalIssue(
            component="xml_parser",
            error_type=warning.warning_code,
            message=warning.warning_message,
            record_id=record.record_id,
            source_file=record.source_file,
            recoverable=False,
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
            source_file=next(
                (record.source_file for record in records if record.record_id == record_id), "",
            ),
        )
        for record_id in semantic_stats.failed_record_ids
    )
    issues.extend(
        OperationalIssue(
            component="nonsense_candidate",
            error_type="model_failure",
            message="Nonsense candidate review is incomplete for at least one sentence in this record.",
            record_id=record_id,
            source_file=next(
                (record.source_file for record in records if record.record_id == record_id), "",
            ),
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
        )
        for result in trial_results
        if result.operational_error
    )
    return issues


def _dictionary_rows(llm_rules: list[dict[str, Any]], tortured_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(llm_rules)
    rows.extend(tortured_rules)
    return rows


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    reset_model_inference_count()
    xml_files = discover_xml_files(config.input_dir)
    records = [record for path in xml_files for record in parse_xml_records(path)]
    records, record_id_warnings = dedupe_records(records)
    operational_issues: list[OperationalIssue] = []
    llm_rules = built_in_llm_rules()
    tortured_rules = load_tortured_rules(config.tortured_dictionary_path, config.dictionary_version)
    tortured_index = build_tortured_rule_index(tortured_rules)
    llm_client = None
    if config.detect_nonsense_candidates or config.detect_llm_semantic or config.validate_llm:
        llm_client = _run_detector(
            "gpt_oss_model",
            build_gpt_oss_client,
            operational_issues,
            None,
        )
    nonsense_detector = (
        NonsenseCandidateDetector(
            llm_client,
            rule_index=tortured_index,
            max_concurrent_batches=config.llm_max_concurrency,
        )
        if config.detect_nonsense_candidates and llm_client is not None
        else None
    )

    findings: list[Finding] = []
    deterministic_candidates = []
    tortured_by_record: dict[str, list[Finding]] = {}
    for record in records:
        deterministic_candidates.extend(_run_detector(
            "llm_response_trace",
            lambda: detect_llm_trace_candidates(record, llm_rules),
            operational_issues,
            [],
            record,
        ))
        tortured_findings = _run_detector(
            "tortured_phrase",
            lambda: detect_tortured_phrases(record, tortured_rules, tortured_index),
            operational_issues,
            [],
            record,
        )
        tortured_by_record[record.record_id] = tortured_findings
        findings.extend(tortured_findings)

    nonsense_stats = NonsenseRunStats()
    if nonsense_detector:
        nonsense_findings, nonsense_stats = _run_detector(
            "nonsense_candidate",
            lambda: nonsense_detector.detect_records(records, tortured_by_record),
            operational_issues,
            ([], NonsenseRunStats()),
        )
        findings.extend(nonsense_findings)

    semantic_candidates = []
    semantic_stats = SemanticRunStats()
    if config.detect_llm_semantic and llm_client is not None:
        semantic_candidates, semantic_stats = _run_detector(
            "llm_response_trace_semantic",
            lambda: detect_semantic_traces(
                llm_client, records, max_concurrent_batches=config.llm_max_concurrency,
            ),
            operational_issues,
            ([], SemanticRunStats()),
        )
    llm_candidates = fuse_llm_trace_candidates([*deterministic_candidates, *semantic_candidates])
    llm_validator = LLMTraceValidator(llm_client) if config.validate_llm and llm_client is not None else None
    apply_llm_trace_validation(llm_candidates, llm_validator)
    findings.extend(candidate_to_finding(candidate) for candidate in llm_candidates)

    def _is_comparable(record: ParsedRecord) -> bool:
        return record.parse_status != "failed" and bool(record.title.strip() or record.abstract_text.strip())

    comparable_records = [record for record in records if _is_comparable(record)]
    operational_issues.extend(
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
        )
        for record in records
        if not _is_comparable(record)
        for component in COMPARABILITY_GATED_COMPONENTS
    )
    numerical_results = _run_detector(
        "numerical_contradiction",
        lambda: detect_numerical_contradictions(comparable_records),
        operational_issues,
        [],
    )
    design_validator = (
        LLMDesignContradictionValidator(llm_client)
        if config.validate_llm and llm_client is not None
        else None
    )
    design_results = _run_detector(
        "design_contradiction",
        lambda: detect_design_contradictions(comparable_records, validator=design_validator),
        operational_issues,
        [],
    )
    trial_results = _run_detector(
        "unverifiable_clinical_trial",
        lambda: detect_unverifiable_trials(
            comparable_records,
            registry_clients={
                CLINICAL_TRIALS_GOV: ClinicalTrialsGovClient(
                    cache_dir=config.output_dir / ".trial_registry_cache",
                    offline_cache_only=not config.verify_trials,
                )
            },
        ),
        operational_issues,
        [],
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

    template_features = _run_detector(
        "shared_preprocessing",
        lambda: [build_template_features(record) for record in records],
        operational_issues,
        None,
    )
    if template_features is None:
        template_features = [build_template_features(record, use_model=False) for record in records]
    exact_template_findings = _run_detector(
        "exact_text_reuse",
        lambda: detect_exact_text_reuse(records, features=template_features),
        operational_issues,
        [],
    )
    entity_template_findings = _run_detector(
        "entity_normalized_template",
        lambda: detect_entity_normalized_templates(records, features=template_features),
        operational_issues,
        [],
    )
    pair_findings = merge_pair_findings(exact_template_findings, entity_template_findings)
    template_rows = cluster_template_findings(pair_findings, records)
    enriched_pair_rows, enriched_family_rows, enriched_abstract_rows = build_enriched_reports(
        records, [*exact_template_findings, *entity_template_findings], template_features
    )
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
        enriched_abstract_rows=enriched_abstract_rows,
        authorship_checks_by_key=authorship_checks_by_key,
    )
    operational_issues.extend(
        _collect_operational_issues(records, findings, trial_results, semantic_stats, nonsense_stats)
    )
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
    commit_sha, worktree_dirty = _git_revision()
    catalogue_version, catalogue_checksum = catalogue_metadata()
    llm_findings = [finding for finding in findings if finding.detector_type == "llm_response_trace"]
    llm_call_stats = getattr(llm_client, "call_stats", None)
    run_metadata_rows: list[tuple[str, Any]] = [
        ("run_date_utc", now.isoformat()),
        ("code_commit_sha", commit_sha),
        ("code_worktree_dirty", worktree_dirty),
        ("input_folder", str(config.input_dir)),
        ("input_manifest_sha256", _input_manifest_checksum(xml_files, config.input_dir)),
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
        ("llm_semantic_model_id", semantic_stats.model_id),
        ("llm_semantic_prompt_version", semantic_stats.prompt_version),
        ("llm_semantic_batch_count", semantic_stats.batch_count),
        ("llm_semantic_batch_failure_count", semantic_stats.batch_failure_count),
        ("llm_semantic_request_count", semantic_stats.request_count),
        ("llm_semantic_retry_count", semantic_stats.retry_count),
        ("llm_semantic_failed_record_count", len(semantic_stats.failed_record_ids)),
        ("llm_semantic_max_concurrent_batches", semantic_stats.max_concurrent_batches),
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
        ("tortured_proximity_rule_count", sum(1 for rule in tortured_rules if rule.proximity)),
        ("tortured_confidence_basis", "heuristic_rule_strength_not_calibrated_probability"),
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
        ("entity_model_inference_count", model_inference_count()),
        ("template_finding_pair_count", len(reviewer_pair_rows) // 2),
        ("template_finding_directional_row_count", len(reviewer_pair_rows)),
        ("template_insufficient_evidence_count", sum(row["review_priority"] == "None" for row in enriched_pair_rows)),
        ("enriched_family_count", len(enriched_family_rows)),
        ("template_feature_version", TEMPLATE_FEATURE_VERSION),
        ("template_pair_classifier_version", TEMPLATE_PAIR_CLASSIFIER_VERSION),
        ("template_signal_validation_version", TEMPLATE_SIGNAL_VALIDATION_VERSION),
        ("template_family_version", TEMPLATE_FAMILY_VERSION),
        ("editorial_scoring_version", EDITORIAL_SCORING_VERSION),
        ("entity_vocabulary_version", ENTITY_VOCABULARY_VERSION),
        ("threshold_config_module", "content_integrity.thresholds"),
        ("design_contradiction_rule_table_version", DESIGN_CONTRADICTION_RULE_TABLE_VERSION),
        ("design_contradiction_prompt_version", DESIGN_CONTRADICTION_PROMPT_VERSION),
        ("comparable_record_count", len(comparable_records)),
        ("records_excluded_from_numerical_design_trial_checks", len(records) - len(comparable_records)),
        ("llm_gateway_request_count", llm_call_stats.request_count if llm_call_stats else 0),
        ("llm_gateway_success_count", llm_call_stats.success_count if llm_call_stats else 0),
        ("llm_gateway_failure_count", llm_call_stats.failure_count if llm_call_stats else 0),
        ("llm_gateway_retry_count", llm_call_stats.retry_count if llm_call_stats else 0),
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
    output_paths: dict[str, Path] = {}
    canonical_report = build_content_integrity_frontend_json(
        records=records,
        findings=reporting_findings,
        enriched_pair_rows=enriched_pair_rows,
        enriched_abstract_rows=enriched_abstract_rows,
        abstract_summary_rows=abstract_summary_rows,
        operational_issues=reporting_operational_issues,
        generated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        git_revision=commit_sha,
        run_metadata=dict(run_metadata_rows),
        template_family_rows=enriched_family_rows,
    )
    output_paths["content_integrity_json"] = write_json(
        output_dir / "content_integrity_results.json",
        build_integrated_content_integrity_json(canonical_report),
    )
    output_paths["workbook"] = write_workbook(
        output_dir / "Editor_Triage_Workbook.xlsx",
        abstract_summary_rows=abstract_summary_rows,
        findings_rows=integrity_finding_rows,
        pair_rows=reviewer_pair_rows,
        operational_issue_rows=[issue.to_dict() for issue in reporting_operational_issues],
        run_metadata_rows=run_metadata_rows,
        authorship_checks_by_key=authorship_checks_by_key,
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
    )


def run_default_pipeline(
    input_dir: str | Path = "/home/pavankrishna/Projets/ASCO/real_asco_files",
    tortured_dictionary_path: str | Path = "🤷_tortured.csv",
    output_dir: str | Path = "outputs",
    authorship_json_path: str | Path = "outputs/frontend_run/final_json.json",
    validate_llm: bool = False,
    detect_llm_semantic: bool = False,
    detect_nonsense_candidates: bool = False,
    verify_trials: bool = False,
    llm_max_concurrency: int = DEFAULT_MAX_CONCURRENT_BATCHES,
) -> PipelineResult:
    config = PipelineConfig(
        input_dir=Path(input_dir),
        output_dir=Path(output_dir),
        tortured_dictionary_path=Path(tortured_dictionary_path),
        authorship_json_path=Path(authorship_json_path),
        validate_llm=validate_llm,
        detect_llm_semantic=detect_llm_semantic,
        detect_nonsense_candidates=detect_nonsense_candidates,
        verify_trials=verify_trials,
        llm_max_concurrency=llm_max_concurrency,
    )
    return run_pipeline(config)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the ASCO content integrity screening POC.")
    parser.add_argument("--input-dir", default="/home/pavankrishna/Projets/ASCO/real_asco_files", help="Folder containing Wiley XML files.")
    parser.add_argument("--tortured-dictionary", default="🤷_tortured.csv", help="Tortured phrase dictionary CSV.")
    parser.add_argument("--output-dir", default="outputs", help="Directory for generated reports.")
    parser.add_argument(
        "--authorship-json",
        default="outputs/frontend_run/final_json.json",
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
        help="Maximum semantic LLM batches in flight at once.",
    )
    args = parser.parse_args(argv)

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
    )
    print(json.dumps({key: str(value) for key, value in result.output_paths.items()}, ensure_ascii=False, indent=2))
    return 0
