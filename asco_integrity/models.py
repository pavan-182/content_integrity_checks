from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
    authors: list[str] = field(default_factory=list)
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
    status: str
    reason: str
    model_id: str
    prompt_version: str

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
    validation_status: str = ""
    validation_reason: str = ""
    validated_by: str = ""
    review_status: str = ""

    def __post_init__(self) -> None:
        if self.detector_type == "llm_response_trace" and self.check_type == "llm_trace":
            self.check_type = "known_pattern"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TemplateClusterMember:
    template_cluster_id: str
    cluster_size: int
    record_id: str
    source_file: str
    similar_record_ids: list[str] = field(default_factory=list)
    similarity_score: float = 0.0
    cluster_severity: str = "low"
    shared_skeleton_excerpt: str = ""
    metadata_context: str = ""
    template_pattern_type: str = "masked_near_duplicate"
    original_text_similarity: float = 0.0
    masked_skeleton_similarity: float = 0.0
    ngram_similarity: float = 0.0
    weighted_section_similarity: float = 0.0
    section_similarities: str = ""
    variable_substitutions: str = ""
    exclusion_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["similar_record_ids"] = " | ".join(self.similar_record_ids)
        return data
