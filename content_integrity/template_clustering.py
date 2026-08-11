from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .models import ParsedRecord

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3, "very_high": 4}
CONFIDENCE_BY_RANK = {rank: confidence for confidence, rank in CONFIDENCE_RANK.items()}


@dataclass(frozen=True, slots=True)
class PairFinding:
    pair_id: str
    record_id: str
    matched_record_id: str
    source_file: str
    matched_source_file: str
    title: str
    matched_title: str
    primary_match_type: str
    supporting_match_types: list[str]
    confidence: str
    severity: str
    matched_sections: list[str]
    matched_sentence_count: int = 0
    shared_text_coverage: float = 0.0
    original_text_similarity: float = 0.0
    masked_skeleton_similarity: float = 0.0
    ngram_similarity: float = 0.0
    high_value_section_similarity: float = 0.0
    weighted_section_similarity: float = 0.0
    variable_substitutions: str = ""
    relationship_context: str = ""
    pair_classification: str = "possible_template_reuse"
    evidence_excerpt: str = ""
    review_status: str = "candidate"
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_pair_key(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right)))


def _confidence(value: str) -> int:
    return CONFIDENCE_RANK.get(value, 0)


def _severity(confidence: str, relationship_context: str) -> str:
    context = relationship_context.lower()
    if (
        "shared trial id:" in context
        or "declared related analysis" in context
        or "explicitly declare a related study" in context
    ):
        return "low"
    if "overlapping authors:" in context or "shared affiliations:" in context:
        return "medium" if confidence in {"high", "very_high"} else "low"
    return "high" if confidence in {"high", "very_high"} else "medium"


def _pair_classification(relationship_context: str) -> str:
    context = relationship_context.lower()
    if (
        "shared trial id:" in context
        or "declared related analysis" in context
        or "explicitly declare a related study" in context
    ):
        return "possible_companion_analysis"
    if "overlapping authors:" in context or "shared affiliations:" in context:
        return "possible_related_work"
    return "possible_template_reuse"


def _evidence_excerpt(item: Any) -> str:
    sentences = getattr(item, "record_matched_sentences", ())
    if sentences:
        return " | ".join(sentences)
    blocks = getattr(item, "matched_text_blocks", ())
    if blocks:
        return " | ".join(sorted(set(blocks)))
    return getattr(item, "shared_skeleton_excerpt", "") or getattr(item, "evidence", "")


def merge_pair_findings(exact_findings: list[Any], entity_findings: list[Any]) -> list[PairFinding]:
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for finding in [*exact_findings, *entity_findings]:
        grouped[canonical_pair_key(finding.record_id, finding.matched_record_id)].append(finding)

    merged: list[PairFinding] = []
    for pair in sorted(grouped):
        source = sorted(
            grouped[pair],
            key=lambda item: (-_confidence(item.confidence), item.check_type, item.match_type),
        )
        primary = source[0]
        confidence = primary.confidence
        supporting = sorted({item.match_type for item in source})
        sections = sorted({section for item in source for section in item.matched_sections})
        relationship = "; ".join(sorted({item.relationship_context for item in source if item.relationship_context}))
        record_details = {
            item.record_id: (item.source_file, item.title)
            for item in source
        } | {
            item.matched_record_id: (item.matched_source_file, item.matched_title)
            for item in source
        }
        merged.append(
            PairFinding(
                pair_id=f"PAIR-{pair[0]}--{pair[1]}",
                record_id=pair[0],
                matched_record_id=pair[1],
                source_file=record_details[pair[0]][0],
                matched_source_file=record_details[pair[1]][0],
                title=record_details[pair[0]][1],
                matched_title=record_details[pair[1]][1],
                primary_match_type=primary.match_type,
                supporting_match_types=supporting,
                confidence=confidence,
                severity=_severity(confidence, relationship),
                matched_sections=sections,
                matched_sentence_count=max(getattr(item, "matched_sentence_count", 0) for item in source),
                shared_text_coverage=max(getattr(item, "shared_text_coverage", 0.0) for item in source),
                original_text_similarity=max(getattr(item, "original_text_similarity", 0.0) for item in source),
                masked_skeleton_similarity=max(getattr(item, "masked_skeleton_similarity", 0.0) for item in source),
                ngram_similarity=max(getattr(item, "ngram_similarity", 0.0) for item in source),
                high_value_section_similarity=max(getattr(item, "high_value_section_similarity", 0.0) for item in source),
                weighted_section_similarity=max(getattr(item, "weighted_section_similarity", 0.0) for item in source),
                variable_substitutions="; ".join(sorted({item.variable_substitutions for item in source if getattr(item, "variable_substitutions", "")})),
                relationship_context=relationship,
                pair_classification=_pair_classification(relationship),
                evidence_excerpt="; ".join(sorted({_evidence_excerpt(item) for item in source if _evidence_excerpt(item)})),
                review_status=primary.review_status,
                evidence="; ".join(sorted({item.evidence for item in source if item.evidence})),
            )
        )
    return merged


def other_record_id(pair: PairFinding, record_id: str) -> str:
    return pair.matched_record_id if pair.record_id == record_id else pair.record_id


def _dominant_pattern(edges: list[PairFinding]) -> str:
    counts = Counter(edge.primary_match_type for edge in edges)
    strongest = {
        pattern: max(
            CONFIDENCE_RANK[edge.confidence]
            for edge in edges
            if edge.primary_match_type == pattern
        )
        for pattern in counts
    }
    return min(counts, key=lambda pattern: (-counts[pattern], -strongest[pattern], pattern))


def _family_confidence(
    size: int,
    density: float,
    median_confidence: str,
    high_value_evidence: bool,
    medoid_verified: bool,
) -> str:
    if not medoid_verified:
        return "low"
    rank = CONFIDENCE_RANK[median_confidence]
    if size >= 3 and density >= 0.66 and rank >= 3 and high_value_evidence:
        return "very_high"
    if rank >= 3 and (density >= 0.5 or high_value_evidence):
        return "high"
    return "medium"


def cluster_template_findings(pair_findings: list[PairFinding], records: list[ParsedRecord]) -> list[dict[str, Any]]:
    """Group accepted pair edges, then verify every member against the medoid."""
    record_ids = {record.record_id for record in records}
    edges = [
        finding for finding in pair_findings
        if finding.pair_classification == "possible_template_reuse"
        and (
            finding.confidence in {"high", "very_high"}
            or (finding.confidence == "medium" and len(finding.supporting_match_types) > 1)
        )
    ]
    adjacency: dict[str, set[str]] = {record_id: set() for record_id in record_ids}
    for edge in edges:
        adjacency.setdefault(edge.record_id, set()).add(edge.matched_record_id)
        adjacency.setdefault(edge.matched_record_id, set()).add(edge.record_id)
    components: list[list[str]] = []
    unseen = set(adjacency)
    while unseen:
        start = min(unseen)
        stack, component = [start], []
        unseen.remove(start)
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in sorted(adjacency[current] & unseen):
                unseen.remove(neighbour)
                stack.append(neighbour)
        if len(component) >= 2:
            components.append(sorted(component))

    lookup = {record.record_id: record for record in records}
    rows: list[dict[str, Any]] = []
    for index, members in enumerate(sorted(components, key=lambda value: (-len(value), value)), 1):
        edge_map = {canonical_pair_key(edge.record_id, edge.matched_record_id): edge for edge in edges}
        medoid = max(
            members,
            key=lambda member: sum(
                CONFIDENCE_RANK[edge_map[canonical_pair_key(member, other)].confidence]
                for other in members if canonical_pair_key(member, other) in edge_map
            ),
        )
        verified = [member for member in members if member == medoid or canonical_pair_key(member, medoid) in edge_map]
        if len(verified) < 2:
            continue
        group_edges = [edge for key, edge in edge_map.items() if set(key) <= set(verified)]
        density = len(group_edges) / (len(verified) * (len(verified) - 1) / 2)
        confidence_ranks = sorted(CONFIDENCE_RANK[edge.confidence] for edge in group_edges)
        median_confidence = CONFIDENCE_BY_RANK[confidence_ranks[(len(confidence_ranks) - 1) // 2]]
        high_value_evidence = any(
            edge.confidence in {"high", "very_high"}
            and (
                edge.high_value_section_similarity >= 0.88
                or any(
                    label in section.lower()
                    for section in edge.matched_sections
                    for label in ("result", "conclusion")
                )
            )
            for edge in group_edges
        )
        medoid_verified = all(
            member == medoid or canonical_pair_key(member, medoid) in edge_map
            for member in verified
        )
        family_id = f"TPL-{index:04d}"
        for member in verified:
            rows.append({
                "template_cluster_id": family_id,
                "cluster_size": len(verified),
                "record_id": member,
                "source_file": lookup[member].source_file,
                "similar_record_ids": sorted(other for other in verified if other != member),
                "cluster_severity": max((edge.severity for edge in group_edges), key=lambda value: {"low": 1, "medium": 2, "high": 3}.get(value, 0), default="medium"),
                "shared_skeleton_excerpt": next((edge.evidence_excerpt for edge in group_edges if edge.evidence_excerpt), ""),
                "template_pattern_type": _dominant_pattern(group_edges),
                "matched_sections": sorted({section for edge in group_edges for section in edge.matched_sections}),
                "edge_density": round(density, 3),
                "median_pair_confidence": median_confidence,
                "family_confidence": _family_confidence(
                    len(verified),
                    density,
                    median_confidence,
                    high_value_evidence,
                    medoid_verified,
                ),
                "medoid_verification_passed": medoid_verified,
                "representative_record_id": medoid,
                "changed_entity_types": sorted({part.split(":", 1)[0] for edge in group_edges for part in edge.variable_substitutions.split("; ") if ":" in part}),
            })
    return sorted(rows, key=lambda row: (row["template_cluster_id"], row["record_id"]))
