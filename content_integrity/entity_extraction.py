from __future__ import annotations

import re
import os
from dataclasses import asdict, dataclass, replace
from functools import lru_cache

from .template_matching_common import (
    DATE_PATTERNS,
    DRUG_SUFFIX_PATTERN,
    EMAIL_PATTERN,
    GENE_PATTERN,
    NUMBER_PATTERN,
    PERCENT_PATTERN,
    PVAL_PATTERN,
    TRIAL_PATTERN,
    URL_PATTERN,
)
from .utils import normalize_for_matching, normalize_whitespace


VOCABULARY_VERSION = "asco-hybrid-v1"
NER_MODEL_ENV = "ASCO_SCISPACY_MODEL"
PUBMEDBERT_MODEL_ENV = "ASCO_PUBMEDBERT_MODEL"
PUBMEDBERT_MIN_SCORE_ENV = "ASCO_PUBMEDBERT_MIN_SCORE"
NER_TYPE_MAP = {
    "GENE_OR_GENE_PRODUCT": "gene", "CANCER": "disease",
    "PATHOLOGICAL_FORMATION": "disease", "SIMPLE_CHEMICAL": "drug",
    "CELL_LINE": "cell_line", "CELL": "cell_line",
}
PUBMEDBERT_TYPE_MAP = {
    "Gene_or_gene_product": "gene", "Cancer": "disease",
    "Pathological_formation": "disease", "Simple_chemical": "drug",
}
DISEASE_RE = re.compile(r"\b(?:(?:non-small[- ]cell|small[- ]cell)\s+lung|breast|lung|colorectal|colon|rectal|prostate|ovarian|pancreatic|gastric|endometrial|cervical|renal(?: cell)?|hepatocellular|urothelial|thyroid|head and neck) (?:cancer|carcinoma)\b|\b(?:melanoma|mesothelioma|glioblastoma|multiple myeloma|hodgkin lymphoma|non-hodgkin lymphoma|acute myeloid leukemia|chronic lymphocytic leukemia)\b", re.I)
MIRNA_RE = re.compile(r"\b(?:(?:hsa|mmu)-)?(?:miR|microRNA)[ -]?\d+[a-z]?(?:-\d+[a-z]?)?\b", re.I)
LNCRNA_RE = re.compile(r"\b(?:LINC\d+|SNHG\d+|MALAT1|HOTAIR|NEAT1|XIST|[A-Z]{2,}\d+-AS\d+)\b")
PROTEIN_RE = re.compile(r"\b(?:HER2|PD-?L1|PD-?1|CTLA-?4|VEGF(?:-A)?|Ki-?67|ER|PR)\b", re.I)
EXPLICIT_GENE_RE = re.compile(r"\b(?:EGFR|ALK|KRAS|NRAS|BRAF|RET|ROS1|MET|PIK3CA|PTEN|TP53|APC)\b")
CELL_LINE_RE = re.compile(r"\b(?:B16|A549|H1299|H460|H1975|MCF-?7|MDA-MB-\d+|T47D|BT-\d+|SK-BR-\d+|HCC\d+|ZR-\d+|SUM\d+)\b", re.I)
ASSAY_RE = re.compile(r"\b(?:qRT-PCR|RT-qPCR|PCR|Western blot(?:ting)?|ELISA|flow cytometry|immunohistochemistry|IHC|CCK-8|MTT|Transwell|luciferase(?: reporter)? assay)\b", re.I)
PATHWAY_RE = re.compile(r"\b(?:PI3K/AKT(?:/mTOR)?|MAPK/ERK|Wnt/β-catenin|TGF-β|NF-κB|Notch|Hedgehog) (?:signaling )?pathway\b", re.I)
ENDPOINT_RE = re.compile(r"\b(?:overall survival|progression-free survival|disease-free survival|objective response rate|pathological complete response)\b", re.I)
BIOMARKER_RE = re.compile(r"\b(?:tumou?r mutational burden|microsatellite instability|mismatch repair deficien(?:cy|t)|circulating tumou?r DNA|minimal residual disease)\b", re.I)
CONTEXTUAL_BIOMARKER_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9.-]{2,})\s+(?:is|was|as|may be|could be|represents?|serves? as)\s+"
    r"(?:an?\s+)?(?:potential(?:ly)?\s+|valuable\s+|promising\s+)*biomarker\b",
    re.I,
)
REGISTRY_RE = re.compile(r"\b(?:ClinicalTrials\.gov|PubMed|GenBank|GEO|TCGA|SEER)\b", re.I)
POPULATION_RE = re.compile(r"\b(?:postmenopausal women|pre?menopausal women|older adults|pediatric patients|patients aged \d+(?:-\d+)? years?)\b", re.I)
TREATMENT_CLASS_RE = re.compile(r"\b(?:chemotherapy|immunotherapy|endocrine therapy|targeted therapy|radiotherapy|anti-HER2 therapy|checkpoint inhibitor(?: therapy)?)\b", re.I)
# ponytail: process-local counter; move into run context if concurrent pipelines are introduced.
_model_inference_count = 0


@dataclass(frozen=True, slots=True)
class TypedEntity:
    text: str
    normalized: str
    entity_type: str
    start: int
    end: int
    section: str
    sentence_index: int
    extraction_method: str
    vocabulary_version: str = VOCABULARY_VERSION
    confidence: str = "high"

    def to_dict(self) -> dict[str, str | int]:
        return asdict(self)


def _sentence_index(text: str, offset: int) -> int:
    return len(re.findall(r"[.!?]\s+", text[:offset]))


@lru_cache(maxsize=1)
def _ner_model():
    model_name = os.getenv(NER_MODEL_ENV, "").strip()
    if not model_name or os.getenv("ASCO_NER_DISABLED") == "1":
        return None
    try:
        import spacy
    except ImportError as exc:
        raise RuntimeError(f"Configured SciSpaCy model {model_name!r} is unavailable.") from exc
    try:
        return spacy.load(model_name)
    except Exception as exc:
        raise RuntimeError(f"Configured SciSpaCy model {model_name!r} could not be loaded.") from exc


def _ner_entities(text: str, section: str) -> list[TypedEntity]:
    model = _ner_model()
    if model is None:
        return []
    global _model_inference_count
    _model_inference_count += 1
    predictions = model(text).ents
    entities = []
    for item in predictions:
        label = NER_TYPE_MAP.get(item.label_)
        if label is None:
            continue
        start, end = item.start_char, item.end_char
        value = text[start:end]
        entities.append(TypedEntity(
            text=value,
            normalized=normalize_for_matching(value),
            entity_type=label,
            start=start,
            end=end,
            section=section,
            sentence_index=_sentence_index(text, start),
            extraction_method="scispacy",
            confidence="model",
        ))
    return entities


@lru_cache(maxsize=1)
def _pubmedbert_pipeline():
    model_name = os.getenv(PUBMEDBERT_MODEL_ENV, "").strip()
    if not model_name or os.getenv("ASCO_NER_DISABLED") == "1":
        return None
    try:
        from transformers import BertTokenizerFast, pipeline
        tokenizer = BertTokenizerFast.from_pretrained(model_name)
        tokenizer.model_max_length = 512
        return pipeline(
            "token-classification", model=model_name, tokenizer=tokenizer,
            aggregation_strategy="simple", device=-1,
        )
    except Exception as exc:
        raise RuntimeError(f"Configured PubMedBERT model {model_name!r} could not be loaded.") from exc


def _pubmedbert_entities(text: str, section: str) -> list[TypedEntity]:
    model = _pubmedbert_pipeline()
    if model is None:
        return []
    minimum_score = float(os.getenv(PUBMEDBERT_MIN_SCORE_ENV, "0.8"))
    global _model_inference_count
    _model_inference_count += 1
    entities = []
    for item in model(text, stride=64):
        label = PUBMEDBERT_TYPE_MAP.get(item["entity_group"])
        score = float(item["score"])
        if label is None or score < minimum_score:
            continue
        start, end = int(item["start"]), int(item["end"])
        value = text[start:end]
        entities.append(TypedEntity(
            text=value,
            normalized=normalize_for_matching(value),
            entity_type=label,
            start=start,
            end=end,
            section=section,
            sentence_index=_sentence_index(text, start),
            extraction_method="pubmedbert",
            confidence=f"{score:.3f}",
        ))
    return entities


def extract_rule_entities(text: str, section: str = "Abstract") -> list[TypedEntity]:
    patterns = [
        ("url", URL_PATTERN, "rule"),
        ("email", EMAIL_PATTERN, "rule"),
        ("trial_id", TRIAL_PATTERN, "rule"),
        ("mirna", MIRNA_RE, "hybrid_context"),
        ("lncrna", LNCRNA_RE, "hybrid_context"),
        ("protein", PROTEIN_RE, "hybrid_context"),
        ("gene", EXPLICIT_GENE_RE, "rule"),
        ("cell_line", CELL_LINE_RE, "hybrid_context"),
        ("assay", ASSAY_RE, "hybrid_context"),
        ("pathway", PATHWAY_RE, "hybrid_context"),
        ("endpoint", ENDPOINT_RE, "hybrid_context"),
        ("biomarker", CONTEXTUAL_BIOMARKER_RE, "hybrid_context"),
        ("biomarker", BIOMARKER_RE, "hybrid_context"),
        ("registry", REGISTRY_RE, "hybrid_context"),
        ("population", POPULATION_RE, "hybrid_context"),
        ("treatment_class", TREATMENT_CLASS_RE, "hybrid_context"),
        ("disease", DISEASE_RE, "rule"),
        ("drug", DRUG_SUFFIX_PATTERN, "rule"),
        ("gene", GENE_PATTERN, "rule"),
        ("date", re.compile("|".join(pattern.pattern for pattern in DATE_PATTERNS), re.I), "rule"),
        ("pvalue", PVAL_PATTERN, "rule"),
        ("percent", PERCENT_PATTERN, "rule"),
        ("number", NUMBER_PATTERN, "rule"),
    ]
    candidates = []
    for entity_type, pattern, method in patterns:
        for match in pattern.finditer(text):
            value = match.group(1) if pattern is CONTEXTUAL_BIOMARKER_RE else match.group(0)
            value = value.rstrip("-") if entity_type == "gene" else value
            start = match.start(1) if pattern is CONTEXTUAL_BIOMARKER_RE else match.start()
            candidates.append((start, start + len(value), entity_type, method, value))
    entities: list[TypedEntity] = []
    occupied: list[tuple[int, int]] = []
    for start, end, entity_type, method, value in sorted(candidates, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(start < existing_end and end > existing_start for existing_start, existing_end in occupied):
            continue
        occupied.append((start, end))
        entities.append(TypedEntity(
            text=value,
            normalized=normalize_for_matching(value),
            entity_type=entity_type,
            start=start,
            end=end,
            section=section,
            sentence_index=_sentence_index(text, start),
            extraction_method=method,
        ))
    return entities


def _merge_model_entities(
    deterministic: list[TypedEntity], model_entities: list[TypedEntity], *, pubmedbert: bool,
) -> list[TypedEntity]:
    if not model_entities:
        return deterministic
    if pubmedbert:
        occupied = [(entity.start, entity.end) for entity in deterministic]
        return sorted([
            *deterministic,
            *(entity for entity in model_entities if not any(
                entity.start < end and entity.end > start for start, end in occupied
            )),
        ], key=lambda entity: entity.start)
    always_keep = {"url", "email", "trial_id", "date", "pvalue", "percent", "number"}
    ner_spans = [(entity.start, entity.end) for entity in model_entities]
    candidates = [entity for entity in deterministic if entity.entity_type in always_keep or not any(
        entity.start < end and entity.end > start for start, end in ner_spans
    )]
    candidates.extend(model_entities)
    selected: list[TypedEntity] = []
    occupied = []
    for entity in sorted(candidates, key=lambda item: (item.start, -(item.end - item.start), item.extraction_method != "rule")):
        if any(entity.start < end and entity.end > start for start, end in occupied):
            continue
        occupied.append((entity.start, entity.end))
        selected.append(entity)
    return selected


def extract_typed_entities(text: str, section: str = "Abstract") -> list[TypedEntity]:
    """Extract deterministic entities and optionally fill biomedical gaps with NER."""
    deterministic = extract_rule_entities(text, section)
    pubmedbert = bool(os.getenv(PUBMEDBERT_MODEL_ENV, "").strip())
    model_entities = _pubmedbert_entities(text, section) if pubmedbert else _ner_entities(text, section)
    return _merge_model_entities(deterministic, model_entities, pubmedbert=pubmedbert)


def project_entities(
    entities: list[TypedEntity], text: str, start: int, end: int, section: str,
) -> list[TypedEntity]:
    return [
        replace(
            entity,
            text=text[entity.start - start:entity.end - start],
            start=entity.start - start,
            end=entity.end - start,
            section=section,
            sentence_index=_sentence_index(text, entity.start - start),
        )
        for entity in entities
        if start <= entity.start and entity.end <= end
    ]


def extract_record_entities(
    title: str, abstract: str, *, use_model: bool = True,
) -> tuple[list[TypedEntity], list[TypedEntity]]:
    """Extract title/abstract entities with at most one biomedical-model call."""
    title_rules = extract_rule_entities(title, "Title")
    abstract_rules = extract_rule_entities(abstract, "Abstract")
    if not title and not abstract:
        return title_rules, abstract_rules
    separator = "\n\n" if title and abstract else ""
    combined = f"{title}{separator}{abstract}"
    abstract_start = len(title) + len(separator)
    if not use_model:
        return title_rules, abstract_rules
    pubmedbert = bool(os.getenv(PUBMEDBERT_MODEL_ENV, "").strip())
    model_entities = (
        _pubmedbert_entities(combined, "Record")
        if pubmedbert
        else _ner_entities(combined, "Record")
    )
    title_model = project_entities(model_entities, title, 0, len(title), "Title")
    abstract_model = project_entities(
        model_entities, abstract, abstract_start, abstract_start + len(abstract), "Abstract"
    )
    return (
        _merge_model_entities(title_rules, title_model, pubmedbert=pubmedbert),
        _merge_model_entities(abstract_rules, abstract_model, pubmedbert=pubmedbert),
    )


def model_inference_count() -> int:
    return _model_inference_count


def reset_model_inference_count() -> None:
    global _model_inference_count
    _model_inference_count = 0


def mask_entities(text: str, entities: list[TypedEntity] | tuple[TypedEntity, ...]) -> str:
    """Mask text from already-extracted local spans."""
    parts: list[str] = []
    cursor = 0
    for entity in entities:
        parts.append(text[cursor:entity.start])
        parts.append(f"<{entity.entity_type.upper()}>")
        cursor = entity.end
    parts.append(text[cursor:])
    return normalize_whitespace("".join(parts))


def mask_text(text: str, section: str = "Abstract") -> tuple[str, list[TypedEntity]]:
    entities = extract_typed_entities(text, section)
    return mask_entities(text, entities), entities


def validate_masking(text: str, masked_text: str, entities: list[TypedEntity]) -> list[str]:
    errors: list[str] = []
    previous_end = 0
    for entity in entities:
        if text[entity.start:entity.end] != entity.text:
            errors.append(f"invalid_span:{entity.text}")
        if entity.start < previous_end:
            errors.append(f"overlap:{entity.text}")
        previous_end = entity.end
    expected = mask_entities(text, entities)
    if expected != masked_text:
        errors.append("mask_reconstruction_mismatch")
    return errors
