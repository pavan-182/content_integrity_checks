from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path

from .candidate_routes import generate_candidate_pairs
from .entity_extraction import extract_typed_entities
from .models import ParsedRecord
from .thresholds import SIGNAL_VALIDATION_MAX_SIGNATURE_BUCKET as MAX_SIGNATURE_BUCKET
from .utils import normalize_for_matching, normalize_whitespace, split_sentences


VALIDATION_VERSION = "asco-signal-validation-v1"
RELATION_RE = re.compile(r"\b(regulates?|targets?|activates?|inhibits?|sponges?)\b", re.IGNORECASE)
MOLECULAR_TYPES = {"lncrna", "mirna", "gene", "protein", "pathway", "biomarker"}
POSITIVE_VERDICTS = {
    "Confirmed template reuse", "Probable template reuse", "Probable related/derivative reuse",
    "Probable template reuse / related duplicate",
}
NEGATIVE_VERDICTS = {
    "No suspicious template reuse", "Confirmed negative control", "Generic genre-template similarity only",
    "Not supported as template reuse", "Legitimate topic continuity; no suspicious template reuse", "Generic title formula only",
}
MANUAL_VERDICTS = {"Borderline / needs full-text review"}


def _pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left.removeprefix("rw"), right.removeprefix("rw"))))


def load_gold_labels(path: Path) -> tuple[dict[tuple[str, str], bool], set[tuple[str, str]]]:
    labels, manual = {}, set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            pair = _pair(row["abstract_id_a"].strip(), row["abstract_id_b"].strip())
            verdict = row["reviewed_verdict"].strip()
            if verdict in MANUAL_VERDICTS or row.get("recommended_pair_class", "").strip() == "Needs manual review":
                manual.add(pair)
            elif verdict in POSITIVE_VERDICTS:
                labels[pair] = True
            elif verdict in NEGATIVE_VERDICTS:
                labels[pair] = False
            else:
                labels[pair] = row.get("pair_class", "").strip() == "Possible template reuse"
    return labels, manual


@dataclass(frozen=True, slots=True)
class SignatureEvidence:
    signal: str
    record_id: str
    signature: str
    section: str
    source_sentence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _sentences(text: str) -> list[str]:
    return split_sentences(text)


def molecular_axis_signatures(record: ParsedRecord) -> list[SignatureEvidence]:
    output = []
    for section in record.abstract_sections or [{"section": "Abstract", "text": record.abstract_text}]:
        for sentence in _sentences(section.get("text", "")):
            entities = [entity for entity in extract_typed_entities(sentence, section.get("section", "Abstract")) if entity.entity_type in MOLECULAR_TYPES]
            triples = []
            for relation in RELATION_RE.finditer(sentence):
                left = [entity for entity in entities if entity.end <= relation.start()]
                right = [entity for entity in entities if entity.start >= relation.end()]
                if left and right:
                    triples.append(f"{left[-1].entity_type.upper()} -> {relation.group(1).upper().rstrip('S')} -> {right[0].entity_type.upper()}")
            if not triples:
                continue
            signature = " | ".join(triples)
            output.append(SignatureEvidence("molecular_axis", record.record_id, signature, section.get("section", "Abstract"), sentence))
    return output


def assay_workflow_signatures(record: ParsedRecord) -> list[SignatureEvidence]:
    sections = record.abstract_sections or [{"section": "Abstract", "text": record.abstract_text}]
    has_assay_sections = any(any(name in normalize_for_matching(section.get("section", "")) for name in ("method", "result")) for section in sections)
    output = []
    for section in sections:
        label, text = section.get("section", "Abstract"), section.get("text", "")
        if has_assay_sections and not any(name in normalize_for_matching(label) for name in ("method", "result")):
            continue
        assays = [entity.normalized for entity in extract_typed_entities(text, label) if entity.entity_type == "assay"]
        ordered = list(dict.fromkeys(assays))
        if len(ordered) >= 2:
            output.append(SignatureEvidence("assay_workflow", record.record_id, " > ".join(ordered), label, normalize_whitespace(text)[:240]))
    return output


def endpoint_bundle_signatures(record: ParsedRecord) -> list[SignatureEvidence]:
    sections = record.abstract_sections or [{"section": "Abstract", "text": record.abstract_text}]
    endpoints = []
    for section in sections:
        endpoints.extend(entity.normalized for entity in extract_typed_entities(section.get("text", ""), section.get("section", "Abstract")) if entity.entity_type == "endpoint")
    unique = sorted(set(endpoints))
    if len(unique) < 2:
        return []
    return [SignatureEvidence("endpoint_bundle", record.record_id, " | ".join(unique), "Abstract", (record.abstract_text or "")[:240])]


def _signal_pairs(evidence: list[SignatureEvidence]) -> tuple[set[tuple[str, str]], dict[str, list[SignatureEvidence]]]:
    buckets: dict[str, list[SignatureEvidence]] = defaultdict(list)
    for item in evidence:
        buckets[item.signature].append(item)
    pairs = {
        pair
        for members in buckets.values()
        if 2 <= len({item.record_id for item in members}) <= MAX_SIGNATURE_BUCKET
        for pair in combinations(sorted({item.record_id for item in members}), 2)
    }
    return pairs, buckets


def _recommend(incremental_positive: int, incremental_negative: int) -> str:
    if incremental_positive and not incremental_negative:
        return "consider_candidate_route"
    return "reject_for_topic_noise" if incremental_negative > incremental_positive else "keep_disabled"


@dataclass(frozen=True, slots=True)
class SignalValidationResult:
    validation_version: str
    signal: str
    signature_count: int
    candidate_pair_count: int
    max_bucket_size: int
    gold_positive_retrieved: int
    gold_negative_retrieved: int
    incremental_positive: int
    incremental_negative: int
    unlabelled_candidate_count: int
    recommendation: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_signals(
    records: list[ParsedRecord], labels: dict[tuple[str, str], bool], manual_pairs: set[tuple[str, str]] | None = None,
) -> tuple[list[SignalValidationResult], list[dict[str, object]]]:
    manual_pairs = manual_pairs or set()
    baseline = {_pair(item.left_record_id, item.right_record_id) for item in generate_candidate_pairs(records)}
    extractors = (molecular_axis_signatures, assay_workflow_signatures, endpoint_bundle_signatures)
    results, examples = [], []
    for extractor in extractors:
        evidence = [item for record in records for item in extractor(record)]
        pairs, buckets = _signal_pairs(evidence)
        positives = {pair for pair in pairs if labels.get(pair) is True}
        negatives = {pair for pair in pairs if labels.get(pair) is False}
        incremental = pairs - baseline
        result = SignalValidationResult(
            validation_version=VALIDATION_VERSION,
            signal=evidence[0].signal if evidence else extractor.__name__.removesuffix("_signatures"),
            signature_count=len(buckets),
            candidate_pair_count=len(pairs),
            max_bucket_size=max((len({item.record_id for item in members}) for members in buckets.values()), default=0),
            gold_positive_retrieved=len(positives),
            gold_negative_retrieved=len(negatives),
            incremental_positive=len({pair for pair in incremental if labels.get(pair) is True}),
            incremental_negative=len({pair for pair in incremental if labels.get(pair) is False}),
            unlabelled_candidate_count=len(pairs - set(labels) - manual_pairs),
            recommendation=_recommend(
                len({pair for pair in incremental if labels.get(pair) is True}),
                len({pair for pair in incremental if labels.get(pair) is False}),
            ),
        )
        results.append(result)
        for signature, members in sorted(buckets.items()):
            bucket_size = len({item.record_id for item in members})
            for item in members[:3]:
                examples.append({**item.to_dict(), "bucket_size": bucket_size})
    return results, examples
