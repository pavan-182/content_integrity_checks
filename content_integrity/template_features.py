from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from .entity_extraction import (
    TypedEntity,
    extract_record_entities,
    extract_rule_entities,
    mask_entities,
    project_entities,
)
from .models import ParsedRecord
from .utils import normalize_for_matching


FEATURE_VERSION = "asco-template-features-v2"


@dataclass(frozen=True, slots=True)
class FeatureSentence:
    index: int
    section: str
    original: str
    normalized: str
    masked: str
    entities: tuple[TypedEntity, ...]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["entities"] = [entity.to_dict() for entity in self.entities]
        return data


@dataclass(frozen=True, slots=True)
class FeatureSection:
    index: int
    section: str
    original: str
    normalized: str
    masked: str
    entities: tuple[TypedEntity, ...]
    sentences: tuple[FeatureSentence, ...]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["entities"] = [entity.to_dict() for entity in self.entities]
        data["sentences"] = [sentence.to_dict() for sentence in self.sentences]
        return data


@dataclass(frozen=True, slots=True)
class TemplateFeatures:
    feature_version: str
    record_id: str
    source_file: str
    source_hash: str
    title: FeatureSection
    abstract: FeatureSection
    sections: tuple[FeatureSection, ...]
    structured_abstract: bool
    trial_ids: tuple[str, ...]
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_version": self.feature_version,
            "record_id": self.record_id,
            "source_file": self.source_file,
            "source_hash": self.source_hash,
            "title": self.title.to_dict(),
            "abstract": self.abstract.to_dict(),
            "sections": [section.to_dict() for section in self.sections],
            "structured_abstract": self.structured_abstract,
            "trial_ids": list(self.trial_ids),
            "metadata": self.metadata,
        }


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?])\s+", text):
        end = boundary.start()
        if text[start:end].strip():
            spans.append((start, end))
        start = boundary.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _feature_section(
    index: int, section: str, text: str, entities: list[TypedEntity],
) -> FeatureSection:
    sentences = []
    for sentence_index, (start, end) in enumerate(_sentence_spans(text)):
        original = text[start:end]
        sentence_entities = project_entities(entities, original, start, end, section)
        sentences.append(FeatureSentence(
            index=sentence_index,
            section=section,
            original=original,
            normalized=normalize_for_matching(original),
            masked=mask_entities(original, sentence_entities),
            entities=tuple(sentence_entities),
        ))
    return FeatureSection(
        index=index,
        section=section,
        original=text,
        normalized=normalize_for_matching(text),
        masked=mask_entities(text, entities),
        entities=tuple(entities),
        sentences=tuple(sentences),
    )


def build_template_features(record: ParsedRecord, *, use_model: bool = True) -> TemplateFeatures:
    """Create the shared template representation with one model inference per record."""
    abstract_text = record.abstract_text or record.title or record.raw_text
    title_entities, abstract_entities = extract_record_entities(
        record.title, abstract_text, use_model=use_model,
    )
    title = _feature_section(-1, "Title", record.title, title_entities)
    abstract = _feature_section(-1, "Abstract", abstract_text, abstract_entities)
    sections = []
    cursor = 0
    for index, item in enumerate(record.abstract_sections):
        label, text = item.get("section", "Abstract"), item.get("text", "")
        start = abstract_text.find(text, cursor)
        if start >= 0:
            entities = project_entities(abstract_entities, text, start, start + len(text), label)
            cursor = start + len(text)
        else:
            # Sections outside the canonical abstract retain deterministic extraction
            # without causing another biomedical-model inference.
            entities = extract_rule_entities(text, label)
        sections.append(_feature_section(index, label, text, entities))
    sections = tuple(sections)
    source = json.dumps({
        "record_id": record.record_id,
        "title": title.original,
        "abstract": abstract.original,
        "sections": [(section.section, section.original) for section in sections],
    }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return TemplateFeatures(
        feature_version=FEATURE_VERSION,
        record_id=record.record_id,
        source_file=record.source_file,
        source_hash=hashlib.sha256(source.encode()).hexdigest(),
        title=title,
        abstract=abstract,
        sections=sections,
        structured_abstract=record.structured_abstract,
        trial_ids=tuple(record.trial_ids),
        metadata={
            "doi": record.doi,
            "authors": tuple(record.authors),
            "affiliations": tuple(record.affiliations),
            "journal": record.journal,
            "article_type": record.article_type,
            "publication_year": record.publication_year,
        },
    )
