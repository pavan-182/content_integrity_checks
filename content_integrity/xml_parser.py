from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from lxml import etree

from .models import ParseWarning, ParsedRecord, TraceTextBlock
from .utils import (
    first_nonempty,
    join_nonempty,
    normalize_whitespace,
    path_stem,
    unique_preserve_order,
    year_from_date_text,
)


STRUCTURED_SECTION_LABELS = [
    "Background and Aims",
    "Background and Objective",
    "Background",
    "Objective",
    "Objectives",
    "Aims",
    "Aim",
    "Methods",
    "Method",
    "Results",
    "Conclusion",
    "Conclusions",
    "Discussion",
    "Introduction",
    "Purpose",
    "Patients and Methods",
    "Materials and Methods",
    "Trial Registration",
    "Case Report",
    "Case Presentation",
    "Interpretation",
    "Significance",
    "Design",
    "Setting",
    "Participants",
    "Interventions",
    "Key Findings",
    "Key Points",
]

EXCLUDED_TEXT_TAGS = {
    "aff": "affiliations",
    "affiliation": "affiliations",
    "author-list": "authors",
    "author_list": "authors",
    "contrib-group": "authors",
    "ref": "references",
    "ref-list": "references",
    "reference": "references",
    "references": "references",
    "table-wrap": "tables",
}
TRACE_BLOCK_TAGS = {"p": "paragraph", "list-item": "list_item", "preformat": "preformatted"}
TRIAL_ID_RE = re.compile(r"\bNCT\d{8}\b", re.IGNORECASE)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_tree(path: str | Path) -> etree._ElementTree:
    parser = etree.XMLParser(
        recover=False,
        resolve_entities=False,
        huge_tree=True,
        remove_blank_text=False,
        no_network=True,
    )
    return etree.parse(str(path), parser)


def _string_from_xpath(root: etree._Element, xpath: str) -> str:
    try:
        value = root.xpath(xpath)
    except Exception:
        return ""
    if isinstance(value, list):
        if not value:
            return ""
        item = value[0]
        if isinstance(item, etree._Element):
            return normalize_whitespace("".join(item.itertext()))
        return normalize_whitespace(str(item))
    return normalize_whitespace(str(value))


def _first_node(root: etree._Element, xpaths: list[str]) -> etree._Element | None:
    for xpath in xpaths:
        try:
            result = root.xpath(xpath)
        except Exception:
            continue
        if result:
            node = result[0]
            if isinstance(node, etree._Element):
                return node
    return None


def _all_nodes(root: etree._Element, xpaths: list[str]) -> list[etree._Element]:
    nodes: list[etree._Element] = []
    for xpath in xpaths:
        try:
            result = root.xpath(xpath)
        except Exception:
            continue
        for node in result:
            if isinstance(node, etree._Element):
                nodes.append(node)
    return nodes


def _paragraph_texts(abstract_node: etree._Element, excluded_sections: set[str] | None = None) -> list[str]:
    paragraphs: list[str] = []
    for child in abstract_node.iterchildren():
        if _local_name(child.tag) == "p":
            text = _text_without_excluded_blocks(child, excluded_sections)
            if text and not re.fullmatch(r"e\d+", text, re.IGNORECASE):
                paragraphs.append(text)
    if paragraphs:
        return paragraphs
    text = _text_without_excluded_blocks(abstract_node, excluded_sections)
    return [text] if text else []


def _text_without_excluded_blocks(node: etree._Element, excluded_sections: set[str] | None = None) -> str:
    parts = [node.text or ""]
    for child in node:
        excluded_name = EXCLUDED_TEXT_TAGS.get(_local_name(child.tag))
        if excluded_name:
            if excluded_sections is not None:
                excluded_sections.add(excluded_name)
        else:
            parts.append(_text_without_excluded_blocks(child, excluded_sections))
        parts.append(child.tail or "")
    return normalize_whitespace("".join(parts))


def _preserve_text(node: etree._Element, excluded_sections: set[str] | None = None) -> str:
    parts = [node.text or ""]
    for child in node:
        excluded_name = EXCLUDED_TEXT_TAGS.get(_local_name(child.tag))
        if excluded_name:
            if excluded_sections is not None:
                excluded_sections.add(excluded_name)
        else:
            parts.append(_preserve_text(child, excluded_sections))
        parts.append(child.tail or "")
    text = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _top_level_trace_nodes(node: etree._Element) -> list[etree._Element]:
    selected: list[etree._Element] = []
    for candidate in node.iterdescendants():
        if _local_name(candidate.tag) not in TRACE_BLOCK_TAGS:
            continue
        if any(
            ancestor is not node and _local_name(ancestor.tag) in TRACE_BLOCK_TAGS
            for ancestor in candidate.iterancestors()
        ):
            continue
        selected.append(candidate)
    return selected


def _split_preserved_structured_block(text: str) -> list[tuple[str, str]]:
    matches = list(STRUCTURED_LABEL_RE.finditer(text))
    if not matches:
        return [("Abstract", text)]
    blocks: list[tuple[str, str]] = []
    prefix = text[: matches[0].start("label")].strip()
    if prefix:
        blocks.append(("Abstract", prefix))
    for index, match in enumerate(matches):
        start = match.start("label")
        end = matches[index + 1].start("label") if index + 1 < len(matches) else len(text)
        source = text[start:end].strip()
        if source:
            blocks.append((_canonical_label(match.group("label")), source))
    return blocks


def build_trace_text_blocks(
    title_node: etree._Element | None,
    abstract_node: etree._Element | None,
    *,
    fallback_title: str,
    fallback_sections: list[dict[str, str]],
    excluded_sections: set[str] | None = None,
) -> tuple[list[TraceTextBlock], bool]:
    blocks: list[TraceTextBlock] = []
    title_source = _preserve_text(title_node, excluded_sections) if title_node is not None else fallback_title
    if title_source:
        blocks.append(TraceTextBlock("title", "Title", "title", 0, title_source))

    abstract_blocks: list[tuple[str, str, str]] = []
    if abstract_node is not None:
        sec_nodes = [child for child in abstract_node.iterchildren() if _local_name(child.tag) == "sec"]
        if sec_nodes:
            for sec in sec_nodes:
                label_node = next(
                    (child for child in sec.iterchildren() if _local_name(child.tag) in {"title", "label"}),
                    None,
                )
                label = _canonical_label(_preserve_text(label_node) if label_node is not None else "Section")
                content_nodes = _top_level_trace_nodes(sec)
                if content_nodes:
                    for content_node in content_nodes:
                        source = _preserve_text(content_node, excluded_sections)
                        if source:
                            abstract_blocks.append((label, TRACE_BLOCK_TAGS[_local_name(content_node.tag)], source))
                else:
                    source = _preserve_text(sec, excluded_sections)
                    label_source = _preserve_text(label_node) if label_node is not None else ""
                    if label_source and source.startswith(label_source):
                        source = source[len(label_source) :].strip()
                    if source:
                        abstract_blocks.append((label, "paragraph", source))
        else:
            content_nodes = _top_level_trace_nodes(abstract_node)
            if content_nodes:
                for content_node in content_nodes:
                    source = _preserve_text(content_node, excluded_sections)
                    if not source or re.fullmatch(r"e\d+", source, re.IGNORECASE):
                        continue
                    for label, preserved in _split_preserved_structured_block(source):
                        abstract_blocks.append((label, TRACE_BLOCK_TAGS[_local_name(content_node.tag)], preserved))
            else:
                source = _preserve_text(abstract_node, excluded_sections)
                if source:
                    abstract_blocks.append(("Abstract", "fallback_abstract", source))

    fallback = False
    if not abstract_blocks:
        fallback = bool(fallback_sections)
        abstract_blocks = [
            (section.get("section", "") or "Abstract", "fallback_abstract", section.get("text", ""))
            for section in fallback_sections
            if section.get("text", "")
        ]
    for index, (label, block_type, source) in enumerate(abstract_blocks):
        blocks.append(TraceTextBlock("abstract", label, block_type, index, source))
    return blocks, fallback


def _trace_provenance(blocks: list[TraceTextBlock]) -> tuple[str, list[TraceTextBlock]]:
    """Return a lossless record text and offsets for each trace block within it."""
    separator = "\n\n"
    parts: list[str] = []
    traced: list[TraceTextBlock] = []
    cursor = 0
    for block in blocks:
        source = block.source_text.strip()
        if not source:
            continue
        if parts:
            cursor += len(separator)
        start = cursor
        cursor += len(source)
        parts.append(source)
        traced.append(replace(block, source_text=source, source_start=start, source_end=cursor))
    return separator.join(parts), traced


def _excluded_sections_present(root: etree._Element) -> set[str]:
    return {
        excluded_name
        for node in root.iter()
        if (excluded_name := EXCLUDED_TEXT_TAGS.get(_local_name(node.tag)))
    }


def _canonical_label(label: str) -> str:
    cleaned = normalize_whitespace(label).rstrip(":-. ")
    cleaned = cleaned.title()
    cleaned = cleaned.replace(" And ", " and ")
    cleaned = cleaned.replace(" Of ", " of ")
    cleaned = cleaned.replace(" In ", " in ")
    cleaned = cleaned.replace(" On ", " on ")
    cleaned = cleaned.replace(" For ", " for ")
    cleaned = cleaned.replace(" With ", " with ")
    cleaned = cleaned.replace(" To ", " to ")
    return cleaned


def _build_label_pattern() -> re.Pattern[str]:
    escaped = sorted((re.escape(label) for label in STRUCTURED_SECTION_LABELS), key=len, reverse=True)
    label_group = "|".join(escaped)
    pattern = rf"(?i)(?:^|[.\n\r;]\s+)\s*(?P<label>{label_group})\s*[:\.\-]\s*"
    return re.compile(pattern)


STRUCTURED_LABEL_RE = _build_label_pattern()


def _split_structured_paragraph(text: str) -> list[dict[str, str]]:
    matches = list(STRUCTURED_LABEL_RE.finditer(text))
    if not matches:
        return []
    sections: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        label = _canonical_label(match.group("label"))
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = normalize_whitespace(text[body_start:body_end])
        if body:
            sections.append({"section": label, "text": body})
    return sections


def _merge_sections(sections: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for section in sections:
        label = section["section"]
        text = normalize_whitespace(section["text"])
        if not text:
            continue
        if merged and merged[-1]["section"].lower() == label.lower():
            merged[-1]["text"] = normalize_whitespace(f"{merged[-1]['text']} {text}")
        else:
            merged.append({"section": label, "text": text})
    return merged


def extract_abstract_sections(
    abstract_node: etree._Element | None,
    fallback_text: str,
    excluded_sections: set[str] | None = None,
) -> tuple[list[dict[str, str]], bool, str]:
    if abstract_node is None:
        cleaned = normalize_whitespace(fallback_text)
        if not cleaned:
            return [], False, ""
        return ([{"section": "Abstract", "text": cleaned}], False, cleaned)

    explicit_sections: list[dict[str, str]] = []
    sec_nodes = [child for child in abstract_node.iterchildren() if _local_name(child.tag) == "sec"]
    if sec_nodes:
        for sec in sec_nodes:
            title = None
            for child in sec.iterchildren():
                if _local_name(child.tag) in {"title", "label"}:
                    title = normalize_whitespace("".join(child.itertext()))
                    if title:
                        break
            section_name = _canonical_label(title or "Section")
            section_text = _text_without_excluded_blocks(sec, excluded_sections)
            if title and section_text.lower().startswith(title.lower()):
                section_text = normalize_whitespace(section_text[len(title) :])
            explicit_sections.append({"section": section_name, "text": section_text})
        explicit_sections = _merge_sections(explicit_sections)
        full_text = normalize_whitespace(" ".join(item["text"] for item in explicit_sections))
        return explicit_sections, True, full_text

    paragraphs = _paragraph_texts(abstract_node, excluded_sections)
    if paragraphs and re.fullmatch(r"[A-Za-z]*\d+(?:[._-][A-Za-z0-9]+)*", paragraphs[0]):
        paragraphs = paragraphs[1:]
    collected_sections: list[dict[str, str]] = []
    structured = False
    for paragraph in paragraphs:
        split_sections = _split_structured_paragraph(paragraph)
        if split_sections:
            structured = True
            collected_sections.extend(split_sections)
        else:
            collected_sections.append({"section": "Abstract", "text": paragraph})
    collected_sections = _merge_sections(collected_sections)
    if not collected_sections:
        cleaned = normalize_whitespace(" ".join(paragraphs))
        if cleaned:
            return ([{"section": "Abstract", "text": cleaned}], False, cleaned)
        return [], False, ""
    full_text = normalize_whitespace(" ".join(item["text"] for item in collected_sections))
    return collected_sections, structured, full_text


def _format_affiliation(node: etree._Element) -> str:
    parts: list[str] = []
    for tag in ("institution", "inst", "dept", "department", "person_title", "city", "state", "province", "country", "post_code"):
        for child in node.iter():
            if _local_name(child.tag) == tag:
                text = normalize_whitespace("".join(child.itertext()))
                if text:
                    parts.append(text)
    cleaned = []
    for part in parts:
        if part not in cleaned:
            cleaned.append(part)
    return ", ".join(cleaned) or normalize_whitespace("".join(node.itertext()))


def _format_author_node(node: etree._Element) -> str:
    candidates = [
        join_nonempty(
            [
                _string_from_xpath(node, ".//*[local-name()='name']/*[local-name()='prefix'][1]"),
                _string_from_xpath(node, ".//*[local-name()='name']/*[local-name()='given-names'][1]"),
                _string_from_xpath(node, ".//*[local-name()='name']/*[local-name()='surname'][1]"),
                _string_from_xpath(node, ".//*[local-name()='name']/*[local-name()='suffix'][1]"),
            ],
            delimiter=" ",
        ),
        join_nonempty(
            [
                _string_from_xpath(node, ".//*[local-name()='first_name'][1]"),
                _string_from_xpath(node, ".//*[local-name()='middle_name'][1]"),
                _string_from_xpath(node, ".//*[local-name()='last_name'][1]"),
                _string_from_xpath(node, ".//*[local-name()='suffix'][1]"),
            ],
            delimiter=" ",
        ),
        _string_from_xpath(node, ".//*[local-name()='collab'][1]"),
    ]
    return first_nonempty(*candidates)


def _primary_author(author_nodes: list[etree._Element]) -> str:
    presenter = next((node for node in author_nodes if node.get("contrib-type") == "presenter"), None)
    return _format_author_node(presenter if presenter is not None else author_nodes[0]) if author_nodes else ""


def _extract_keywords(root: etree._Element) -> list[str]:
    keywords: list[str] = []
    # Wiley article JATS
    for node in _all_nodes(root, [".//*[local-name()='kwd']"]):
        text = normalize_whitespace("".join(node.itertext()))
        if text:
            keywords.append(text)
    # Manuscript Central article_set keywords
    for node in _all_nodes(root, [".//*[local-name()='attr_type'][@name='Keywords']/*[local-name()='attribute'][@name]"]):
        text = normalize_whitespace(node.get("name"))
        if text:
            keywords.append(text)
    return unique_preserve_order(keywords)


def _extract_article_root(tree: etree._ElementTree | etree._Element, source_file: str) -> ParsedRecord:
    root = tree.getroot() if isinstance(tree, etree._ElementTree) else tree
    article_meta = _first_node(root, [".//*[local-name()='article-meta'][1]"])
    metadata_root = article_meta if article_meta is not None else root
    record_id = first_nonempty(
        _string_from_xpath(metadata_root, ".//*[local-name()='article-id'][@pub-id-type='manuscript'][1]"),
        _string_from_xpath(metadata_root, ".//*[local-name()='article-id'][@pub-id-type='submission-id'][1]"),
        _string_from_xpath(metadata_root, ".//*[local-name()='article-id'][@custom-type='abstract-id'][1]"),
        _string_from_xpath(metadata_root, ".//*[local-name()='article-id'][@pub-id-type='doi'][1]"),
        path_stem(source_file),
    )
    doi = _string_from_xpath(metadata_root, ".//*[local-name()='article-id'][@pub-id-type='doi'][1]")
    title_node = _first_node(
        metadata_root,
        [
            ".//*[local-name()='title-group'][1]/*[local-name()='article-title'][1]",
            ".//*[local-name()='article-title'][1]",
        ],
    )
    title = normalize_whitespace("".join(title_node.itertext())) if title_node is not None else ""
    abstract_node = _first_node(metadata_root, [".//*[local-name()='abstract'][1]"])
    excluded_sections = _excluded_sections_present(root)
    abstract_sections, structured, abstract_text = extract_abstract_sections(abstract_node, "", excluded_sections)
    trace_text_blocks, trace_fallback = build_trace_text_blocks(
        title_node,
        abstract_node,
        fallback_title=title,
        fallback_sections=abstract_sections,
        excluded_sections=excluded_sections,
    )
    keywords = _extract_keywords(metadata_root)
    author_nodes = _all_nodes(metadata_root, [".//*[local-name()='contrib-group'][1]/*[local-name()='contrib'][@contrib-type='author' or @contrib-type='presenter']", ".//*[local-name()='author'][@contrib-type='author']"])
    authors = unique_preserve_order(_format_author_node(node) for node in author_nodes)
    primary_author = _primary_author(author_nodes)
    affiliations = unique_preserve_order(
        _format_affiliation(node)
        for node in _all_nodes(metadata_root, [".//*[local-name()='aff']", ".//*[local-name()='profile_affiliation']", ".//*[local-name()='current_profile_affiliation']"])
    )
    journal = first_nonempty(
        _string_from_xpath(root, ".//*[local-name()='journal-meta'][1]/*[local-name()='journal-title-group'][1]/*[local-name()='journal-title'][1]"),
        _string_from_xpath(root, ".//*[local-name()='journal-meta'][1]/*[local-name()='journal-title'][1]"),
    )
    article_type = first_nonempty(
        _string_from_xpath(metadata_root, ".//*[local-name()='article-categories'][1]//*[local-name()='subject'][1]"),
        root.get("article-type"),
    )
    publication_year = first_nonempty(
        _string_from_xpath(metadata_root, ".//*[local-name()='pub-date'][1]/*[local-name()='year'][1]"),
        _string_from_xpath(metadata_root, ".//*[local-name()='history'][1]/*[local-name()='date'][@date-type='accepted'][1]/*[local-name()='year'][1]"),
        _string_from_xpath(metadata_root, ".//*[local-name()='history'][1]/*[local-name()='date'][@date-type='epub'][1]/*[local-name()='year'][1]"),
        _string_from_xpath(metadata_root, ".//*[local-name()='history'][1]/*[local-name()='date'][@date-type='received'][1]/*[local-name()='year'][1]"),
        _string_from_xpath(metadata_root, ".//*[local-name()='copyright-year'][1]"),
    )
    raw_text, trace_text_blocks = _trace_provenance(trace_text_blocks)
    trial_ids = unique_preserve_order(match.upper() for match in TRIAL_ID_RE.findall(f"{title} {abstract_text}"))
    warnings: list[ParseWarning] = []
    for field_name, value in (
        ("record_id", record_id),
        ("title", title),
        ("abstract_text", abstract_text),
        ("journal", journal),
        ("article_type", article_type),
        ("publication_year", publication_year),
    ):
        if not value:
            warnings.append(
                ParseWarning(
                    warning_code=f"missing_{field_name}",
                    warning_message=f"Missing expected field: {field_name}.",
                    field_name=field_name,
                    severity="warning",
                )
            )
    if trace_fallback:
        warnings.append(
            ParseWarning(
                warning_code="llm_trace_preprocessing_fallback",
                warning_message="Lossless XML trace blocks were unavailable; best available parsed text was used.",
                field_name="abstract_text",
            )
        )
    parse_status = "parsed_with_warnings" if warnings else "parsed"
    return ParsedRecord(
        source_file=source_file,
        schema_type=_local_name(root.tag),
        record_id=record_id,
        doi=doi,
        title=title,
        abstract_text=abstract_text,
        abstract_sections=abstract_sections or [{"section": "Abstract", "text": abstract_text}],
        structured_abstract=structured,
        keywords=keywords,
        trial_ids=trial_ids,
        authors=authors,
        primary_author=primary_author,
        affiliations=affiliations,
        journal=journal,
        article_type=article_type,
        publication_year=publication_year,
        raw_text=raw_text,
        excluded_sections=sorted(excluded_sections),
        parse_status=parse_status,
        parse_warnings=warnings,
        trace_text_blocks=trace_text_blocks,
        trace_preprocessing_fallback=trace_fallback,
    )


def _extract_article_set_root(tree: etree._ElementTree, source_file: str) -> ParsedRecord:
    root = tree.getroot()
    article = _first_node(root, [".//*[local-name()='article'][1]"])
    if article is None:
        return ParsedRecord(
            source_file=source_file,
            schema_type="article_set",
            record_id=path_stem(source_file),
            raw_text="",
            parse_status="failed",
            parse_warnings=[ParseWarning("missing_article_node", "Could not locate nested article node.", severity="error")],
        )

    record_id = first_nonempty(
        article.get("ms_no"),
        article.get("tracking_no"),
        _string_from_xpath(article, ".//*[local-name()='article_id_list'][1]/*[local-name()='article_id'][@id_type='manuscript'][1]"),
        _string_from_xpath(article, ".//*[local-name()='article_id_list'][1]/*[local-name()='article_id'][@id_type='submission-id'][1]"),
        _string_from_xpath(article, ".//*[local-name()='article_id_list'][1]/*[local-name()='article_id'][@id_type='publisher-id'][1]"),
        path_stem(source_file),
    )
    doi = _string_from_xpath(article, ".//*[local-name()='article_id_list'][1]/*[local-name()='article_id'][@id_type='doi'][1]")
    title_node = _first_node(article, [".//*[local-name()='article_title'][1]"])
    title = normalize_whitespace("".join(title_node.itertext())) if title_node is not None else ""
    abstract_node = _first_node(article, [".//*[local-name()='abstract'][1]"])
    excluded_sections = _excluded_sections_present(article)
    abstract_sections, structured, abstract_text = extract_abstract_sections(abstract_node, "", excluded_sections)
    trace_text_blocks, trace_fallback = build_trace_text_blocks(
        title_node,
        abstract_node,
        fallback_title=title,
        fallback_sections=abstract_sections,
        excluded_sections=excluded_sections,
    )
    keywords = _extract_keywords(article)
    author_nodes = _all_nodes(article, [".//*[local-name()='author_list'][1]/*[local-name()='author']", ".//*[local-name()='contrib-group'][1]/*[local-name()='contrib'][@contrib-type='author' or @contrib-type='presenter']"])
    authors = unique_preserve_order(_format_author_node(node) for node in author_nodes)
    primary_author = _primary_author(author_nodes)
    affiliations = unique_preserve_order(
        _format_affiliation(node)
        for node in _all_nodes(article, [".//*[local-name()='author_list'][1]/*[local-name()='author']/*[local-name()='affiliation']", ".//*[local-name()='author_list'][1]/*[local-name()='author']/*[local-name()='profile_affiliation']", ".//*[local-name()='author_list'][1]/*[local-name()='author']/*[local-name()='current_profile_affiliation']", ".//*[local-name()='affiliation']", ".//*[local-name()='profile_affiliation']", ".//*[local-name()='current_profile_affiliation']"])
    )
    journal = first_nonempty(
        _string_from_xpath(article, ".//*[local-name()='journal'][1]/*[local-name()='full_journal_title'][1]"),
        _string_from_xpath(article, ".//*[local-name()='journal'][1]/*[local-name()='publisher_name'][1]"),
        _string_from_xpath(article, ".//*[local-name()='journal'][1]/*[local-name()='journal_abbreviation'][1]"),
        _string_from_xpath(article, ".//*[local-name()='journal'][1]/*[local-name()='journal'][1]"),
    )
    article_type = first_nonempty(
        _string_from_xpath(article, ".//*[local-name()='publication_type'][1]"),
        _string_from_xpath(article, ".//*[local-name()='custom_fields'][@cd_code='Wiley - Manuscript type'][1]/@cd_value"),
        _string_from_xpath(article, ".//*[local-name()='article_status'][1]"),
    )
    publication_year = first_nonempty(
        _string_from_xpath(article, ".//*[local-name()='history'][1]/*[local-name()='date'][@date-type='accepted'][1]/*[local-name()='year'][1]"),
        _string_from_xpath(article, ".//*[local-name()='history'][1]/*[local-name()='date'][@date-type='rev-recd'][1]/*[local-name()='year'][1]"),
        _string_from_xpath(article, ".//*[local-name()='history'][1]/*[local-name()='date'][@date-type='received'][1]/*[local-name()='year'][1]"),
        year_from_date_text(article.get("export_date")),
    )
    raw_text, trace_text_blocks = _trace_provenance(trace_text_blocks)
    trial_ids = unique_preserve_order(match.upper() for match in TRIAL_ID_RE.findall(f"{title} {abstract_text}"))
    warnings: list[ParseWarning] = []
    for field_name, value in (
        ("record_id", record_id),
        ("title", title),
        ("abstract_text", abstract_text),
        ("journal", journal),
        ("article_type", article_type),
        ("publication_year", publication_year),
    ):
        if not value:
            warnings.append(
                ParseWarning(
                    warning_code=f"missing_{field_name}",
                    warning_message=f"Missing expected field: {field_name}.",
                    field_name=field_name,
                    severity="warning",
                )
            )
    if trace_fallback:
        warnings.append(
            ParseWarning(
                warning_code="llm_trace_preprocessing_fallback",
                warning_message="Lossless XML trace blocks were unavailable; best available parsed text was used.",
                field_name="abstract_text",
            )
        )
    parse_status = "parsed_with_warnings" if warnings else "parsed"
    return ParsedRecord(
        source_file=source_file,
        schema_type="article_set",
        record_id=record_id,
        doi=doi,
        title=title,
        abstract_text=abstract_text,
        abstract_sections=abstract_sections or [{"section": "Abstract", "text": abstract_text}],
        structured_abstract=structured,
        keywords=keywords,
        trial_ids=trial_ids,
        authors=authors,
        primary_author=primary_author,
        affiliations=affiliations,
        journal=journal,
        article_type=article_type,
        publication_year=publication_year,
        raw_text=raw_text,
        excluded_sections=sorted(excluded_sections),
        parse_status=parse_status,
        parse_warnings=warnings,
        trace_text_blocks=trace_text_blocks,
        trace_preprocessing_fallback=trace_fallback,
    )


def parse_xml(path: str | Path) -> ParsedRecord:
    return parse_xml_records(path)[0]


def parse_xml_records(path: str | Path) -> list[ParsedRecord]:
    source_file = Path(path).name
    try:
        tree = _parse_tree(path)
    except Exception as exc:
        return [ParsedRecord(
            source_file=source_file,
            schema_type="unknown",
            record_id=path_stem(source_file),
            parse_status="failed",
            parse_warnings=[
                ParseWarning(
                    warning_code="xml_parse_failed",
                    warning_message=f"Could not parse XML: {exc}",
                    severity="error",
                )
            ],
        )]

    root = tree.getroot()
    schema = _local_name(root.tag)
    if schema == "article":
        article_nodes = [root, *root.xpath(".//*[local-name()='sub-article']")]
        records = [_extract_article_root(node, source_file) for node in article_nodes]
        if len(records) > 1:
            for record in records:
                record.source_file = f"{source_file}#{record.record_id}"
        return records
    if schema == "article_set":
        return [_extract_article_set_root(tree, source_file)]

    # Fallback for unexpected XML root types.
    record_id = path_stem(source_file)
    excluded_sections = _excluded_sections_present(root)
    text = _text_without_excluded_blocks(root, excluded_sections)
    return [ParsedRecord(
        source_file=source_file,
        schema_type=schema,
        record_id=record_id,
        raw_text=text,
        excluded_sections=sorted(excluded_sections),
        parse_status="parsed_with_warnings",
        parse_warnings=[
            ParseWarning(
                warning_code="unexpected_root",
                warning_message=f"Unexpected XML root element: {schema}",
                severity="warning",
            )
        ],
    )]


def discover_xml_files(input_dir: str | Path) -> list[Path]:
    root = Path(input_dir)
    return sorted(path for path in root.rglob("*.xml") if path.is_file())
