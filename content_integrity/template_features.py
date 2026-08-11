from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .entity_extraction import TypedEntity, mask_text
from .models import ParsedRecord
from .utils import normalize_for_matching


FEATURE_VERSION = "asco-template-features-v1"


@dataclass(frozen=True, slots=True)
class FeatureSection:
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
        }


def _feature_section(index: int, section: str, text: str) -> FeatureSection:
    masked, entities = mask_text(text, section)
    return FeatureSection(
        index=index,
        section=section,
        original=text,
        normalized=normalize_for_matching(text),
        masked=masked,
        entities=tuple(entities),
    )


def build_template_features(record: ParsedRecord) -> TemplateFeatures:
    """Create one stable, JSON-serializable feature object per parsed record."""
    title = _feature_section(-1, "Title", record.title)
    abstract = _feature_section(-1, "Abstract", record.abstract_text or record.raw_text)
    sections = tuple(
        _feature_section(index, item.get("section", "Abstract"), item.get("text", ""))
        for index, item in enumerate(record.abstract_sections)
    )
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
    )
