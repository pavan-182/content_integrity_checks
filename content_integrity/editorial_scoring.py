from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import ParsedRecord
from .pair_classification import PairClassification, classify_pairs


SCORING_VERSION = "asco-editorial-priority-v1"
# ponytail: bands reuse audited primary-evidence weights; re-fit when labelled pairs include this score schema.
HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class EditorialPriority:
    scoring_version: str
    left_record_id: str
    right_record_id: str
    pair_class: str
    editorial_score: float
    review_priority: str
    priority_reason: str
    primary_evidence: tuple[str, ...]
    context_interpretation: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["primary_evidence"] = " | ".join(data["primary_evidence"])
        return data


def assign_editorial_priority(classification: PairClassification) -> EditorialPriority:
    score = classification.review_score
    if classification.pair_class == "insufficient_evidence":
        priority, reason = "None", "No primary evidence."
    elif classification.pair_class in {"possible_companion_analysis", "possible_related_work"}:
        priority, reason = "Low", "Aligned related-study context caps priority; context does not increase suspicion."
    elif score >= HIGH_THRESHOLD:
        priority, reason = "High", "Exact or substantial primary evidence meets the 0.85 high-priority band."
    elif score >= MEDIUM_THRESHOLD:
        priority, reason = "Medium", "Strong masked-body primary evidence meets the 0.75 medium-priority band."
    else:
        priority, reason = "Low", "Primary evidence is limited to the 0.65 title-template band."
    return EditorialPriority(
        scoring_version=SCORING_VERSION,
        left_record_id=classification.left_record_id,
        right_record_id=classification.right_record_id,
        pair_class=classification.pair_class,
        editorial_score=score,
        review_priority=priority,
        priority_reason=reason,
        primary_evidence=classification.primary_evidence,
        context_interpretation=classification.context_interpretation,
    )


def score_editorial_priorities(records: list[ParsedRecord]) -> list[EditorialPriority]:
    return [assign_editorial_priority(item) for item in classify_pairs(records)]
