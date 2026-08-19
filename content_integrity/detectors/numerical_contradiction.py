"""Sentence-local V1 checks, plus one lightweight cross-sentence pass: the most recently
stated population total ("120 patients were enrolled") is carried forward in document order
and used as the denominator for a later percentage/count claim that states no local
denominator, provided the population category matches (patient/participant/subject/case) and
the later sentence carries no qualifier (e.g. "evaluable", "responders", a named subgroup)
marking it as a different (sub)population. Everything else remains sentence-local.

Known limitation: exclusive subgroup counts are only compared against a total stated in the
same sentence. Falling back to a record-level enrollment total was evaluated and rejected: a
subgroup sentence without a local total carries no evidence that its groups partition the
enrolled population (in the ASCO corpus the exclusivity markers describe mutations and image
patches, not patients), and abstracts commonly report several patient counts, so no single one
can be taken as the denominator without guessing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from ..models import ParsedRecord
from ..template_matching_common import _sentence_split
from ..thresholds import NUMERICAL_CONTRADICTION_PERCENTAGE_TOLERANCE as PERCENTAGE_TOLERANCE
from ..utils import normalize_label, normalize_whitespace
NUMBER = r"-?\d+(?:\.\d+)?"
PERCENT_NUMBER = r"-?\d+(?:[.,]\d+)?"
POPULATION = r"patients?|participants?|subjects?|cases?"
UNIT = r"%|years?|months?|weeks?|days?|hours?|minutes?|mg|g|kg|ml|mm|cm"

COUNT_PERCENT_PATTERNS = (
    (
        re.compile(
            rf"\b(?P<numerator>\d+)\s+(?:of|out of)\s+(?P<denominator>\d+)"
            rf"(?:\s+(?P<unit>(?:evaluable\s+)?{POPULATION}))?\s*"
            rf"(?:\(|,?\s*(?:representing|corresponding to|reported as|responded|or|=)\s*\(?)?"
            rf"(?P<percentage>\d+(?:[.,]\d+)?)\s*%\)?",
            re.IGNORECASE,
        ),
        "count_of_denominator_with_percentage",
    ),
    (
        re.compile(
            rf"(?<![\d.])\b(?P<numerator>\d+)\s*/\s*(?P<denominator>\d+)"
            rf"(?![\d,]|\s*/)"
            rf"(?:\s+(?P<unit>{POPULATION}))?\s*"
            rf"(?:\(|,?\s*(?:representing|corresponding to|reported as|responded|or|=)\s*\(?)?"
            rf"(?P<percentage>\d+(?:[.,]\d+)?)\s*%\)?",
            re.IGNORECASE,
        ),
        "fraction_with_percentage",
    ),
    (
        re.compile(
            rf"\b(?P<percentage>\d+(?:[.,]\d+)?)\s*%\s*"
            rf"\(\s*(?P<numerator>\d+)\s*/\s*(?P<denominator>\d+)\s*\)",
            re.IGNORECASE,
        ),
        "percentage_with_fraction",
    ),
)
COUNT_PERCENT_WITHOUT_DENOMINATOR_RE = re.compile(
    rf"\b(?P<numerator>\d+)\s+(?P<unit>{POPULATION})\s*"
    rf"\(\s*(?P<percentage>\d+(?:[.,]\d+)?)\s*%\s*\)",
    re.IGNORECASE,
)
COUNT_OF_RE = re.compile(
    rf"\b(?P<numerator>\d+)\s+(?:of|out of)\s+(?P<denominator>\d+)(?![\d,])"
    rf"(?:\s+(?P<unit>(?:evaluable\s+)?{POPULATION}))?",
    re.IGNORECASE,
)
FRACTION_RE = re.compile(
    r"(?<![\d.])\b(?P<numerator>\d+)\s*/\s*(?P<denominator>\d+)"
    r"(?![\d,]|\s*/)"
)
OUTCOME_AMONG_RE = re.compile(
    rf"\b(?P<numerator>\d+)\s+"
    rf"(?P<label>(?:{POPULATION})\s+(?:with|who had)\s+[A-Za-z][A-Za-z -]{{0,30}}?|"
    r"serious adverse events?|adverse events?|hospitali[sz]ations?|episodes?|"
    r"responses?|responders?|events?|unique cases?|cases?|deaths?|toxicities?|"
    r"recurrences?|relapses?)\s+"
    rf"(?:among|in|of)\s+(?P<denominator>\d+)\s+"
    rf"(?P<unit>(?:evaluable\s+|treated\s+|enrolled\s+)?{POPULATION})\b",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(rf"(?<![\d.,])(?P<percentage>{PERCENT_NUMBER})\s*%")
RELATIVE_PERCENT_RE = re.compile(
    r"\b(?:relative|increase[sd]?|increasing|change[sd]?|improve(?:ment|d)?|"
    r"reduction|reduced|decreas(?:e|ed|ing)|higher|growth|rose|gain(?:ed)?|"
    r"dose intensity)\b",
    re.IGNORECASE,
)
RATE_CONTEXT_RE = re.compile(
    r"\b(?:rate|proportion|percentage|prevalence|incidence|response|responded|"
    r"patients?|participants?|subjects?|cases?|events?|toxicity|survival)\b",
    re.IGNORECASE,
)
COUNT_CONTEXT_RE = re.compile(
    rf"\b(?:{POPULATION}|responses?|responders?|events?|deaths?|toxicities?|"
    r"hospitali[sz]ations?|episodes?|evaluable|treated|enrolled)\b",
    re.IGNORECASE,
)
ONE_PER_PERSON_RE = re.compile(
    rf"\b(?:responders?|responses?|deaths?|unique cases?|recurrences?|relapses?|"
    rf"(?:{POPULATION})\s+with\b|(?:{POPULATION})\s+who\b|responded)\b",
    re.IGNORECASE,
)
REPEATABLE_EVENT_RE = re.compile(
    r"\b(?:serious adverse events?|adverse events?|events?|toxicities?|"
    r"hospitali[sz]ations?|episodes?|admissions?)\b",
    re.IGNORECASE,
)
MEDIAN_RANGE_RE = re.compile(
    rf"\bmedian(?:\s+(?P<label>[a-z][a-z -]{{0,24}}?))?\s*"
    rf"(?:was|of|=|:)?\s*(?P<value>{NUMBER})\s*(?P<median_unit>{UNIT})?\s*"
    rf"[,;(]?\s*(?:with\s+)?(?:an?\s+)?range\s*(?:of|was|=|:)?\s*"
    rf"(?P<lower>{NUMBER})\s*(?:-|–|—|to)\s*(?P<upper>{NUMBER})"
    rf"\s*(?P<range_unit>{UNIT})?",
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    rf"\b(?:(?P<level>\d+(?:\.\d+)?)\s*%\s*)?"
    rf"(?P<label>CI|confidence interval|range)\s*(?:of|was|=|:)?\s*"
    rf"(?P<lower>{NUMBER})\s*(?P<lower_unit>{UNIT})?\s*(?:-|–|—|to)\s*"
    rf"(?P<upper>{NUMBER})\s*(?P<upper_unit>{UNIT})?",
    re.IGNORECASE,
)
EXCLUSIVE_RE = re.compile(r"\b(?:mutually exclusive|non-overlapping|disjoint)\b", re.IGNORECASE)
TOTAL_KEYWORD_RE = re.compile(
    rf"\b(?:among|total(?:\s+of)?|included|enrolled)\s+"
    rf"(?P<total>\d+)\s+(?P<unit>(?:evaluable\s+)?{POPULATION})\b",
    re.IGNORECASE,
)
# "N patients were enrolled" -- the number precedes the population noun, with the
# enrollment verb trailing. Not covered by TOTAL_KEYWORD_RE (keyword-before-number only),
# but this is the phrasing the OE-09 audit example itself uses.
TOTAL_ENROLLED_RE = re.compile(
    rf"\b(?P<total>\d+)\s+(?P<unit>(?:evaluable\s+)?{POPULATION})\s+"
    rf"(?:were\s+|was\s+)?(?:enrolled|included)\b",
    re.IGNORECASE,
)
# Sentences carrying one of these qualifiers describe a (sub)population distinct from a
# plain enrollment/total count, so a total from one sentence must not be linked to -- or
# read out of -- a sentence that contains one of these words.
DIFFERENT_POPULATION_RE = re.compile(
    r"\b(?:evaluable|eligible|assessable|responders?|responding|survivors?|"
    r"non-responders?|per-protocol|completers?|subgroup|cohort|arm|subset)\b",
    re.IGNORECASE,
)
SUBGROUP_RE = re.compile(
    rf"\b(?P<count>\d+)\s+(?:(?:{POPULATION})\s+)?"
    r"(?:were\s+|had\s+|with\s+)?"
    r"(?P<label>[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z][A-Za-z0-9-]*){0,3}?)"
    r"(?=\s*(?:,|;|\band\b|[.]?\s*$))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NumericalClaim:
    record_id: str
    section: str
    sentence: str
    claim_type: str
    label: str = ""
    numerator: float | None = None
    denominator: float | None = None
    reported_percentage: float | None = None
    value: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    unit: str = ""
    extraction_method: str = ""
    outcome_classification: str = "ambiguous"
    # Non-empty only when the denominator was not stated in this claim's own sentence but
    # carried forward from an earlier population-total sentence (see OE-09); holds that
    # earlier sentence, for evidence and for excluding these claims from same-sentence-only
    # checks.
    denominator_source_sentence: str = ""


@dataclass(frozen=True, slots=True)
class NumericalContradictionFinding:
    """calculated_value is the derived percentage, subgroup sum, numerator, or valid boundary.

    It is null for median/range and reversed-interval contradictions.
    """

    finding_id: str
    check_type: str
    check_triggered: bool
    record_id: str
    source_file: str
    title: str
    contradiction_type: str
    evidence: str
    severity: str
    confidence: str
    review_reason: str
    section: str
    source_sentence: str
    reported_values: str
    calculated_value: float | None
    difference: float
    tolerance: float
    review_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float(value: str | None) -> float | None:
    return float(value.replace(",", ".")) if value is not None else None


def _unit(value: str | None) -> str:
    cleaned = normalize_whitespace(value).lower()
    return re.sub(r"s$", "", cleaned)


def _compatible_units(*values: str | None) -> bool:
    units = {_unit(value) for value in values if _unit(value)}
    return len(units) <= 1


def _find_total(text: str) -> re.Match[str] | None:
    matches = [m for m in (TOTAL_KEYWORD_RE.search(text), TOTAL_ENROLLED_RE.search(text)) if m]
    return min(matches, key=lambda m: m.start()) if matches else None


def _label_before(sentence: str, start: int) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z-]*", sentence[max(0, start - 50):start])
    return " ".join(words[-5:])


def _classify_outcome(label: str, sentence: str) -> str:
    if ONE_PER_PERSON_RE.search(label):
        return "one_per_person"
    if REPEATABLE_EVENT_RE.search(label):
        return "repeatable_event"
    if re.search(rf"\b(?:{POPULATION})\s+(?:with|who)\b", sentence, re.IGNORECASE):
        return "one_per_person"
    if REPEATABLE_EVENT_RE.search(sentence):
        return "repeatable_event"
    if ONE_PER_PERSON_RE.search(sentence):
        return "one_per_person"
    return "ambiguous"


def _section_sentences(record: ParsedRecord) -> list[tuple[str, str]]:
    if record.abstract_sections:
        return [
            (normalize_label(item.get("section", "")) or "Abstract", sentence)
            for item in record.abstract_sections
            for sentence in _sentence_split(item.get("text", ""))
        ]
    return [
        ("Abstract", sentence)
        for sentence in _sentence_split(record.abstract_text or record.raw_text)
    ]


def _claim(
    record: ParsedRecord,
    section: str,
    sentence: str,
    claim_type: str,
    method: str,
    **values: Any,
) -> NumericalClaim:
    return NumericalClaim(
        record_id=record.record_id,
        section=section,
        sentence=sentence,
        claim_type=claim_type,
        extraction_method=method,
        **values,
    )


def extract_numerical_claims(record: ParsedRecord) -> list[NumericalClaim]:
    claims: list[NumericalClaim] = []
    # Most recently stated population total, carried forward across sentences (OE-09).
    # Updated only after a sentence's own claims are extracted, so a total and a percentage
    # in the same sentence are never linked through this path -- that's already the
    # same-sentence checks' job.
    carried_total: float | None = None
    carried_unit = ""
    carried_sentence = ""
    for section, sentence in _section_sentences(record):
        for pattern, method in COUNT_PERCENT_PATTERNS:
            for match in pattern.finditer(sentence):
                label = _label_before(sentence, match.start())
                claims.append(_claim(
                    record, section, sentence, "count_percentage", method,
                    label=label,
                    numerator=_float(match.group("numerator")),
                    denominator=_float(match.group("denominator")),
                    reported_percentage=_float(match.group("percentage")),
                    unit=_unit(match.groupdict().get("unit")) or "patient",
                    outcome_classification=_classify_outcome(label, sentence),
                ))

        for match in COUNT_PERCENT_WITHOUT_DENOMINATOR_RE.finditer(sentence):
            label = _label_before(sentence, match.start())
            numerator = _float(match.group("numerator"))
            unit = _unit(match.group("unit"))
            claims.append(_claim(
                record, section, sentence, "count_percentage",
                "count_with_parenthetical_percentage",
                label=label,
                numerator=numerator,
                reported_percentage=_float(match.group("percentage")),
                unit=unit,
                outcome_classification=_classify_outcome(label, sentence),
            ))
            # This claim has no local denominator. If an earlier sentence stated a
            # population total of the same kind (patient/participant/subject/case), and
            # this sentence carries no qualifier marking it a different (sub)population,
            # link the two -- conservatively: no match at all if either check fails.
            if (
                carried_total is not None
                and unit.split()[-1] == carried_unit.split()[-1]
                and not DIFFERENT_POPULATION_RE.search(sentence)
            ):
                claims.append(_claim(
                    record, section, sentence, "count_percentage",
                    "carried_forward_denominator",
                    label=label,
                    numerator=numerator,
                    denominator=carried_total,
                    reported_percentage=_float(match.group("percentage")),
                    unit=unit,
                    outcome_classification=_classify_outcome(label, sentence),
                    denominator_source_sentence=carried_sentence,
                ))

        if COUNT_CONTEXT_RE.search(sentence):
            for pattern, method in (
                (COUNT_OF_RE, "count_of_denominator"),
                (FRACTION_RE, "count_fraction"),
                (OUTCOME_AMONG_RE, "outcome_count_among_population"),
            ):
                for match in pattern.finditer(sentence):
                    label = match.groupdict().get("label") or _label_before(sentence, match.start())
                    claims.append(_claim(
                        record, section, sentence, "count_denominator", method,
                        label=label,
                        numerator=_float(match.group("numerator")),
                        denominator=_float(match.group("denominator")),
                        unit=_unit(match.groupdict().get("unit")) or "patient",
                        outcome_classification=_classify_outcome(label, sentence),
                    ))

        for match in PERCENT_RE.finditer(sentence):
            # A hyphen between an alphanumeric token and a percentage is range
            # or label punctuation (25-50%, grade IV-5%), not a negative sign.
            if (
                match.group("percentage").startswith("-")
                and match.start() > 0
                and sentence[match.start()] in "-–—"
                and not sentence[match.start() - 1].isspace()
            ):
                continue
            # In malformed abstract prose, ``n = 570,25%`` means count 570
            # followed by 25%, not a decimal-comma percentage.
            if "," in match.group("percentage") and re.search(
                r"\bn\s*=\s*$", sentence[:match.start()], re.IGNORECASE
            ):
                continue
            claims.append(_claim(
                record, section, sentence, "percentage", "explicit_percentage",
                label=_label_before(sentence, match.start()),
                reported_percentage=_float(match.group("percentage")),
                unit="%",
                value=_float(match.group("percentage")),
            ))

        for match in MEDIAN_RANGE_RE.finditer(sentence):
            if _compatible_units(match.group("median_unit"), match.group("range_unit")):
                claims.append(_claim(
                    record, section, sentence, "median_range", "explicit_median_and_range",
                    label=normalize_whitespace(match.group("label")),
                    value=_float(match.group("value")),
                    lower_bound=_float(match.group("lower")),
                    upper_bound=_float(match.group("upper")),
                    unit=_unit(match.group("median_unit") or match.group("range_unit")),
                ))

        for match in RANGE_RE.finditer(sentence):
            if _compatible_units(match.group("lower_unit"), match.group("upper_unit")):
                claims.append(_claim(
                    record, section, sentence, "numeric_range", "explicit_range",
                    label=normalize_whitespace(match.group("label")),
                    lower_bound=_float(match.group("lower")),
                    upper_bound=_float(match.group("upper")),
                    unit=_unit(match.group("lower_unit") or match.group("upper_unit")),
                ))

        exclusive = EXCLUSIVE_RE.search(sentence)
        if exclusive and (total := _find_total(sentence[:exclusive.start()])):
            groups = [
                (float(match.group("count")), normalize_whitespace(match.group("label")))
                for match in SUBGROUP_RE.finditer(sentence[exclusive.end():])
            ]
            if len(groups) >= 2:
                claims.append(_claim(
                    record, section, sentence, "exclusive_subgroups",
                    "explicit_exclusive_subgroups",
                    label="; ".join(label for _, label in groups),
                    numerator=sum(count for count, _ in groups),
                    denominator=float(total.group("total")),
                    unit=_unit(total.group("unit")),
                ))

        # Update the carried-forward total *after* this sentence's own claims are built,
        # and only from a sentence that isn't itself qualifying a sub-population (e.g. "50
        # patients were evaluable") -- otherwise a subset count would leak forward as if it
        # were the whole enrolled population.
        total_match = _find_total(sentence)
        if total_match and not DIFFERENT_POPULATION_RE.search(sentence):
            carried_total = _float(total_match.group("total"))
            carried_unit = _unit(total_match.group("unit"))
            carried_sentence = sentence
    return claims


def _finding(
    record: ParsedRecord,
    claim: NumericalClaim,
    contradiction_type: str,
    evidence: str,
    severity: str,
    confidence: str,
    reported_values: str,
    calculated_value: float | None,
    difference: float,
    tolerance: float,
) -> NumericalContradictionFinding:
    return NumericalContradictionFinding(
        finding_id="",
        check_type="numerical_contradiction",
        check_triggered=True,
        record_id=record.record_id,
        source_file=record.source_file,
        title=record.title,
        contradiction_type=contradiction_type,
        evidence=evidence,
        severity=severity,
        confidence=confidence,
        review_reason=f"{evidence} Verify the values and linked population in the source data.",
        section=claim.section,
        source_sentence=claim.sentence,
        reported_values=reported_values,
        calculated_value=calculated_value,
        difference=round(difference, 3),
        tolerance=tolerance,
        review_status="candidate",
    )


def detect_numerical_contradictions(
    records: list[ParsedRecord],
    *,
    percentage_tolerance: float = PERCENTAGE_TOLERANCE,
) -> list[NumericalContradictionFinding]:
    if percentage_tolerance < 0:
        raise ValueError("percentage_tolerance must be non-negative")

    findings: list[NumericalContradictionFinding] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(record: ParsedRecord, claim: NumericalClaim, finding: NumericalContradictionFinding) -> None:
        key = (
            record.record_id,
            finding.contradiction_type,
            claim.sentence,
            finding.reported_values,
        )
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    for record in records:
        claims = extract_numerical_claims(record)
        invalid_count_percentages = {
            (claim.sentence, claim.numerator, claim.denominator)
            for claim in claims
            if (
                claim.claim_type == "count_percentage"
                and claim.reported_percentage is not None
                and not 0 <= claim.reported_percentage <= 100
            )
        }
        for claim in claims:
            if (
                claim.claim_type in {"count_percentage", "count_denominator"}
                and claim.numerator is not None
                and claim.denominator is not None
                and (claim.sentence, claim.numerator, claim.denominator)
                not in invalid_count_percentages
            ):
                if (
                    not claim.denominator_source_sentence
                    and claim.numerator > claim.denominator
                    and claim.outcome_classification == "one_per_person"
                ):
                    difference = claim.numerator - claim.denominator
                    add(record, claim, _finding(
                        record, claim, "numerator_exceeds_denominator",
                        f"The reported count {claim.numerator:g} exceeds the denominator {claim.denominator:g}.",
                        "high", "high",
                        f"numerator={claim.numerator:g}; denominator={claim.denominator:g}",
                        claim.numerator, difference, 0.0,
                    ))
                if claim.reported_percentage is not None and claim.denominator > 0:
                    calculated = claim.numerator / claim.denominator * 100
                    difference = abs(claim.reported_percentage - calculated)
                    if difference > percentage_tolerance and claim.denominator_source_sentence:
                        add(record, claim, _finding(
                            record, claim, "carried_forward_percentage_mismatch",
                            f"{claim.numerator:g} of {claim.denominator:g} calculates to "
                            f"{calculated:.1f}%, not the reported {claim.reported_percentage:g}%. "
                            f"The denominator {claim.denominator:g} is not stated in this sentence; "
                            f"it was carried forward from an earlier sentence: "
                            f"\"{claim.denominator_source_sentence}\"",
                            "medium", "medium",
                            f"{claim.numerator:g}/{claim.denominator:g} (denominator carried forward); "
                            f"reported_percentage={claim.reported_percentage:g}%",
                            round(calculated, 3), difference, percentage_tolerance,
                        ))
                    elif difference > percentage_tolerance:
                        add(record, claim, _finding(
                            record, claim, "count_percentage_mismatch",
                            f"{claim.numerator:g} of {claim.denominator:g} calculates to "
                            f"{calculated:.1f}%, not the reported {claim.reported_percentage:g}%.",
                            "medium", "very_high",
                            f"{claim.numerator:g}/{claim.denominator:g}; "
                            f"reported_percentage={claim.reported_percentage:g}%",
                            round(calculated, 3), difference, percentage_tolerance,
                        ))

            if (
                claim.claim_type == "percentage"
                and claim.reported_percentage is not None
                and not 0 <= claim.reported_percentage <= 100
            ):
                percent_match = re.search(
                    rf"{re.escape(f'{claim.reported_percentage:g}')}0*\s*%", claim.sentence
                )
                position = percent_match.start() if percent_match else len(claim.sentence)
                context = claim.sentence[:position + 30]
                if RATE_CONTEXT_RE.search(claim.sentence) and not RELATIVE_PERCENT_RE.search(context):
                    boundary = 0.0 if claim.reported_percentage < 0 else 100.0
                    direction = "below 0%" if claim.reported_percentage < 0 else "above 100%"
                    add(record, claim, _finding(
                        record, claim, "impossible_percentage",
                        f"The reported absolute rate or proportion is {claim.reported_percentage:g}%, "
                        f"which is {direction}.",
                        "high", "high",
                        f"reported_percentage={claim.reported_percentage:g}%",
                        boundary, abs(claim.reported_percentage - boundary), 0.0,
                    ))

            if (
                claim.claim_type == "median_range"
                and claim.value is not None
                and claim.lower_bound is not None
                and claim.upper_bound is not None
                and claim.lower_bound <= claim.upper_bound
                and not claim.lower_bound <= claim.value <= claim.upper_bound
            ):
                difference = min(
                    abs(claim.value - claim.lower_bound),
                    abs(claim.value - claim.upper_bound),
                )
                add(record, claim, _finding(
                    record, claim, "median_outside_range",
                    f"The reported median {claim.value:g} lies outside the explicit range "
                    f"{claim.lower_bound:g}–{claim.upper_bound:g} {claim.unit}.",
                    "medium", "very_high",
                    f"median={claim.value:g}; range={claim.lower_bound:g}–{claim.upper_bound:g}",
                    None, difference, 0.0,
                ))

            if (
                claim.claim_type == "numeric_range"
                and claim.lower_bound is not None
                and claim.upper_bound is not None
                and claim.lower_bound > claim.upper_bound
            ):
                add(record, claim, _finding(
                    record, claim, "reversed_interval",
                    f"The reported {claim.label or 'range'} runs from {claim.lower_bound:g} "
                    f"down to {claim.upper_bound:g}.",
                    "medium", "very_high",
                    f"lower_bound={claim.lower_bound:g}; upper_bound={claim.upper_bound:g}",
                    None, claim.lower_bound - claim.upper_bound, 0.0,
                ))

            if (
                claim.claim_type == "exclusive_subgroups"
                and claim.numerator is not None
                and claim.denominator is not None
                and claim.numerator > claim.denominator
            ):
                add(record, claim, _finding(
                    record, claim, "exclusive_subgroups_exceed_total",
                    f"Explicitly exclusive subgroup counts sum to {claim.numerator:g}, "
                    f"exceeding the reported total {claim.denominator:g}.",
                    "high", "high",
                    f"subgroup_sum={claim.numerator:g}; total={claim.denominator:g}",
                    claim.numerator, claim.numerator - claim.denominator, 0.0,
                ))

    return [
        replace(finding, finding_id=f"NUM-{index:05d}")
        for index, finding in enumerate(findings, start=1)
    ]
