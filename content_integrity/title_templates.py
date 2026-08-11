from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from itertools import combinations

from .template_features import TemplateFeatures
from .utils import normalize_for_matching, text_tokens


MIN_TITLE_WORDS = 4
MAX_BUCKET_SIZE = 50


def masked_title_signature(features: TemplateFeatures) -> str:
    return sha256(normalize_for_matching(features.title.masked).encode()).hexdigest()


def _comparable(features: TemplateFeatures) -> bool:
    return len(text_tokens(normalize_for_matching(features.title.masked))) >= MIN_TITLE_WORDS


def title_candidate_pairs(features: list[TemplateFeatures]) -> set[tuple[str, str]]:
    """Retrieve title-formula candidates from exact signatures and shared masked trigrams."""
    exact, trigrams = {}, {}
    for feature in features:
        if not _comparable(feature):
            continue
        exact.setdefault(masked_title_signature(feature), []).append(feature.record_id)
        tokens = text_tokens(normalize_for_matching(feature.title.masked))
        for index in range(len(tokens) - 2):
            trigrams.setdefault(tuple(tokens[index:index + 3]), []).append(feature.record_id)
    pairs = {
        pair
        for members in exact.values()
        if 2 <= len(members) <= MAX_BUCKET_SIZE
        for pair in combinations(sorted(set(members)), 2)
    }
    shared = Counter()
    for members in trigrams.values():
        if 2 <= len(members) <= MAX_BUCKET_SIZE:
            shared.update(combinations(sorted(set(members)), 2))
    pairs.update(pair for pair, count in shared.items() if count >= 2)
    return pairs


@dataclass(frozen=True, slots=True)
class TitleTemplateMatch:
    left_record_id: str
    right_record_id: str
    left_signature: str
    right_signature: str
    exact_masked_signature: bool
    masked_title_similarity: float
    original_title_similarity: float
    left_masked_title: str
    right_masked_title: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compare_title_templates(features: list[TemplateFeatures]) -> list[TitleTemplateMatch]:
    lookup = {feature.record_id: feature for feature in features}
    matches = []
    for left_id, right_id in sorted(title_candidate_pairs(features)):
        left, right = lookup[left_id], lookup[right_id]
        left_signature, right_signature = masked_title_signature(left), masked_title_signature(right)
        matches.append(TitleTemplateMatch(
            left_record_id=left_id,
            right_record_id=right_id,
            left_signature=left_signature,
            right_signature=right_signature,
            exact_masked_signature=left_signature == right_signature,
            masked_title_similarity=SequenceMatcher(
                None, normalize_for_matching(left.title.masked), normalize_for_matching(right.title.masked)
            ).ratio(),
            original_title_similarity=SequenceMatcher(None, left.title.original.lower(), right.title.original.lower()).ratio(),
            left_masked_title=left.title.masked,
            right_masked_title=right.title.masked,
        ))
    return matches
