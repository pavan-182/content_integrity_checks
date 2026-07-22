from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import blake2b
from itertools import combinations
from typing import Iterable

from .models import ParsedRecord, TemplateClusterMember
from .utils import join_nonempty, normalize_for_matching, normalize_whitespace, text_tokens


DATE_PATTERNS = [
    re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:19|20)\d{2}/\d{2}/\d{2}\b"),
    re.compile(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
        r"[a-z]*\.?\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
        re.IGNORECASE,
    ),
]
PVAL_PATTERN = re.compile(r"\bp\s*[<>=]\s*0?\.\d+\b", re.IGNORECASE)
PERCENT_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\s*%")
NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\b")
TRIAL_PATTERN = re.compile(r"\b(?:NCT|ACTRN|ISRCTN|EUCTR|EudraCT|ChiCTR)[A-Za-z0-9\-_.]+\b", re.IGNORECASE)
GENE_PATTERN = re.compile(r"\b(?:[A-Z]{2,}\d+[A-Z0-9\-]*|[A-Z]{2,}-\d+[A-Z0-9\-]*)\b")
DRUG_SUFFIX_PATTERN = re.compile(
    r"\b[A-Za-z]{4,}(?:mab|nib|tinib|cept|vir|statin|caftor|parib|azole|cillin|ximab|zumab|ib)\b",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[\w.\-+]+@[\w.\-]+\.\w+\b")
PLACEHOLDER_TOKEN_RE = re.compile(r"<(URL|EMAIL|DATE|TRIAL_ID|PVAL|PCT|GENE|DRUG|NUM)>")
PLACEHOLDER_TOKENS = {"url", "email", "date", "trial", "id", "pval", "pct", "gene", "drug", "num"}
BOILERPLATE_RE = re.compile(r"\b(?:n a n a|not applicable|none|n a)\b")
# ponytail: provisional transparent weights; replace after labelled ASCO calibration.
SECTION_WEIGHTS = {"background": 0.1, "methods": 0.2, "results": 0.4, "conclusions": 0.3}
NGRAM_SIZE = 5
MIN_SHARED_NGRAMS = 3
MAX_NGRAM_BUCKET = 50
MIN_NGRAM_CONTAINMENT = 0.50
MIN_ORIGINAL_SUPPORT = 0.55
MIN_NGRAM_ORIGINAL_SUPPORT = 0.65


def _entity_shape_key(skeleton: str, bucket_size: int = 5) -> str:
    counts = Counter(PLACEHOLDER_TOKEN_RE.findall(skeleton))
    bucketed = sorted(f"{token}{count // bucket_size}" for token, count in counts.items())
    return ":".join(bucketed) if bucketed else "NOPLACEHOLDER"


def _sentence_split(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?])\s+", normalize_whitespace(text))
    return [piece.strip() for piece in pieces if piece and piece.strip()]


def _mask_variables(text: str) -> str:
    masked = normalize_whitespace(text)
    masked = GENE_PATTERN.sub("<GENE>", masked)
    masked = masked.lower().replace("<gene>", "<GENE>")
    masked = URL_PATTERN.sub("<URL>", masked)
    masked = EMAIL_PATTERN.sub("<EMAIL>", masked)
    for pattern in DATE_PATTERNS:
        masked = pattern.sub("<DATE>", masked)
    masked = TRIAL_PATTERN.sub("<TRIAL_ID>", masked)
    masked = PVAL_PATTERN.sub("<PVAL>", masked)
    masked = PERCENT_PATTERN.sub("<PCT>", masked)
    masked = DRUG_SUFFIX_PATTERN.sub("<DRUG>", masked)
    masked = re.sub(r"\b\d+(?:\.\d+)?\b", "<NUM>", masked)
    masked = re.sub(r"\s+", " ", masked).strip()
    return masked


def build_skeleton_text(record: ParsedRecord) -> str:
    source = record.abstract_text or ""
    if not source.strip():
        source = record.title or ""
    if not source.strip() and record.raw_text.strip():
        source = record.raw_text
    return _mask_variables(source)


def build_normalized_text(record: ParsedRecord) -> str:
    return normalize_for_matching(record.abstract_text or record.title or record.raw_text)


def _content_class(record: ParsedRecord, skeleton: str) -> str:
    text = normalize_for_matching(f"{record.article_type} {record.title} {record.abstract_text}")
    if "trial in progress" in text:
        return "trials_in_progress"
    if "late breaking" in text:
        return "late_breaking_placeholder"
    tokens = text_tokens(BOILERPLATE_RE.sub(" ", normalize_for_matching(skeleton)))
    content_count = sum(token not in PLACEHOLDER_TOKENS for token in tokens)
    if not tokens or content_count == 0:
        return "empty_or_unusable"
    if content_count < 5 or content_count / len(tokens) < 0.35:
        return "administrative_boilerplate"
    return "valid_short" if content_count < 25 else "comparable"


def _shingles(text: str, size: int = NGRAM_SIZE) -> set[tuple[str, ...]]:
    tokens = text_tokens(text)
    return {tuple(tokens[index:index + size]) for index in range(len(tokens) - size + 1)}


def _ngram_similarity(left: str, right: str) -> float:
    left_shingles = _shingles(left)
    right_shingles = _shingles(right)
    if not left_shingles or not right_shingles:
        return 0.0
    return len(left_shingles & right_shingles) / min(len(left_shingles), len(right_shingles))


def _section_similarities(left: ParsedRecord, right: ParsedRecord) -> dict[str, float]:
    left_sections = {normalize_for_matching(item["section"]): item["text"] for item in left.abstract_sections}
    right_sections = {normalize_for_matching(item["section"]): item["text"] for item in right.abstract_sections}
    return {
        section: _similarity(_mask_variables(left_sections[section]), _mask_variables(right_sections[section]))
        for section in left_sections.keys() & right_sections.keys()
        if section != "abstract"
    }


def _high_value_section_similarity(left: ParsedRecord, right: ParsedRecord) -> float:
    scores = _section_similarities(left, right)
    return max(
        (score for section, score in scores.items() if "result" in section or "conclusion" in section),
        default=0.0,
    )


def _weighted_section_similarity(left: ParsedRecord, right: ParsedRecord) -> float:
    scores = _section_similarities(left, right)
    weighted = [
        (score, weight)
        for section, score in scores.items()
        for label, weight in SECTION_WEIGHTS.items()
        if label in section
    ]
    return sum(score * weight for score, weight in weighted) / sum(weight for _, weight in weighted) if weighted else 0.0


def _variable_substitutions(left: str, right: str) -> str:
    patterns = {
        "trial_id": TRIAL_PATTERN,
        "date": DATE_PATTERNS[0],
        "pvalue": PVAL_PATTERN,
        "percent": PERCENT_PATTERN,
        "gene": GENE_PATTERN,
        "drug": DRUG_SUFFIX_PATTERN,
        "number": NUMBER_PATTERN,
    }
    changes: list[str] = []
    for label, pattern in patterns.items():
        left_values = [normalize_whitespace(value) for value in pattern.findall(left)]
        right_values = [normalize_whitespace(value) for value in pattern.findall(right)]
        if left_values != right_values and (left_values or right_values):
            changes.append(f"{label}: {' | '.join(left_values[:4]) or 'none'} -> {' | '.join(right_values[:4]) or 'none'}")
    return "; ".join(changes)


@dataclass(frozen=True, slots=True)
class _PairEvidence:
    masked: float
    original: float
    ngram: float
    high_value_section: float
    weighted_section: float

    @property
    def structural(self) -> float:
        return max(self.masked, self.high_value_section, self.weighted_section)

    @property
    def score(self) -> float:
        return max(self.structural, self.ngram)


@dataclass(slots=True)
class _UnionFind:
    parent: dict[str, str]
    size: dict[str, int]

    @classmethod
    def create(cls, items: Iterable[str]) -> "_UnionFind":
        parent = {item: item for item in items}
        size = {item: 1 for item in items}
        return cls(parent=parent, size=size)

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: str, right: str) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left == root_right:
            return
        if self.size[root_left] < self.size[root_right]:
            root_left, root_right = root_right, root_left
        self.parent[root_right] = root_left
        self.size[root_left] += self.size[root_right]


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens = text_tokens(left)
    right_tokens = text_tokens(right)
    forward = SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).ratio()
    reverse = SequenceMatcher(None, right_tokens, left_tokens, autojunk=False).ratio()
    return (forward + reverse) / 2


def _metadata_context(record: ParsedRecord) -> str:
    return join_nonempty(
        [
            f"journal={record.journal}" if record.journal else "",
            f"type={record.article_type}" if record.article_type else "",
            f"year={record.publication_year}" if record.publication_year else "",
            f"authors={record.author_count}",
            f"affiliations={record.affiliation_count}",
            f"schema={record.schema_type}" if record.schema_type else "",
        ],
        delimiter="; ",
    )


def _shared_excerpt(skeletons: list[str]) -> str:
    sentence_counter: Counter[str] = Counter()
    for skeleton in skeletons:
        sentence_counter.update(_sentence_split(skeleton))
    repeated = [sentence for sentence, count in sentence_counter.items() if count >= 2]
    if repeated:
        repeated.sort(key=lambda value: (len(value), sentence_counter[value]), reverse=True)
        return repeated[0][:240]
    if skeletons:
        return skeletons[0][:240]
    return ""


def _candidate_pairs(
    records: list[ParsedRecord],
    skeletons: dict[str, str],
    normalized_texts: dict[str, str],
) -> set[tuple[str, str]]:
    blocks: dict[str, list[str]] = defaultdict(list)
    ngram_index: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for record in records:
        record_id = record.record_id
        skeleton = skeletons[record_id]
        tokens = text_tokens(skeleton)
        length_bucket = len(tokens) // 40
        blocks[f"prefix:{length_bucket}:{' '.join(tokens[:15])}"].append(record_id)
        shape_key = f"shape:{length_bucket}:{_entity_shape_key(skeleton)}"
        if not shape_key.endswith(":NOPLACEHOLDER"):
            blocks[shape_key].append(record_id)
        blocks[f"exact-original:{blake2b(normalized_texts[record_id].encode(), digest_size=16).hexdigest()}"].append(record_id)
        blocks[f"exact-masked:{blake2b(skeleton.encode(), digest_size=16).hexdigest()}"].append(record_id)
        for section in record.abstract_sections:
            section_text = _mask_variables(section["text"])
            if len(text_tokens(section_text)) >= 15:
                fingerprint = blake2b(section_text.encode(), digest_size=16).hexdigest()
                blocks[f"section:{normalize_for_matching(section['section'])}:{fingerprint}"].append(record_id)
        for shingle in _shingles(skeleton):
            ngram_index[shingle].add(record_id)

    pairs: set[tuple[str, str]] = set()
    for block_key, members in blocks.items():
        if len(members) < 2 or (block_key.startswith("shape:") and len(members) > 500):
            continue
        pairs.update(combinations(sorted(members), 2))

    shared_ngram_counts: Counter[tuple[str, str]] = Counter()
    # ponytail: frequency cap downweights boilerplate; replace with learned IDF after validation.
    for members in ngram_index.values():
        if 2 <= len(members) <= MAX_NGRAM_BUCKET:
            shared_ngram_counts.update(combinations(sorted(members), 2))
    pairs.update(pair for pair, count in shared_ngram_counts.items() if count >= MIN_SHARED_NGRAMS)
    return pairs


def _pair_evidence(
    left_id: str,
    right_id: str,
    skeletons: dict[str, str],
    normalized_texts: dict[str, str],
    record_lookup: dict[str, ParsedRecord],
) -> _PairEvidence:
    return _PairEvidence(
        masked=_similarity(skeletons[left_id], skeletons[right_id]),
        original=_similarity(normalized_texts[left_id], normalized_texts[right_id]),
        ngram=_ngram_similarity(skeletons[left_id], skeletons[right_id]),
        high_value_section=_high_value_section_similarity(record_lookup[left_id], record_lookup[right_id]),
        weighted_section=_weighted_section_similarity(record_lookup[left_id], record_lookup[right_id]),
    )


def _is_match(evidence: _PairEvidence, similarity_threshold: float) -> bool:
    return (
        evidence.original >= MIN_ORIGINAL_SUPPORT
        and evidence.structural >= similarity_threshold
    ) or (
        evidence.original >= MIN_NGRAM_ORIGINAL_SUPPORT
        and evidence.ngram >= MIN_NGRAM_CONTAINMENT
    )


def cluster_templates(
    records: list[ParsedRecord],
    similarity_threshold: float = 0.88,
) -> list[TemplateClusterMember]:
    skeletons = {record.record_id: build_skeleton_text(record) for record in records}
    normalized_texts = {record.record_id: build_normalized_text(record) for record in records}
    content_classes = {record.record_id: _content_class(record, skeletons[record.record_id]) for record in records}
    excluded_classes = {"empty_or_unusable", "administrative_boilerplate"}
    comparable_records = [record for record in records if content_classes[record.record_id] not in excluded_classes]
    excluded_records = [record for record in records if content_classes[record.record_id] in excluded_classes]
    record_lookup = {record.record_id: record for record in comparable_records}
    record_ids = [record.record_id for record in comparable_records]
    if len(record_ids) < 2:
        return [TemplateClusterMember("", 0, record.record_id, record.source_file, cluster_severity="excluded", metadata_context=_metadata_context(record), template_pattern_type="", exclusion_reason=content_classes[record.record_id]) for record in excluded_records]
    union_find = _UnionFind.create(record_ids)
    candidate_pairs = _candidate_pairs(comparable_records, skeletons, normalized_texts)
    evidence_cache: dict[tuple[str, str], _PairEvidence] = {}

    def evidence(left_id: str, right_id: str) -> _PairEvidence:
        pair = tuple(sorted((left_id, right_id)))
        if pair not in evidence_cache:
            evidence_cache[pair] = _pair_evidence(*pair, skeletons, normalized_texts, record_lookup)
        return evidence_cache[pair]

    for left_id, right_id in candidate_pairs:
        if _is_match(evidence(left_id, right_id), similarity_threshold):
            union_find.union(left_id, right_id)

    components: dict[str, list[str]] = defaultdict(list)
    for record_id in record_ids:
        components[union_find.find(record_id)].append(record_id)

    clusters: list[TemplateClusterMember] = []
    verified_groups: list[list[str]] = []
    for members in sorted(components.values(), key=lambda values: (-len(values), values[0])):
        remaining = set(members)
        while len(remaining) >= 2:
            medoid = max(remaining, key=lambda candidate: sum(evidence(candidate, other).score for other in remaining if other != candidate))
            verified_members = sorted(member for member in remaining if member == medoid or _is_match(evidence(member, medoid), similarity_threshold))
            if len(verified_members) < 2:
                remaining.remove(medoid)
                continue
            verified_groups.append(verified_members)
            remaining.difference_update(verified_members)

    for cluster_index, verified_members in enumerate(sorted(verified_groups, key=lambda values: (-len(values), values[0])), start=1):
        cluster_id = f"TPL-{cluster_index:04d}"
        medoid = max(verified_members, key=lambda candidate: sum(evidence(candidate, other).score for other in verified_members if other != candidate))
        shared_excerpt = _shared_excerpt([skeletons[record_id] for record_id in verified_members])
        for member in verified_members:
            reference = medoid if member != medoid else max(
                (other for other in verified_members if other != member),
                key=lambda other: evidence(member, other).score,
            )
            pair_evidence = evidence(member, reference)
            section_scores = _section_similarities(record_lookup[member], record_lookup[reference])
            pattern_type = "exact_duplicate" if normalized_texts[member] == normalized_texts[reference] else "masked_near_duplicate"
            substitutions = _variable_substitutions(record_lookup[member].abstract_text, record_lookup[reference].abstract_text)
            if pattern_type != "exact_duplicate" and pair_evidence.high_value_section >= similarity_threshold and pair_evidence.high_value_section > pair_evidence.masked:
                pattern_type = "shared_section"
            elif pair_evidence.ngram >= MIN_NGRAM_CONTAINMENT and pair_evidence.structural < similarity_threshold:
                pattern_type = "reordered_or_partial_template"
            elif substitutions and pair_evidence.masked - pair_evidence.original >= 0.03:
                pattern_type = "entity_value_substitution"
            cluster_size = len(verified_members)
            clusters.append(
                TemplateClusterMember(
                    template_cluster_id=cluster_id,
                    cluster_size=cluster_size,
                    record_id=member,
                    source_file=record_lookup[member].source_file,
                    similar_record_ids=sorted(other for other in verified_members if other != member),
                    similarity_score=round(pair_evidence.score, 3),
                    cluster_severity="candidate",
                    shared_skeleton_excerpt=shared_excerpt,
                    metadata_context=_metadata_context(record_lookup[member]),
                    template_pattern_type=pattern_type,
                    original_text_similarity=round(pair_evidence.original, 3),
                    masked_skeleton_similarity=round(pair_evidence.masked, 3),
                    ngram_similarity=round(pair_evidence.ngram, 3),
                    weighted_section_similarity=round(pair_evidence.weighted_section, 3),
                    section_similarities="; ".join(f"{section}={score:.3f}" for section, score in sorted(section_scores.items())),
                    variable_substitutions=substitutions,
                )
            )

    clusters.sort(key=lambda row: (row.template_cluster_id, row.record_id))
    clusters.extend(
        TemplateClusterMember(
            template_cluster_id="",
            cluster_size=0,
            record_id=record.record_id,
            source_file=record.source_file,
            cluster_severity="excluded",
            metadata_context=_metadata_context(record),
            template_pattern_type="",
            exclusion_reason=content_classes[record.record_id],
        )
        for record in excluded_records
    )
    return clusters
