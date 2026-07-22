#!/usr/bin/env python3
"""Convert article rows from a CSV into separate ASCO-style JATS XML files.

Each eligible CSV row becomes one standalone XML file. Rows whose abstract
column contains a retraction notice, withdrawal notice, or publisher notice in
place of a research abstract are filtered out by default. The complete value
from every retained abstract is preserved. Structured section markup is added
only when the abstract begins with explicit section headings such as "Abstract
Background ... Methods ... Results ... Conclusion ..." or "Background: ...
Methods: ...".

Example:
    python csv_to_asco_xml_v2.py \
        --csv "phase0_full_213_manual_read (1).csv" \
        --output-dir asco_xml_output
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
import xml.etree.ElementTree as ET


JATS_DOCTYPE = (
    '<!DOCTYPE article PUBLIC "-//NLM//DTD JATS (Z39.96) Journal Publishing DTD v1.3 20210610//EN" '
    '"JATS-journalpublishing1-3.dtd">'
)

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Deliberately case-sensitive. This prevents ordinary phrases such as
# "the results showed" or "in conclusion" from being mistaken for headings.
SECTION_MARKER = re.compile(
    r"(?<![A-Za-z])"
    r"(Backgrounds?/aims|Background|Introduction|Objective|Objectives|Purpose|"
    r"Patients and methods|Materials and methods|Methods?|Results?|Findings?|"
    r"Conclusions?|Discussion)"
    r"\s*:?[\t \u00a0]+"
)

# Strong publisher/retraction-notice patterns. These are intentionally narrow:
# the converter filters only rows that clearly contain notice text in place of
# a research abstract. Matching is performed after HTML decoding, lowercasing,
# URL replacement, and number replacement.
NOTICE_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    (
        "article_retracted",
        re.compile(r"\bthis article (?:has been|was) retracted\b", re.IGNORECASE),
    ),
    (
        "article_withdrawn_by_publisher",
        re.compile(
            r"\bthis article (?:was|has been) withdrawn by (?:the )?publishers?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "retraction_notice_link",
        re.compile(
            r"\b(?:the )?retraction notice is available at\s*(?:<url>|https?://|www\.)",
            re.IGNORECASE,
        ),
    ),
    (
        "online_pdf_replaced_by_notice",
        re.compile(
            r"\bonline pdf (?:was |has been )?(?:replaced|watermarked)\b.*\bretraction\b",
            re.IGNORECASE,
        ),
    ),
    (
        "publisher_formal_retraction_notice",
        re.compile(r"\bofficially retracts the article entitled\b", re.IGNORECASE),
    ),
    (
        "cross_paper_methods_similarity_notice",
        re.compile(
            r"\bfigures? and text within the methods section (?:are|is) strikingly similar\b"
            r".*\bsame party prepared (?:these|the) papers?\b",
            re.IGNORECASE,
        ),
    ),
)

URL_TOKEN_RE = re.compile(r"https?://\S+|www\.\S+|<url>", re.IGNORECASE)
NUMBER_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])\d+(?:[.,:/-]\d+)*(?![A-Za-z])|<num>", re.IGNORECASE
)


SECTION_CANONICAL = {
    "Backgrounds/aims": "Background",
    "Background": "Background",
    "Introduction": "Background",
    "Objective": "Background",
    "Objectives": "Background",
    "Purpose": "Background",
    "Patient and method": "Methods",
    "Patients and methods": "Methods",
    "Materials and methods": "Methods",
    "Method": "Methods",
    "Methods": "Methods",
    "Result": "Results",
    "Results": "Results",
    "Finding": "Results",
    "Findings": "Results",
    "Conclusion": "Conclusions",
    "Conclusions": "Conclusions",
    "Discussion": "Conclusions",
}


class ConversionError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert CSV article rows into separate ASCO-style JATS XML files."
    )
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--title-column", default="Title")
    parser.add_argument("--abstract-column", default="abstract_text")
    parser.add_argument("--record-id-column", default="Record ID")
    parser.add_argument("--authors-column", default="Author")
    parser.add_argument("--institution-column", default="Institution")
    parser.add_argument("--subject-column", default="Subject")
    parser.add_argument("--doi-column", default="OriginalPaperDOI")
    parser.add_argument("--date-column", default="OriginalPaperDate")
    parser.add_argument("--journal-column", default="Journal")
    parser.add_argument("--publisher-column", default="Publisher")
    parser.add_argument("--country-column", default="Country")
    parser.add_argument("--article-type-column", default="ArticleType")
    parser.add_argument("--manual-verdict-column", default="manual_verdict")
    parser.add_argument("--reason-column", default="Reason")
    parser.add_argument("--abstract-id-prefix", default="rw")
    parser.add_argument("--meeting-id", default="335")
    parser.add_argument("--session-id", default="17304")
    parser.add_argument(
        "--series-title",
        default="2026 ASCO Annual Meeting (May 29 - June 2, 2026)",
    )
    parser.add_argument("--series-text", default="Publication Only")
    parser.add_argument("--subject-default", default="Oncology—General")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--include-empty-abstracts", action="store_true")
    parser.add_argument(
        "--include-notice-like-abstracts",
        action="store_true",
        help=(
            "Do not filter rows whose abstract text looks like a retraction, "
            "withdrawal, or publisher notice. By default, such rows are excluded."
        ),
    )
    return parser.parse_args()


def clean_cell(value: Optional[str], *, preserve_newlines: bool = False) -> str:
    if value is None:
        return ""
    value = html.unescape(str(value).replace("\ufeff", "")).replace("\u00a0", " ").strip()
    if value.lower() in {"nan", "none", "null", "nat"}:
        return ""
    if preserve_newlines:
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\s*\n\s*", "\n", value)
        return value.strip()
    return re.sub(r"\s+", " ", value).strip()


def normalize_for_notice_filter(value: Optional[str]) -> str:
    """Normalize text for robust notice-pattern matching.

    URLs and numbers are replaced with the same placeholders used in the user's
    examples. The original CSV value is never modified.
    """
    text = clean_cell(value, preserve_newlines=True).lower()
    text = URL_TOKEN_RE.sub("<url>", text)
    text = NUMBER_TOKEN_RE.sub("<num>", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .;,:-")


def detect_notice_like_abstract(value: Optional[str]) -> Optional[str]:
    """Return the matching filter rule, or None for a research abstract."""
    normalized = normalize_for_notice_filter(value)
    if not normalized:
        return None
    for rule_name, pattern in NOTICE_PATTERNS:
        if pattern.search(normalized):
            return rule_name
    return None


def normalize_record_id(raw: str, row_number: int) -> str:
    raw = clean_cell(raw)
    if re.fullmatch(r"\d+\.0", raw):
        raw = raw[:-2]
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    return normalized or f"row{row_number:05d}"


def first_subject(raw: str, default: str) -> str:
    value = clean_cell(raw)
    if not value:
        return default
    first = next((part.strip() for part in value.split(";") if part.strip()), default)
    return re.sub(r"^\([A-Za-z]+\)\s*", "", first).strip() or default


def parse_year(raw_date: str) -> str:
    value = clean_cell(raw_date)
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return match.group(0) if match else str(datetime.now().year)


def split_authors(raw: str) -> List[str]:
    value = clean_cell(raw)
    if not value:
        return ["Author not provided"]
    return [part.strip() for part in value.split(";") if part.strip()] or ["Author not provided"]


def split_person_name(full_name: str) -> Tuple[str, str]:
    full_name = clean_cell(full_name)
    if not full_name:
        return "", "Author not provided"
    if "," in full_name:
        surname, given = [part.strip() for part in full_name.split(",", 1)]
        return given, surname
    parts = full_name.split()
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]


def split_explicit_sections(text: str) -> List[Tuple[str, str]]:
    """Split only genuinely structured abstracts; otherwise return an empty list.

    Crucially, this function never treats lowercase prose such as "the results
    showed" or "in conclusion" as section headings.
    """
    normalized = clean_cell(text, preserve_newlines=True)
    if not normalized:
        return []

    # Remove a publisher-added leading label, while retaining the actual abstract.
    working = re.sub(r"^Abstract\s*:?\s*", "", normalized, count=1)
    matches = list(SECTION_MARKER.finditer(working))
    if not matches:
        return []

    # A structured abstract must begin with a heading and contain at least two headings.
    if matches[0].start() > 2 or len(matches) < 2:
        return []

    sections: List[Tuple[str, str]] = []
    for index, match in enumerate(matches):
        label = SECTION_CANONICAL.get(match.group(1), match.group(1))
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(working)
        body = working[start:end].strip(" \t\r\n:;-")
        if body:
            sections.append((label, body))

    if len(sections) < 2:
        return []
    return sections


def sub(parent: ET.Element, tag: str, text: Optional[str] = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = text
    return element


def add_journal_meta(front: ET.Element) -> None:
    journal_meta = sub(front, "journal-meta")
    sub(journal_meta, "journal-id", "ascomtg", **{"journal-id-type": "hwp"})
    sub(journal_meta, "journal-id", "ascomtg", **{"journal-id-type": "pmc"})
    sub(journal_meta, "journal-id", "ASCOMTG", **{"journal-id-type": "publisher-id"})
    title_group = sub(journal_meta, "journal-title-group")
    sub(title_group, "journal-title", "Journal of Clinical Oncology")
    sub(title_group, "abbrev-journal-title", "ASCO MEETING ABSTRACTS")
    sub(journal_meta, "issn", "0732-183X", **{"pub-type": "ppub"})
    sub(journal_meta, "issn", "1527-7755", **{"pub-type": "epub"})
    publisher = sub(journal_meta, "publisher")
    sub(publisher, "publisher-name", "American Society of Clinical Oncology")


def add_contributors(article_meta: ET.Element, authors_raw: str, institution_raw: str, id_seed: int) -> None:
    contrib_group = sub(article_meta, "contrib-group")
    authors = split_authors(authors_raw)
    affiliation_id = f"A{id_seed}"
    for index, author in enumerate(authors):
        contributor_id = f"C{id_seed + index}"
        contrib = sub(
            contrib_group,
            "contrib",
            id=contributor_id,
            **{"contrib-type": "presenter" if index == 0 else "author"},
        )
        name = sub(contrib, "name")
        given, surname = split_person_name(author)
        sub(name, "surname", surname)
        if given:
            sub(name, "given-names", given)
        sub(contrib, "xref", **{"ref-type": "aff", "rid": affiliation_id})
    institution = clean_cell(institution_raw) or "Institution not provided in source CSV"
    sub(contrib_group, "aff", institution, id=affiliation_id)


def add_abstract(article_meta: ET.Element, abstract_id: str, abstract_text: str) -> None:
    abstract = sub(article_meta, "abstract")
    id_paragraph = sub(abstract, "p")
    sub(id_paragraph, "bold", abstract_id)

    full_text = clean_cell(abstract_text, preserve_newlines=True)
    sections = split_explicit_sections(full_text)
    if not sections:
        # Preserve the complete abstract as one paragraph. Remove only a publisher-added
        # leading "Abstract" label; no content is discarded.
        full_text = re.sub(r"^Abstract\s*:?\s*", "", full_text, count=1)
        sub(abstract, "p", full_text)
        return

    paragraph = sub(abstract, "p")
    for label, body in sections:
        bold = sub(paragraph, "bold", f"{label}: ")
        bold.tail = body + "  "


def add_custom_meta(article_meta: ET.Element, row: Dict[str, str], args: argparse.Namespace, row_number: int) -> None:
    values = [
        ("source-row-number", str(row_number)),
        ("source-record-id", clean_cell(row.get(args.record_id_column))),
        ("source-journal", clean_cell(row.get(args.journal_column))),
        ("source-publisher", clean_cell(row.get(args.publisher_column))),
        ("source-country", clean_cell(row.get(args.country_column))),
        ("source-article-type", clean_cell(row.get(args.article_type_column))),
        ("manual-verdict", clean_cell(row.get(args.manual_verdict_column))),
        ("source-reason", clean_cell(row.get(args.reason_column))),
    ]
    values = [(name, value) for name, value in values if value]
    if not values:
        return
    group = sub(article_meta, "custom-meta-group")
    for name, value in values:
        meta = sub(group, "custom-meta")
        sub(meta, "meta-name", name)
        sub(meta, "meta-value", value)


def build_article_xml(row: Dict[str, str], row_number: int, args: argparse.Namespace) -> Tuple[str, bytes]:
    title = clean_cell(row.get(args.title_column))
    abstract_text = clean_cell(row.get(args.abstract_column), preserve_newlines=True)
    if not title:
        raise ConversionError(f"Row {row_number}: missing {args.title_column!r}")
    if not abstract_text and not args.include_empty_abstracts:
        raise ConversionError(f"Row {row_number}: missing {args.abstract_column!r}")

    record_id = normalize_record_id(row.get(args.record_id_column, ""), row_number)
    abstract_id = f"{args.abstract_id_prefix}{record_id}"
    numeric_seed = 8000000 + row_number * 100
    year = parse_year(row.get(args.date_column, ""))

    root = ET.Element(
        "article",
        {
            "dtd-version": "1.3",
            "article-type": "meeting-abstract",
            "xmlns:xsi": XSI_NS,
            "xmlns:xlink": XLINK_NS,
        },
    )
    front = sub(root, "front")
    add_journal_meta(front)
    article_meta = sub(front, "article-meta")

    sub(article_meta, "article-id", args.meeting_id, **{"pub-id-type": "custom", "custom-type": "meeting-id"})
    sub(article_meta, "article-id", args.session_id, **{"pub-id-type": "custom", "custom-type": "session-id"})
    sub(article_meta, "article-id", str(numeric_seed), **{"pub-id-type": "custom", "custom-type": "session-participation-id"})
    sub(article_meta, "article-id", record_id, **{"pub-id-type": "custom", "custom-type": "temporary-abstract-id"})
    sub(article_meta, "article-id", abstract_id, **{"pub-id-type": "custom", "custom-type": "abstract-id"})
    doi = clean_cell(row.get(args.doi_column))
    if doi:
        sub(article_meta, "article-id", doi, **{"pub-id-type": "doi"})

    categories = sub(article_meta, "article-categories")
    subject_group = sub(categories, "subj-group", **{"subj-group-type": "heading"})
    sub(subject_group, "subject", first_subject(row.get(args.subject_column, ""), args.subject_default))
    sub(categories, "series-title", args.series_title)
    sub(categories, "series-text", args.series_text)

    title_group = sub(article_meta, "title-group")
    sub(title_group, "article-title", title)
    add_contributors(article_meta, row.get(args.authors_column, ""), row.get(args.institution_column, ""), numeric_seed)

    pub_date = sub(article_meta, "pub-date", **{"pub-type": "ppub"})
    sub(pub_date, "year", year)
    sub(article_meta, "fpage", abstract_id)
    sub(article_meta, "lpage", abstract_id)

    permissions = sub(article_meta, "permissions")
    sub(permissions, "copyright-statement", "ASCO-style conversion for internal testing; source rights remain with the original publisher.")
    sub(permissions, "copyright-year", year)

    add_abstract(article_meta, abstract_id, abstract_text)
    add_custom_meta(article_meta, row, args, row_number)

    xml_body = ET.tostring(root, encoding="utf-8", xml_declaration=False)
    return (
        abstract_id,
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        + JATS_DOCTYPE.encode("utf-8")
        + b"\n"
        + xml_body
        + b"\n",
    )


def validate_required_columns(fieldnames: Sequence[str], args: argparse.Namespace) -> None:
    missing = [column for column in (args.title_column, args.abstract_column) if column not in fieldnames]
    if missing:
        raise SystemExit("Missing required CSV column(s): " + ", ".join(repr(x) for x in missing))


def choose_output_path(output_dir: Path, abstract_id: str, overwrite: bool) -> Path:
    path = output_dir / f"{abstract_id}.xml"
    if overwrite or not path.exists():
        return path
    counter = 2
    while True:
        candidate = output_dir / f"{abstract_id}_{counter}.xml"
        if not candidate.exists():
            return candidate
        counter += 1


def extract_abstract_text(xml_bytes: bytes) -> str:
    root = ET.fromstring(xml_bytes.split(b"\n", 2)[2])
    abstract = root.find("./front/article-meta/abstract")
    if abstract is None:
        return ""
    paragraphs = abstract.findall("p")
    if len(paragraphs) < 2:
        return ""
    return clean_cell(" ".join("".join(p.itertext()) for p in paragraphs[1:]))


def source_content_words(text: str) -> List[str]:
    text = clean_cell(text)
    text = re.sub(r"^Abstract\s*:?\s*", "", text, count=1)
    # Heading labels may be converted to bold labels with colons. Ignore them for
    # content-preservation validation.
    text = re.sub(
        r"\b(?:Backgrounds?/aims|Background|Introduction|Objective|Objectives|Purpose|"
        r"Patients and methods|Materials and methods|Methods?|Results?|Findings?|"
        r"Conclusions?|Discussion)\s*:?[ ]+",
        " ",
        text,
    )
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def validate_abstract_preservation(source_text: str, xml_bytes: bytes) -> None:
    source_words = source_content_words(source_text)
    output_words = source_content_words(extract_abstract_text(xml_bytes))
    if source_words != output_words:
        # Provide a useful failure rather than silently creating truncated XML.
        mismatch = next(
            (i for i, (a, b) in enumerate(zip(source_words, output_words)) if a != b),
            min(len(source_words), len(output_words)),
        )
        raise ConversionError(
            f"abstract preservation check failed at word {mismatch}; "
            f"source_words={len(source_words)}, output_words={len(output_words)}"
        )


def convert(args: argparse.Namespace) -> int:
    if not args.csv.exists():
        raise SystemExit(f"Input CSV not found: {args.csv}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "conversion_manifest.csv"
    filtered_path = args.output_dir / "filtered_notice_rows.csv"
    success_count = 0
    filtered_count = 0
    skipped_count = 0

    with args.csv.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header row.")
        validate_required_columns(reader.fieldnames, args)

        rows: Iterable[Tuple[int, Dict[str, str]]] = enumerate(reader, start=2)
        if args.start:
            rows = ((line_no, row) for index, (line_no, row) in enumerate(rows) if index >= args.start)

        with (
            manifest_path.open("w", encoding="utf-8", newline="") as manifest_file,
            filtered_path.open("w", encoding="utf-8", newline="") as filtered_file,
        ):
            writer = csv.DictWriter(
                manifest_file,
                fieldnames=[
                    "csv_line",
                    "record_id",
                    "abstract_id",
                    "output_file",
                    "title",
                    "status",
                    "filter_rule",
                    "message",
                ],
            )
            writer.writeheader()

            filtered_writer = csv.DictWriter(
                filtered_file,
                fieldnames=[
                    "csv_line",
                    "record_id",
                    "title",
                    "filter_rule",
                    "abstract_text",
                ],
            )
            filtered_writer.writeheader()

            processed = 0
            for csv_line, row in rows:
                if args.limit is not None and processed >= args.limit:
                    break
                processed += 1

                title = clean_cell(row.get(args.title_column))
                source_record_id = clean_cell(row.get(args.record_id_column))
                source_abstract = row.get(args.abstract_column, "")

                filter_rule = detect_notice_like_abstract(source_abstract)
                if filter_rule and not args.include_notice_like_abstracts:
                    filtered_count += 1
                    message = (
                        "filtered because abstract_text contains publisher/retraction "
                        f"notice text ({filter_rule})"
                    )
                    writer.writerow({
                        "csv_line": csv_line,
                        "record_id": source_record_id,
                        "abstract_id": "",
                        "output_file": "",
                        "title": title,
                        "status": "filtered_notice_text",
                        "filter_rule": filter_rule,
                        "message": message,
                    })
                    filtered_writer.writerow({
                        "csv_line": csv_line,
                        "record_id": source_record_id,
                        "title": title,
                        "filter_rule": filter_rule,
                        "abstract_text": clean_cell(source_abstract, preserve_newlines=True),
                    })
                    continue

                try:
                    abstract_id, xml_bytes = build_article_xml(row, csv_line, args)
                    ET.fromstring(xml_bytes.split(b"\n", 2)[2])
                    validate_abstract_preservation(source_abstract, xml_bytes)
                    output_path = choose_output_path(args.output_dir, abstract_id, args.overwrite)
                    output_path.write_bytes(xml_bytes)
                    success_count += 1
                    writer.writerow({
                        "csv_line": csv_line,
                        "record_id": source_record_id,
                        "abstract_id": abstract_id,
                        "output_file": output_path.name,
                        "title": title,
                        "status": "created",
                        "filter_rule": "",
                        "message": "abstract-preservation check passed",
                    })
                except Exception as exc:
                    skipped_count += 1
                    writer.writerow({
                        "csv_line": csv_line,
                        "record_id": source_record_id,
                        "abstract_id": "",
                        "output_file": "",
                        "title": title,
                        "status": "skipped_error",
                        "filter_rule": "",
                        "message": str(exc),
                    })

    print(f"Created {success_count} XML file(s) in: {args.output_dir}")
    print(f"Filtered {filtered_count} notice-like abstract row(s).")
    print(f"Skipped {skipped_count} row(s) because of conversion errors.")
    print(f"Manifest: {manifest_path}")
    print(f"Filtered rows: {filtered_path}")
    return 0 if success_count else 1


def main() -> int:
    try:
        return convert(parse_args())
    except KeyboardInterrupt:
        print("Conversion cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())