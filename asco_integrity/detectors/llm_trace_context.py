from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import ParseWarning, ParsedRecord, TraceTextBlock
from ..rules import LLMTraceRule


@dataclass(frozen=True, slots=True)
class TraceContext:
    containing_sentence: str
    previous_sentence: str
    next_sentence: str
    containing_paragraph: str
    quotation_context: str


@dataclass(slots=True)
class TraceCandidate:
    record_id: str
    source_file: str
    field_name: str
    section_label: str
    block_type: str
    block_index: int
    source_text: str
    source_start: int
    source_end: int
    matched_text: str
    context: TraceContext
    match_type: str
    mapped_rule_id: str
    category: str
    severity: str
    confidence: float
    reason: str = ""
    rule: LLMTraceRule | None = None
    discovery_model_id: str = ""
    discovery_prompt_version: str = ""
    discovery_batch_id: str = ""
    validation_status: str = ""
    validation_reason: str = ""
    validated_by: str = ""
    review_status: str = ""

    @property
    def priority_eligible(self) -> bool:
        return self.rule.priority_eligible if self.rule else True

    @property
    def signal_level(self) -> str:
        return self.rule.signal_level if self.rule else "contextual"


def trace_blocks_for_record(record: ParsedRecord) -> list[TraceTextBlock]:
    if record.trace_text_blocks:
        return record.trace_text_blocks
    blocks: list[TraceTextBlock] = []
    if record.title:
        blocks.append(TraceTextBlock("title", "Title", "title", 0, record.title, record.title))
    for index, section in enumerate(record.abstract_sections):
        text = section.get("text", "")
        if text:
            label = section.get("section", "") or "Abstract"
            blocks.append(TraceTextBlock("abstract", label, "fallback_abstract", index, text, text))
    if not any(block.field_name == "abstract" for block in blocks) and record.abstract_text:
        blocks.append(
            TraceTextBlock("abstract", "Abstract", "fallback_abstract", 0, record.abstract_text, record.abstract_text)
        )
    record.trace_text_blocks = blocks
    record.trace_preprocessing_fallback = True
    if not any(warning.warning_code == "llm_trace_preprocessing_fallback" for warning in record.parse_warnings):
        record.parse_warnings.append(
            ParseWarning(
                warning_code="llm_trace_preprocessing_fallback",
                warning_message="Lossless XML trace blocks were unavailable; best available parsed text was used.",
                field_name="abstract_text",
            )
        )
        if record.parse_status == "parsed":
            record.parse_status = "parsed_with_warnings"
    return blocks


def locate_unique_source_span(
    record: ParsedRecord,
    section_or_field: str,
    matched_text: str,
) -> tuple[TraceTextBlock, int, int]:
    requested = section_or_field.strip()
    blocks = trace_blocks_for_record(record)
    if requested.casefold() == "title":
        eligible = [block for block in blocks if block.field_name == "title"]
    else:
        eligible = [
            block
            for block in blocks
            if block.field_name != "title" and block.section_label.casefold() == requested.casefold()
        ]
    if not eligible:
        raise ValueError(f"Unknown source section {requested!r} for {record.record_id}")
    matches: list[tuple[TraceTextBlock, int, int]] = []
    for block in eligible:
        for match in re.finditer(re.escape(matched_text), block.source_text, re.IGNORECASE | re.UNICODE):
            matches.append((block, match.start(), match.end()))
    if len(matches) != 1:
        detail = "not found" if not matches else "ambiguous"
        raise ValueError(f"Matched text was {detail} in section {requested!r} for {record.record_id}")
    return matches[0]


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for boundary in re.finditer(r"[.!?](?:[\"'”’])?(?=\s+|$)", text):
        end = boundary.end()
        if text[start:end].strip():
            spans.append((start, end))
        following = re.match(r"\s+", text[end:])
        start = end + (following.end() if following else 0)
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _quotation_context(text: str, start: int, end: int) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    if text[line_start:start].lstrip().startswith(">") or text[line_start:end].lstrip().startswith(">"):
        return "blockquote"
    if text[:start].count("```") % 2 == 1:
        return "code_fence"
    before = text[:start]
    if before.count('"') % 2 == 1 or before.count("“") > before.count("”"):
        return "quoted"
    single_quotes = [
        index
        for index, character in enumerate(before)
        if character in {"'", "‘", "’"}
        and not (
            index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        )
    ]
    if len(single_quotes) % 2 == 1:
        return "quoted"
    return "not_quoted"


def context_for_span(text: str, start: int, end: int) -> TraceContext:
    spans = _sentence_spans(text)
    containing_index = next(
        (index for index, (sentence_start, sentence_end) in enumerate(spans) if sentence_start <= start < sentence_end),
        None,
    )
    if containing_index is None:
        containing = text
        previous = next_sentence = ""
    else:
        sentence_start, sentence_end = spans[containing_index]
        containing = text[sentence_start:sentence_end].strip()
        previous = (
            text[spans[containing_index - 1][0] : spans[containing_index - 1][1]].strip()
            if containing_index
            else ""
        )
        next_sentence = (
            text[spans[containing_index + 1][0] : spans[containing_index + 1][1]].strip()
            if containing_index + 1 < len(spans)
            else ""
        )
    if "\n\n" in containing or not re.search(r"[.!?](?:[\"'”’])?$", containing):
        containing = text
    return TraceContext(
        containing_sentence=containing,
        previous_sentence=previous,
        next_sentence=next_sentence,
        containing_paragraph=text,
        quotation_context=_quotation_context(text, start, end),
    )
