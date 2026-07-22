from __future__ import annotations

from ..models import ParsedRecord, TemplateClusterMember
from ..template_detection import cluster_templates as _cluster_templates


def cluster_templates(
    records: list[ParsedRecord],
    similarity_threshold: float = 0.88,
) -> list[TemplateClusterMember]:
    return _cluster_templates(records, similarity_threshold=similarity_threshold)
