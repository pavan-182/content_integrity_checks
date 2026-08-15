from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations

from .models import ParsedRecord
from .template_features import TemplateFeatures, build_template_features
from .template_matching_common import _candidate_pairs
from .title_templates import title_candidate_pairs


@dataclass(frozen=True, slots=True)
class CandidatePair:
    left_record_id: str
    right_record_id: str
    routes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["routes"] = " | ".join(self.routes)
        data["route_count"] = len(self.routes)
        return data


def _pairs_by_value(values: dict[str, str]) -> set[tuple[str, str]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for record_id, value in values.items():
        if value:
            buckets[value].append(record_id)
    return {
        pair
        for members in buckets.values()
        if len(members) >= 2
        for pair in combinations(sorted(members), 2)
    }


def generate_candidate_pairs(
    records: list[ParsedRecord], features: list[TemplateFeatures] | None = None,
) -> list[CandidatePair]:
    """Merge existing body retrieval with exact and title-template routes."""
    features = features if features is not None else [build_template_features(record) for record in records]
    skeletons = {feature.record_id: feature.abstract.masked for feature in features}
    normalized = {feature.record_id: feature.abstract.normalized for feature in features}
    routes: dict[tuple[str, str], set[str]] = defaultdict(set)

    def add(pairs: set[tuple[str, str]], route: str) -> None:
        for pair in pairs:
            routes[pair].add(route)

    add(_candidate_pairs(
        records,
        skeletons,
        normalized,
        {
            feature.record_id: {section.section: section.masked for section in feature.sections}
            for feature in features
        },
    ), "body_candidate")
    add(_pairs_by_value(skeletons), "exact_masked_body")
    add(_pairs_by_value(normalized), "exact_original_body")
    add(title_candidate_pairs(features), "title_template")

    section_buckets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for feature in features:
        for section in feature.sections:
            if section.masked:
                section_buckets[(section.section, section.masked)].add(feature.record_id)
    add({
        pair
        for members in section_buckets.values()
        if len(members) >= 2
        for pair in combinations(sorted(members), 2)
    }, "exact_masked_section")
    return [CandidatePair(left, right, tuple(sorted(pair_routes))) for (left, right), pair_routes in sorted(routes.items())]
