from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass

from .models import ParsedRecord
from .pair_classification import PairClassification, classify_pairs


FAMILY_VERSION = "asco-suspicious-families-v1"
ELIGIBLE_CLASSES = {"possible_template_reuse"}
MIN_EDGE_SCORE = 0.75


@dataclass(frozen=True, slots=True)
class SuspiciousFamilyMember:
    family_version: str
    family_id: str
    record_id: str
    family_size: int
    representative_record_id: str
    member_status: str
    direct_to_representative: bool
    eligible_neighbor_ids: tuple[str, ...]
    accepted_edge_count: int
    family_edge_score: float
    excluded_pair_count: int

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["eligible_neighbor_ids"] = " | ".join(self.eligible_neighbor_ids)
        return data


def build_suspicious_families(classifications: list[PairClassification]) -> list[SuspiciousFamilyMember]:
    eligible = [
        item for item in classifications
        if item.pair_class in ELIGIBLE_CLASSES and item.review_score >= MIN_EDGE_SCORE
    ]
    adjacency: dict[str, set[str]] = defaultdict(set)
    edge_scores = {}
    for item in eligible:
        pair = tuple(sorted((item.left_record_id, item.right_record_id)))
        adjacency[pair[0]].add(pair[1])
        adjacency[pair[1]].add(pair[0])
        edge_scores[pair] = item.review_score

    components, unseen = [], set(adjacency)
    while unseen:
        pending, members = [min(unseen)], set()
        while pending:
            member = pending.pop()
            if member in members:
                continue
            members.add(member)
            pending.extend(adjacency[member] - members)
        unseen -= members
        components.append(sorted(members))

    output = []
    verified_components = [members for members in components if len(members) >= 3]
    for index, members in enumerate(sorted(verified_components, key=lambda values: (-len(values), values)), start=1):
        member_set = set(members)
        family_edges = {pair: score for pair, score in edge_scores.items() if set(pair) <= member_set}
        representative = min(
            members,
            key=lambda member: (-sum(score for pair, score in family_edges.items() if member in pair), -len(adjacency[member]), member),
        )
        excluded = sum(
            1 for item in classifications
            if {item.left_record_id, item.right_record_id} <= member_set
            and not (item.pair_class in ELIGIBLE_CLASSES and item.review_score >= MIN_EDGE_SCORE)
        )
        family_score = round(sum(family_edges.values()) / len(family_edges), 3)
        for member in members:
            direct = member == representative or representative in adjacency[member]
            output.append(SuspiciousFamilyMember(
                family_version=FAMILY_VERSION,
                family_id=f"FAM-{index:04d}",
                record_id=member,
                family_size=len(members),
                representative_record_id=representative,
                member_status="representative" if member == representative else "member" if direct else "outlier",
                direct_to_representative=direct,
                eligible_neighbor_ids=tuple(sorted(adjacency[member] & member_set)),
                accepted_edge_count=len(family_edges),
                family_edge_score=family_score,
                excluded_pair_count=excluded,
            ))
    return output


def cluster_suspicious_families(records: list[ParsedRecord]) -> list[SuspiciousFamilyMember]:
    return build_suspicious_families(classify_pairs(records))
