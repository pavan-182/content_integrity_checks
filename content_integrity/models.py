from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


INACTIVE_VALIDATION_STATUSES = {
    "candidate",
    "pending",
    "rejected",
    "uncertain",
    "validation_failed",
}
REVIEW_CANDIDATE_VALIDATION_STATUSES = {"candidate", "pending", "uncertain"}


@dataclass(slots=True)
class ParseWarning:
    warning_code: str
    warning_message: str
    field_name: str = ""
    section_or_field: str = ""
    severity: str = "warning"
    evidence_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TraceTextBlock:
    field_name: str
    section_label: str
    block_type: str
    block_index: int
    source_text: str
    source_start: int | None = None
    source_end: int | None = None


@dataclass(slots=True)
class ParsedRecord:
    source_file: str
    schema_type: str = ""
    record_id: str = ""
    doi: str = ""
    title: str = ""
    abstract_text: str = ""
    abstract_sections: list[dict[str, str]] = field(default_factory=list)
    structured_abstract: bool = False
    keywords: list[str] = field(default_factory=list)
    trial_ids: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    primary_author: str = ""
    affiliations: list[str] = field(default_factory=list)
    journal: str = ""
    article_type: str = ""
    publication_year: str = ""
    raw_text: str = ""
    excluded_sections: list[str] = field(default_factory=list)
    parse_status: str = "parsed"
    parse_warnings: list[ParseWarning] = field(default_factory=list)
    trace_text_blocks: list[TraceTextBlock] = field(default_factory=list)
    trace_preprocessing_fallback: bool = False

    @property
    def author_count(self) -> int:
        return len(self.authors)

    @property
    def affiliation_count(self) -> int:
        return len(self.affiliations)

    @property
    def abstract_section_count(self) -> int:
        return len(self.abstract_sections)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["parse_warnings"] = [warning.to_dict() for warning in self.parse_warnings]
        data["author_count"] = self.author_count
        data["affiliation_count"] = self.affiliation_count
        data["abstract_section_count"] = self.abstract_section_count
        return data


@dataclass(slots=True)
class ValidationResult:
    finding_id: str
    # Supported: "", confirmed, rejected, uncertain, validation_failed, candidate.
    # Reports may label the blank status as not_validated.
    status: str
    reason: str
    model_id: str
    prompt_version: str
    # Calibrated P(genuine concern) in [0, 1] from the validator model; None when not supplied
    # (e.g. llm_response_trace validation, which does not score confidence).
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Finding:
    finding_id: str
    record_id: str
    source_file: str
    detector_type: str
    category: str
    matched_text: str
    evidence_snippet: str
    section_or_field: str
    severity: str
    confidence: float | str
    rule_id: str
    check_type: str = ""
    expected_term: str = ""
    # Supported: "", confirmed, rejected, uncertain, validation_failed, candidate.
    # Reports may label the blank status as not_validated.
    validation_status: str = ""
    validation_reason: str = ""
    validated_by: str = ""
    review_status: str = ""
    review_reason: str = ""
    # numerical_contradiction only: the derived percentage/subgroup-sum/numerator/valid
    # boundary value, how far the reported number is from it, and the tolerance it was
    # checked against.
    calculated_value: float | None = None
    difference: float | None = None
    tolerance: float | None = None
    # unverifiable_clinical_trial only: which registry the trial ID was checked against,
    # its normalized form, whether it matches that registry's expected ID format, and the
    # registry lookup's resolved status.
    registry_name: str = ""
    normalized_trial_id: str = ""
    format_valid: bool | None = None
    verification_status: str = ""

    def __post_init__(self) -> None:
        if self.detector_type == "llm_response_trace" and self.check_type == "llm_trace":
            self.check_type = "known_pattern"

    @property
    def normalized_validation_status(self) -> str:
        return self.validation_status.strip().lower() or "not_validated"

    @property
    def active(self) -> bool:
        return self.normalized_validation_status not in INACTIVE_VALIDATION_STATUSES

    @property
    def review_candidate(self) -> bool:
        return self.normalized_validation_status in REVIEW_CANDIDATE_VALIDATION_STATUSES

    @property
    def validation_failed(self) -> bool:
        return self.normalized_validation_status == "validation_failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "detected": True,
            "active": self.active,
            "review_candidate": self.review_candidate,
        }


@dataclass(frozen=True, slots=True)
class OperationalIssue:
    component: str
    error_type: str
    message: str
    record_id: str = ""
    source_file: str = ""
    status: str = "failed"
    retry_count: int = 0
    recoverable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
