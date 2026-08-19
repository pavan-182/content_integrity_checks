from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from hashlib import blake2b
from itertools import combinations

from .models import ParsedRecord
from .thresholds import (
    TEMPLATE_MATCHING_COMMON_MAX_APPROXIMATE_BUCKET as MAX_APPROXIMATE_BUCKET,
    TEMPLATE_MATCHING_COMMON_MAX_NGRAM_BUCKET as MAX_NGRAM_BUCKET,
    TEMPLATE_MATCHING_COMMON_MIN_SHARED_NGRAMS as MIN_SHARED_NGRAMS,
    TEMPLATE_MATCHING_COMMON_NGRAM_SIZE as NGRAM_SIZE,
    TEMPLATE_MATCHING_COMMON_SECTION_WEIGHTS as SECTION_WEIGHTS,
)
from .utils import normalize_for_matching, normalize_whitespace, split_sentences, text_tokens


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
# Single source of truth for supported trial-registry prefixes; unverifiable_trial.py builds its
# own registry-specific regexes from this instead of re-listing the prefixes independently.
SUPPORTED_REGISTRY_PREFIXES = ("NCT", "ISRCTN", "ACTRN", "EUCTR", "EudraCT", "ChiCTR")
TRIAL_PATTERN = re.compile(
    r"\b(?:" + "|".join(SUPPORTED_REGISTRY_PREFIXES) + r")[A-Za-z0-9\-_.]+\b", re.IGNORECASE
)
GENE_PATTERN = re.compile(r"\b(?:[A-Z]{2,}\d+[A-Z0-9\-]*|[A-Z]{2,}-\d+[A-Z0-9\-]*)\b")
DRUG_SUFFIX_PATTERN = re.compile(
    r"\b[A-Za-z]{4,}(?:mab|nib|tinib|cept|vir|statin|caftor|parib|azole|cillin|ximab|zumab|ib)\b",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[\w.\-+]+@[\w.\-]+\.\w+\b")
PLACEHOLDER_TOKEN_RE = re.compile(
    r"<(URL|EMAIL|DATE|TRIAL_ID|PVAL|PCT|GENE|PROTEIN|MIRNA|LNCRNA|DRUG|DISEASE|"
    r"BIOMARKER|PATHWAY|CELL_LINE|ASSAY|ENDPOINT|REGISTRY|POPULATION|TREATMENT_CLASS|NUM)>"
)
PLACEHOLDER_TOKENS = {
    "url", "email", "date", "trial", "id", "pval", "pct", "gene", "drug",
    "protein", "mirna", "lncrna", "disease", "biomarker", "pathway", "cell",
    "line", "assay", "endpoint", "registry", "population", "treatment", "class", "num",
}
BOILERPLATE_RE = re.compile(r"\b(?:n a n a|not applicable|none|n a)\b")


def _entity_shape_key(skeleton: str, bucket_size: int = 5) -> str:
    counts = Counter(PLACEHOLDER_TOKEN_RE.findall(skeleton))
    bucketed = sorted(f"{token}{count // bucket_size}" for token, count in counts.items())
    return ":".join(bucketed) if bucketed else "NOPLACEHOLDER"


def _sentence_split(text: str) -> list[str]:
    return split_sentences(text)


def _mask_variables(text: str) -> str:
    masked = normalize_whitespace(text)
    masked = TRIAL_PATTERN.sub("<TRIAL_ID>", masked)
    masked = GENE_PATTERN.sub("<GENE>", masked)
    masked = masked.lower().replace("<trial_id>", "<TRIAL_ID>").replace("<gene>", "<GENE>")
    masked = URL_PATTERN.sub("<URL>", masked)
    masked = EMAIL_PATTERN.sub("<EMAIL>", masked)
    for pattern in DATE_PATTERNS:
        masked = pattern.sub("<DATE>", masked)
    masked = PVAL_PATTERN.sub("<PVAL>", masked)
    masked = PERCENT_PATTERN.sub("<PCT>", masked)
    masked = DRUG_SUFFIX_PATTERN.sub("<DRUG>", masked)
    return re.sub(r"\s+", " ", NUMBER_PATTERN.sub("<NUM>", masked)).strip()


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


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens, right_tokens = text_tokens(left), text_tokens(right)
    forward = SequenceMatcher(None, left_tokens, right_tokens, autojunk=False).ratio()
    reverse = SequenceMatcher(None, right_tokens, left_tokens, autojunk=False).ratio()
    return (forward + reverse) / 2


def _shared_excerpt(skeletons: list[str]) -> str:
    sentence_counter: Counter[str] = Counter()
    for skeleton in skeletons:
        sentence_counter.update(_sentence_split(skeleton))
    repeated = [sentence for sentence, count in sentence_counter.items() if count >= 2]
    if repeated:
        repeated.sort(key=lambda value: (len(value), sentence_counter[value]), reverse=True)
        return repeated[0][:240]
    return skeletons[0][:240] if skeletons else ""


def _candidate_pairs(
    records: list[ParsedRecord],
    skeletons: dict[str, str],
    normalized_texts: dict[str, str],
    section_skeletons: dict[str, dict[str, str]] | None = None,
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
        sections = (
            section_skeletons.get(record_id, {}).items()
            if section_skeletons is not None
            else ((section["section"], _mask_variables(section["text"])) for section in record.abstract_sections)
        )
        for section, section_text in sections:
            if len(text_tokens(section_text)) >= 15:
                fingerprint = blake2b(section_text.encode(), digest_size=16).hexdigest()
                blocks[f"section:{normalize_for_matching(section)}:{fingerprint}"].append(record_id)
        for shingle in _shingles(skeleton):
            ngram_index[shingle].add(record_id)

    pairs = {
        pair
        for block_key, members in blocks.items()
        if len(members) >= 2 and not (
            block_key.startswith(("shape:", "prefix:"))
            and len(members) > MAX_APPROXIMATE_BUCKET
        )
        for pair in combinations(sorted(members), 2)
    }
    shared_ngram_counts: Counter[tuple[str, str]] = Counter()
    # ponytail: frequency cap downweights boilerplate; replace with learned IDF after validation.
    for members in ngram_index.values():
        if 2 <= len(members) <= MAX_NGRAM_BUCKET:
            shared_ngram_counts.update(combinations(sorted(members), 2))
    pairs.update(pair for pair, count in shared_ngram_counts.items() if count >= MIN_SHARED_NGRAMS)
    return pairs
