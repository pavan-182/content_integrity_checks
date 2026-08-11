from __future__ import annotations

from dataclasses import asdict, dataclass

from .evidence_scoring import TieredEvidenceScore, score_pair_evidence
from .models import ParsedRecord
from .study_context import StudyContextComparison, compare_study_context


CLASSIFIER_VERSION = "asco-pair-classifier-v1"
EXACT_REUSE = {"exact_original_body", "exact_results_section"}


def _context_evidence(context: StudyContextComparison) -> tuple[str, ...]:
    evidence = []
    for field in (
        "shared_trial_ids", "shared_databases", "shared_study_periods", "shared_sample_sizes",
        "shared_populations", "shared_treatments", "shared_endpoints",
    ):
        if getattr(context, field):
            evidence.append(field)
    if context.shared_authors:
        evidence.append("shared_authors")
    if context.shared_affiliations:
        evidence.append("shared_affiliations")
    if context.explicit_companion_wording:
        evidence.append("explicit_companion_wording")
    return tuple(evidence)


@dataclass(frozen=True, slots=True)
class PairClassification:
    classifier_version: str
    left_record_id: str
    right_record_id: str
    pair_class: str
    rule_path: str
    primary_evidence: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    contextual_evidence: tuple[str, ...]
    context_interpretation: str
    review_score: float
    limitations: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for field in ("primary_evidence", "supporting_evidence", "contextual_evidence"):
            data[field] = " | ".join(data[field])
        return data


def classify_pair(score: TieredEvidenceScore, context: StudyContextComparison) -> PairClassification:
    exact_reuse = bool(EXACT_REUSE & set(score.primary_evidence))
    related_context = context.context_interpretation != "no_structured_context"
    if not score.primary_evidence:
        pair_class = "insufficient_evidence"
        rule_path = "no primary evidence -> insufficient evidence"
        limitations = "Retrieval, supporting, and contextual signals cannot independently create a finding."
    elif exact_reuse and related_context:
        pair_class = "possible_related_duplicate"
        rule_path = "exact body/Results reuse + related-study context -> possible related duplicate"
        limitations = "Study context does not suppress exact full-abstract or Results reuse."
    elif context.context_interpretation == "likely_companion_analysis":
        pair_class = "possible_companion_analysis"
        rule_path = "primary evidence + aligned study context + endpoint/companion difference -> possible companion analysis"
        limitations = "Companion context is an interpretation and remains subject to editorial review."
    else:
        pair_class = "possible_template_reuse"
        rule_path = "primary evidence + no aligned companion explanation -> possible template reuse"
        limitations = "This reproducible rule is a review route, not a misconduct determination."
    return PairClassification(
        classifier_version=CLASSIFIER_VERSION,
        left_record_id=score.left_record_id,
        right_record_id=score.right_record_id,
        pair_class=pair_class,
        rule_path=rule_path,
        primary_evidence=score.primary_evidence,
        supporting_evidence=score.supporting_evidence,
        contextual_evidence=tuple(sorted(set(score.contextual_evidence) | set(_context_evidence(context)))),
        context_interpretation=context.context_interpretation,
        review_score=score.review_score,
        limitations=limitations,
    )


def classify_pairs(
    records: list[ParsedRecord],
    scores: list[TieredEvidenceScore] | None = None,
    context_rows: list[StudyContextComparison] | None = None,
) -> list[PairClassification]:
    contexts = {
        (item.left_record_id, item.right_record_id): item
        for item in context_rows or compare_study_context(records)
    }
    return [
        classify_pair(score, contexts[(score.left_record_id, score.right_record_id)])
        for score in scores or score_pair_evidence(records)
    ]
