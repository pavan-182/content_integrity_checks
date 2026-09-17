from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any
from typing import TYPE_CHECKING

import pysbd

if TYPE_CHECKING:
    from .models import ParsedRecord

WHITESPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[0-9A-Za-z]+")


def normalize_whitespace(text: str | None) -> str:
    if not text:
        return ""
    return WHITESPACE_RE.sub(" ", str(text)).strip()


def lowercase_normalize(text: str | None) -> str:
    return normalize_whitespace(text).lower()


def split_sentences(text: str) -> list[str]:
    return list(_split_sentences(normalize_whitespace(text)))


# Segmentation is a pure function of the text and dominated template preprocessing when repeated
# per entity and per detector, so results are memoized (bounded; thread-safe for correctness).
# The bound must hold one batch's section texts: 3,000 synthetic records needed 14,394 entries,
# and an 8,192-entry cache made each per-record detector re-segment most of them (design checks
# took 83 s instead of 17 s). 65,536 entries covers 6,000 records for about 10 MB.
SENTENCE_CACHE_SIZE = 65536


@lru_cache(maxsize=SENTENCE_CACHE_SIZE)
def _split_sentences(normalized: str) -> tuple[str, ...]:
    # PySBD stores original_text on the instance, so a shared segmenter races across runs.
    segmenter = pysbd.Segmenter(language="en", clean=False)
    return tuple(piece.strip() for piece in segmenter.segment(normalized) if piece.strip())


@lru_cache(maxsize=SENTENCE_CACHE_SIZE)
def sentence_starts(text: str) -> tuple[int, ...]:
    """Start offsets of `split_sentences(text)` pieces within `normalize_whitespace(text)`."""
    normalized = normalize_whitespace(text)
    starts: list[int] = []
    cursor = 0
    for sentence in _split_sentences(normalized):
        # PySBD with clean=False preserves characters, so every piece is found in order.
        cursor = max(normalized.find(sentence, cursor), cursor)
        starts.append(cursor)
        cursor += len(sentence)
    return tuple(starts)


def sentence_index_at(text: str, offset: int) -> int:
    """0-based index into `split_sentences(text)` of the sentence containing `text[offset]`."""
    position = len(WHITESPACE_RE.sub(" ", text[:offset]).lstrip())
    return max(bisect_right(sentence_starts(text), position) - 1, 0)


def text_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    return TOKEN_RE.findall(str(text).lower())


def join_nonempty(values: Iterable[Any], delimiter: str = " | ") -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = normalize_whitespace(value)
        else:
            cleaned = normalize_whitespace(str(value))
        if cleaned:
            parts.append(cleaned)
    return delimiter.join(parts)


def first_nonempty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = normalize_whitespace(value)
        else:
            cleaned = normalize_whitespace(str(value))
        if cleaned:
            return cleaned
    return ""


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = normalize_whitespace(value)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def to_pipe_string(values: Sequence[str] | None) -> str:
    if not values:
        return ""
    return " | ".join(v for v in values if normalize_whitespace(v))


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        cleaned = normalize_whitespace(str(value)).replace(",", "")
        if not cleaned:
            return None
        return int(float(cleaned))
    except Exception:
        return None


def year_from_date_text(value: str | None) -> str:
    if not value:
        return ""
    m = re.search(r"(19|20)\d{2}", value)
    return m.group(0) if m else ""


def path_stem(path: str | Path) -> str:
    return Path(path).stem


def evidence_snippet(text: str, start: int, end: int, window: int = 80) -> str:
    if not text:
        return ""
    start_idx = max(0, start - window)
    end_idx = min(len(text), end + window)
    snippet = text[start_idx:end_idx]
    return normalize_whitespace(snippet)


def section_for_match(record: "ParsedRecord", field_name: str, matched_text: str) -> str:
    if field_name == "title":
        return "Title"
    needle = lowercase_normalize(matched_text)
    for section in record.abstract_sections:
        if needle and needle in lowercase_normalize(section.get("text", "")):
            return normalize_label(section.get("section", "")) or "Abstract"
    return "Abstract"


def strip_outer_quotes(value: str) -> str:
    cleaned = normalize_whitespace(value)
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        return cleaned[1:-1].strip()
    return cleaned


def normalize_for_matching(text: str | None) -> str:
    if not text:
        return ""
    text = str(text).lower()
    text = text.replace("\u2010", "-").replace("\u2011", "-").replace("\u2012", "-")
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return normalize_whitespace(text)


def normalize_label(label: str) -> str:
    cleaned = normalize_whitespace(label).rstrip(":-. ").title()
    cleaned = cleaned.replace(" And ", " and ")
    cleaned = cleaned.replace(" Of ", " of ")
    cleaned = cleaned.replace(" In ", " in ")
    cleaned = cleaned.replace(" On ", " on ")
    cleaned = cleaned.replace(" For ", " for ")
    cleaned = cleaned.replace(" With ", " with ")
    cleaned = cleaned.replace(" To ", " to ")
    return cleaned


def ensure_parent_dir(path: str | Path) -> Path:
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def dedupe_records(
    records: list["ParsedRecord"],
) -> tuple[list["ParsedRecord"], list[dict[str, str]]]:
    grouped: dict[str, list[ParsedRecord]] = {}
    for record in records:
        grouped.setdefault(record.record_id, []).append(record)

    deduped: list[ParsedRecord] = []
    warnings: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for record_id, members in grouped.items():
        source_files = {member.source_file for member in members}
        if len(members) == 1:
            deduped.extend(members)
            used_ids.add(record_id)
            continue
        content_signatures = {(member.title, member.abstract_text, member.raw_text) for member in members}
        if len(source_files) == 1 and len(content_signatures) == 1:
            deduped.append(members[0])
            used_ids.add(record_id)
            for dropped in members[1:]:
                warnings.append({
                    "record_id": record_id,
                    "source_file": dropped.source_file,
                    "reason": "ingestion_duplicate",
                    "action": "dropped_duplicate_file",
                })
            continue
        for index, member in enumerate(members):
            if index:
                base_id = f"{record_id}__{path_stem(member.source_file)}"
                unique_id = base_id
                suffix = 2
                while unique_id in used_ids:
                    unique_id = f"{base_id}__{suffix}"
                    suffix += 1
                member.record_id = unique_id
                warnings.append({
                    "record_id": record_id,
                    "source_file": member.source_file,
                    "reason": "record_id_collision_disambiguated",
                    "action": f"rewrote_record_id_to_{unique_id}",
                })
            used_ids.add(member.record_id)
            deduped.append(member)
    assert len({record.record_id for record in deduped}) == len(deduped)
    return deduped, warnings
