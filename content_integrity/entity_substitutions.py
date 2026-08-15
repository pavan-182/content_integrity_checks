from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .candidate_routes import generate_candidate_pairs
from .pair_evidence import PairEvidence
from .template_features import TemplateFeatures, build_template_features
from .utils import normalize_whitespace


ENTITY_TYPE_ORDER = (
    "gene", "protein", "mirna", "lncrna", "drug", "biomarker", "pathway", "cell_line",
    "disease", "population", "endpoint", "assay", "treatment_class",
)
ENTITY_TYPES = set(ENTITY_TYPE_ORDER)
SUBSTITUTABLE_TYPES = ENTITY_TYPES - {"assay", "treatment_class"}


def _entities(feature: TemplateFeatures) -> dict[str, dict[str, tuple[str, str]]]:
    values: dict[str, dict[str, tuple[str, str]]] = {}
    for section in (feature.title, feature.abstract):
        sentences = re.split(r"(?<=[.!?])\s+", section.original)
        for entity in section.entities:
            if entity.entity_type not in ENTITY_TYPES or not entity.normalized:
                continue
            sentence = section.original if section.section == "Title" else sentences[entity.sentence_index] if entity.sentence_index < len(sentences) else section.original
            bucket = values.setdefault(entity.entity_type, {})
            if entity.normalized not in bucket or section.section != "Title":
                bucket[entity.normalized] = (entity.text, normalize_whitespace(sentence))
    return values


def _format(values: dict[str, dict[str, tuple[str, str]]], selected: dict[str, set[str]]) -> str:
    return "; ".join(
        f"{entity_type}: {' | '.join(values[entity_type][value][0] for value in sorted(selected[entity_type]))}"
        for entity_type in sorted(selected)
        if selected[entity_type]
    )


def _sentences(values: dict[str, dict[str, tuple[str, str]]], selected: dict[str, set[str]]) -> str:
    output = []
    for entity_type in [*ENTITY_TYPE_ORDER, *(item for item in sorted(selected) if item not in ENTITY_TYPES)]:
        for value in sorted(selected[entity_type]):
            sentence = values[entity_type][value][1]
            if sentence and sentence not in output:
                output.append(sentence[:240])
            if len(output) == 2:
                return " | ".join(output)
    return " | ".join(output)


@dataclass(frozen=True, slots=True)
class EntitySubstitution:
    left_record_id: str
    right_record_id: str
    shared_entities: str
    left_only_entities: str
    right_only_entities: str
    likely_substitutions: str
    left_supporting_sentences: str
    right_supporting_sentences: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def collect_entity_substitutions(
    records, evidence_rows: list[PairEvidence] | None = None,
    features: list[TemplateFeatures] | None = None,
) -> list[EntitySubstitution]:
    features = features if features is not None else [build_template_features(record) for record in records]
    lookup = {feature.record_id: _entities(feature) for feature in features}
    output = []
    pairs = (
        [(item.left_record_id, item.right_record_id) for item in evidence_rows]
        if evidence_rows is not None
        else [(item.left_record_id, item.right_record_id) for item in generate_candidate_pairs(records, features)]
    )
    for left_id, right_id in pairs:
        left, right = lookup[left_id], lookup[right_id]
        shared = {entity_type: set(left.get(entity_type, {})) & set(right.get(entity_type, {})) for entity_type in ENTITY_TYPES}
        left_only = {entity_type: set(left.get(entity_type, {})) - set(right.get(entity_type, {})) for entity_type in ENTITY_TYPES}
        right_only = {entity_type: set(right.get(entity_type, {})) - set(left.get(entity_type, {})) for entity_type in ENTITY_TYPES}
        substitutions = []
        substitution_types = {entity_type for entity_type in SUBSTITUTABLE_TYPES if left_only[entity_type] and right_only[entity_type]}
        for entity_type in ENTITY_TYPE_ORDER:
            if entity_type not in substitution_types:
                continue
            for old, new in zip(sorted(left_only[entity_type]), sorted(right_only[entity_type])):
                substitutions.append(f"{entity_type}: {left[entity_type][old][0]} -> {right[entity_type][new][0]}")
        output.append(EntitySubstitution(
            left_record_id=left_id,
            right_record_id=right_id,
            shared_entities=_format(left, shared),
            left_only_entities=_format(left, left_only),
            right_only_entities=_format(right, right_only),
            likely_substitutions="; ".join(substitutions),
            left_supporting_sentences=_sentences(left, left_only),
            right_supporting_sentences=_sentences(right, right_only),
        ))
    return output
