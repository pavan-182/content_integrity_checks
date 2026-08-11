from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .candidate_routes import generate_candidate_pairs
from .models import ParsedRecord
from .pair_evidence import PairEvidence
from .template_features import TemplateFeatures, build_template_features
from .utils import normalize_for_matching


COMPANION_RE = re.compile(
    r"\b(?:subgroup|secondary|interim|final|follow[- ]up|post hoc|exploratory|extension|companion)\s+"
    r"(?:analys(?:is|es)|study)\b",
    re.IGNORECASE,
)
SAMPLE_SIZE_RE = re.compile(
    r"\b(?:n\s*=\s*|enrolled\s+|included\s+|randomi[sz]ed\s+|total of\s+)?"
    r"(\d{2,6})\s+(?:patients?|participants?|subjects?)\b",
    re.IGNORECASE,
)
CONTEXT_TYPES = {"registry", "date", "population", "endpoint", "drug", "treatment_class", "trial_id"}


def _values(feature: TemplateFeatures) -> dict[str, set[str]]:
    values = {entity_type: set() for entity_type in CONTEXT_TYPES}
    for entity in (*feature.title.entities, *feature.abstract.entities):
        if entity.entity_type in values and entity.normalized:
            values[entity.entity_type].add(entity.text.upper() if entity.entity_type == "trial_id" else entity.normalized)
    values["trial_id"].update(value.upper() for value in feature.trial_ids)
    return values


def _overlap(left: set[str], right: set[str]) -> str:
    if not left or not right:
        return "unknown"
    if left == right:
        return "same"
    return "partial" if left & right else "different"


def _normalized_overlap(left: list[str], right: list[str]) -> set[str]:
    return set(map(normalize_for_matching, left)) & set(map(normalize_for_matching, right)) - {""}


@dataclass(frozen=True, slots=True)
class StudyContextComparison:
    left_record_id: str
    right_record_id: str
    shared_trial_ids: tuple[str, ...]
    shared_databases: tuple[str, ...]
    shared_study_periods: tuple[str, ...]
    shared_sample_sizes: tuple[str, ...]
    shared_populations: tuple[str, ...]
    shared_treatments: tuple[str, ...]
    shared_endpoints: tuple[str, ...]
    left_only_endpoints: tuple[str, ...]
    right_only_endpoints: tuple[str, ...]
    shared_authors: int
    shared_affiliations: int
    same_trial_id: bool
    same_database: bool
    same_population: str
    same_period: bool
    endpoint_overlap: str
    explicit_companion_wording: bool
    context_interpretation: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for field, value in data.items():
            if isinstance(value, tuple):
                data[field] = " | ".join(value)
        return data


def compare_study_context(
    records: list[ParsedRecord], evidence_rows: list[PairEvidence] | None = None,
) -> list[StudyContextComparison]:
    features = [build_template_features(record) for record in records]
    feature_lookup = {feature.record_id: feature for feature in features}
    record_lookup = {record.record_id: record for record in records}
    value_lookup = {feature.record_id: _values(feature) for feature in features}
    output = []
    pairs = (
        [(item.left_record_id, item.right_record_id) for item in evidence_rows]
        if evidence_rows is not None
        else [(item.left_record_id, item.right_record_id) for item in generate_candidate_pairs(records, features)]
    )
    for left_id, right_id in pairs:
        left_record, right_record = record_lookup[left_id], record_lookup[right_id]
        left, right = value_lookup[left_id], value_lookup[right_id]
        shared_trials = left["trial_id"] & right["trial_id"]
        shared_databases = left["registry"] & right["registry"]
        shared_periods = left["date"] & right["date"]
        shared_populations = left["population"] & right["population"]
        shared_treatments = (left["drug"] | left["treatment_class"]) & (right["drug"] | right["treatment_class"])
        shared_endpoints = left["endpoint"] & right["endpoint"]
        left_sizes = set(SAMPLE_SIZE_RE.findall(feature_lookup[left_id].abstract.original))
        right_sizes = set(SAMPLE_SIZE_RE.findall(feature_lookup[right_id].abstract.original))
        shared_sizes = left_sizes & right_sizes
        population_match = _overlap(left["population"], right["population"])
        endpoint_match = _overlap(left["endpoint"], right["endpoint"])
        companion = bool(
            COMPANION_RE.search(f"{left_record.title} {left_record.abstract_text}")
            or COMPANION_RE.search(f"{right_record.title} {right_record.abstract_text}")
        )
        aligned = bool(
            shared_trials
            or (shared_databases and (shared_populations or shared_periods or shared_sizes))
            or (shared_populations and (shared_periods or shared_sizes))
        )
        if aligned and (companion or endpoint_match in {"partial", "different"}):
            interpretation = "likely_companion_analysis"
        elif aligned:
            interpretation = "likely_same_study"
        elif companion or shared_databases or shared_populations or shared_periods:
            interpretation = "possible_related_study"
        else:
            interpretation = "no_structured_context"
        output.append(StudyContextComparison(
            left_record_id=left_id,
            right_record_id=right_id,
            shared_trial_ids=tuple(sorted(shared_trials)),
            shared_databases=tuple(sorted(shared_databases)),
            shared_study_periods=tuple(sorted(shared_periods)),
            shared_sample_sizes=tuple(sorted(shared_sizes)),
            shared_populations=tuple(sorted(shared_populations)),
            shared_treatments=tuple(sorted(shared_treatments)),
            shared_endpoints=tuple(sorted(shared_endpoints)),
            left_only_endpoints=tuple(sorted(left["endpoint"] - right["endpoint"])),
            right_only_endpoints=tuple(sorted(right["endpoint"] - left["endpoint"])),
            shared_authors=len(_normalized_overlap(left_record.authors, right_record.authors)),
            shared_affiliations=len(_normalized_overlap(left_record.affiliations, right_record.affiliations)),
            same_trial_id=bool(shared_trials),
            same_database=bool(shared_databases),
            same_population=population_match,
            same_period=bool(shared_periods),
            endpoint_overlap=endpoint_match,
            explicit_companion_wording=companion,
            context_interpretation=interpretation,
        ))
    return output
