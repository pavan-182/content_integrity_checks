"""Standalone trial-registration verification; no scientific or misconduct judgement."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ..models import ParsedRecord
from ..template_detection import TRIAL_PATTERN, _sentence_split
from ..utils import normalize_label, normalize_whitespace


CLINICAL_TRIALS_GOV = "ClinicalTrials.gov"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
CLINICAL_TRIALS_GOV_API = "https://clinicaltrials.gov/api/v2/studies"

NCT_REFERENCE_RE = re.compile(
    r"\bNCT\s*[-_:]?\s*(?P<body>\d[A-Za-z0-9]{0,11}|X{2,}|pending|TBD)\b",
    re.IGNORECASE,
)
OTHER_REGISTRY_RE = re.compile(
    r"\b(?P<prefix>ISRCTN|ACTRN|EUCTR|EudraCT|ChiCTR)"
    r"\s*[-_:]?\s*(?P<body>[A-Za-z0-9][A-Za-z0-9._/-]*)\b",
    re.IGNORECASE,
)
PLACEHOLDER_REGISTRATION_RE = re.compile(
    r"\b(?:trial\s+)?registration\s+(?:number|identifier)?\s*"
    r"(?:is|was|remains)?\s*(?:pending|TBD|to be added|to follow)\b",
    re.IGNORECASE,
)
REGISTRATION_WITHOUT_ID_RE = re.compile(
    r"\b(?:registered\s+(?:at|with|on)\s+ClinicalTrials\.gov|"
    r"(?:the\s+)?(?:study|trial)\s+(?:was|is|has been)\s+prospectively registered|"
    r"prospectively registered\s+(?:study|trial)|"
    r"ClinicalTrials\.gov\s+(?:identifier|registration number|number)\s+"
    r"(?:was\s+)?(?:not provided|unavailable|omitted)|"
    r"(?:trial\s+)?registration\s+(?:number|identifier)\s+"
    r"(?:was\s+)?(?:not provided|unavailable|omitted))\b",
    re.IGNORECASE,
)
REGISTRY_DATA_SOURCE_RE = re.compile(
    r"\b(?:data\s+sources?|source\s+data|landscape\s+analysis|"
    r"indexed\s+in|public\s+data)\b",
    re.IGNORECASE,
)
NCT_VALID_RE = re.compile(r"^NCT\d{8}$")
PLACEHOLDER_ID_RE = re.compile(r"(?:X{2,}|PENDING|TBD|TOBEADDED|TOFOLLOW)", re.IGNORECASE)

REGISTRY_NAMES = {
    "NCT": CLINICAL_TRIALS_GOV,
    "ISRCTN": "ISRCTN",
    "ACTRN": "ANZCTR",
    "EUCTR": "EU Clinical Trials Register",
    "EUDRACT": "EU Clinical Trials Register",
    "CHICTR": "Chinese Clinical Trial Registry",
}
REGISTRY_FORMAT_DESCRIPTIONS = {
    CLINICAL_TRIALS_GOV: "NCT followed by exactly eight digits",
    "ISRCTN": "ISRCTN followed by exactly eight digits",
    "ANZCTR": "ACTRN followed by the expected fourteen-digit registration number",
    "EU Clinical Trials Register": "a recognised EUCTR or EudraCT registration format",
    "Chinese Clinical Trial Registry": "a recognised ChiCTR registration format",
}


@dataclass(frozen=True, slots=True)
class TrialReferenceClaim:
    record_id: str
    source_file: str
    title: str
    section: str
    source_sentence: str
    matched_text: str
    raw_trial_id: str
    normalized_trial_id: str
    registry_name: str
    extraction_method: str
    format_valid: bool
    claim_type: str


@dataclass(frozen=True, slots=True)
class RegistryLookupResult:
    registry_name: str
    queried_trial_id: str
    lookup_status: str
    found: bool
    external_record_id: str = ""
    external_title: str = ""
    study_type: str = ""
    phase: str = ""
    recruitment_status: str = ""
    conditions: tuple[str, ...] = ()
    interventions: tuple[str, ...] = ()
    enrollment: int | None = None
    source_record_url: str = ""
    http_status: int | None = None
    error_type: str = ""
    error_message: str = ""
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class TrialVerificationResult:
    finding_id: str
    check_type: str
    check_triggered: bool
    evidence: str
    severity: str
    confidence: str
    matched_source_type: str
    matched_source_id: str
    review_reason: str
    record_id: str
    source_file: str
    title: str
    finding_type: str
    verification_status: str
    registry_name: str
    raw_trial_id: str
    normalized_trial_id: str
    format_valid: bool
    source_section: str
    source_sentence: str
    lookup_status: str
    external_record_id: str
    external_title: str
    external_study_type: str
    external_phase: str
    external_recruitment_status: str
    external_conditions: tuple[str, ...]
    external_interventions: tuple[str, ...]
    external_enrollment: int | None
    source_record_url: str
    cache_hit: bool
    operational_error: str
    review_status: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["external_conditions"] = list(self.external_conditions)
        data["external_interventions"] = list(self.external_interventions)
        return data


class RegistryClient(Protocol):
    def lookup(self, normalized_trial_id: str) -> RegistryLookupResult: ...


Transport = Callable[[str, float], tuple[int, bytes]]


def _canonical_registry_id(value: str, registry_name: str) -> str:
    canonical = normalize_whitespace(value).upper()
    if registry_name == CLINICAL_TRIALS_GOV:
        canonical = re.sub(r"[\s\-_:]", "", canonical)
    return canonical


def _failed_result(
    trial_id: str,
    registry_name: str,
    *,
    error_type: str,
    error_message: str,
    http_status: int | None = None,
) -> RegistryLookupResult:
    return RegistryLookupResult(
        registry_name=registry_name,
        queried_trial_id=trial_id,
        lookup_status="lookup_failed",
        found=False,
        http_status=http_status,
        error_type=error_type,
        error_message=error_message,
    )


def _validate_registry_result(
    result: Any,
    requested_trial_id: str,
    expected_registry_name: str,
) -> RegistryLookupResult:
    if not isinstance(result, RegistryLookupResult):
        return _failed_result(
            requested_trial_id,
            expected_registry_name,
            error_type="invalid_registry_client_result",
            error_message="Registry client did not return RegistryLookupResult.",
        )
    requested = _canonical_registry_id(requested_trial_id, expected_registry_name)
    queried = _canonical_registry_id(result.queried_trial_id, expected_registry_name)
    if result.registry_name != expected_registry_name or queried != requested:
        return _failed_result(
            requested_trial_id,
            expected_registry_name,
            error_type="invalid_registry_client_result",
            error_message="Registry name or queried identifier did not match the selected adapter.",
            http_status=result.http_status,
        )
    if result.lookup_status == "verified":
        if not result.found or not result.external_record_id:
            return _failed_result(
                requested_trial_id,
                expected_registry_name,
                error_type="invalid_registry_client_result",
                error_message="Verified registry result was missing required existence fields.",
                http_status=result.http_status,
            )
        external = _canonical_registry_id(result.external_record_id, expected_registry_name)
        if external != requested:
            return _failed_result(
                requested_trial_id,
                expected_registry_name,
                error_type="registry_identifier_mismatch",
                error_message=(
                    f"Registry returned {result.external_record_id} for requested "
                    f"{requested_trial_id}."
                ),
                http_status=result.http_status,
            )
        return replace(result, external_record_id=external)
    if result.lookup_status == "not_found":
        if result.found or result.external_record_id:
            return _failed_result(
                requested_trial_id,
                expected_registry_name,
                error_type="invalid_registry_client_result",
                error_message="Not-found registry result contained contradictory existence fields.",
                http_status=result.http_status,
            )
        return result
    if result.lookup_status == "lookup_failed":
        if result.found or result.external_record_id:
            return _failed_result(
                requested_trial_id,
                expected_registry_name,
                error_type="invalid_registry_client_result",
                error_message="Failed registry result exposed a matched external record.",
                http_status=result.http_status,
            )
        return replace(
            result,
            external_record_id="",
            external_title="",
            study_type="",
            phase="",
            recruitment_status="",
            conditions=(),
            interventions=(),
            enrollment=None,
            source_record_url="",
        )
    return _failed_result(
        requested_trial_id,
        expected_registry_name,
        error_type="invalid_registry_client_result",
        error_message=f"Unsupported registry lookup status: {result.lookup_status}.",
        http_status=result.http_status,
    )


def _default_transport(url: str, timeout_seconds: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "asco-integrity-screen/1"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.status, response.read()


class ClinicalTrialsGovClient:
    """Authoritative ClinicalTrials.gov API v2 adapter with persistent JSON caching."""

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        offline_cache_only: bool = False,
        max_cache_age_seconds: float | None = None,
        transport: Transport = _default_transport,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if max_cache_age_seconds is not None and max_cache_age_seconds < 0:
            raise ValueError("max_cache_age_seconds must be non-negative")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.offline_cache_only = offline_cache_only
        self.max_cache_age_seconds = max_cache_age_seconds
        self.transport = transport
        self.sleep = sleep

    def _cache_path(self, trial_id: str) -> Path | None:
        return self.cache_dir / f"{trial_id}.json" if self.cache_dir is not None else None

    def _read_cache(
        self,
        trial_id: str,
    ) -> tuple[RegistryLookupResult | None, str, str]:
        path = self._cache_path(trial_id)
        if path is None:
            return None, "", ""
        try:
            if not path.is_file():
                return None, "", ""
            if (
                self.max_cache_age_seconds is not None
                and time.time() - path.stat().st_mtime > self.max_cache_age_seconds
            ):
                return (
                    None,
                    "expired_cache_entry",
                    "Cached registry response exceeded the configured maximum age.",
                )
            data = json.loads(path.read_text(encoding="utf-8"))
            data["conditions"] = tuple(data.get("conditions", ()))
            data["interventions"] = tuple(data.get("interventions", ()))
            raw_result = RegistryLookupResult(**data)
            if raw_result.lookup_status not in {"verified", "not_found"}:
                raise ValueError("cache contained a non-cacheable lookup status")
            validated = _validate_registry_result(
                raw_result,
                trial_id,
                CLINICAL_TRIALS_GOV,
            )
            if validated.lookup_status == "lookup_failed":
                raise ValueError(validated.error_message)
            return replace(validated, cache_hit=True), "", ""
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return (
                None,
                "invalid_cache_entry",
                f"Cached registry response was rejected: {normalize_whitespace(str(exc))}",
            )

    def _write_cache(self, result: RegistryLookupResult) -> None:
        path = self._cache_path(result.queried_trial_id)
        if path is None or result.lookup_status not in {"verified", "not_found"}:
            return
        temporary_name = ""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(asdict(result), ensure_ascii=False, sort_keys=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                handle.write(payload)
                temporary_name = handle.name
            os.replace(temporary_name, path)
        except (OSError, TypeError, ValueError):
            return
        finally:
            try:
                if temporary_name and os.path.exists(temporary_name):
                    os.unlink(temporary_name)
            except OSError:
                pass

    @staticmethod
    def _failed(
        trial_id: str,
        *,
        error_type: str,
        error_message: str,
        http_status: int | None = None,
    ) -> RegistryLookupResult:
        return _failed_result(
            trial_id,
            CLINICAL_TRIALS_GOV,
            error_type=error_type,
            error_message=error_message,
            http_status=http_status,
        )

    @staticmethod
    def _not_found(trial_id: str, http_status: int = 404) -> RegistryLookupResult:
        return RegistryLookupResult(
            registry_name=CLINICAL_TRIALS_GOV,
            queried_trial_id=trial_id,
            lookup_status="not_found",
            found=False,
            http_status=http_status,
        )

    @staticmethod
    def _parse_record(trial_id: str, payload: dict[str, Any], status: int) -> RegistryLookupResult:
        protocol = payload.get("protocolSection")
        if not isinstance(protocol, dict):
            raise ValueError("response has no protocolSection object")
        identification = protocol.get("identificationModule", {})
        design = protocol.get("designModule", {})
        status_module = protocol.get("statusModule", {})
        conditions_module = protocol.get("conditionsModule", {})
        arms_module = protocol.get("armsInterventionsModule", {})
        external_id = normalize_whitespace(str(identification.get("nctId", "")))
        if not external_id:
            raise ValueError("response has no NCT identifier")
        interventions = tuple(
            normalize_whitespace(str(item.get("name", "")))
            for item in arms_module.get("interventions", [])
            if isinstance(item, dict) and normalize_whitespace(str(item.get("name", "")))
        )
        enrollment = design.get("enrollmentInfo", {}).get("count")
        return RegistryLookupResult(
            registry_name=CLINICAL_TRIALS_GOV,
            queried_trial_id=trial_id,
            lookup_status="verified",
            found=True,
            external_record_id=external_id,
            external_title=normalize_whitespace(
                str(identification.get("officialTitle") or identification.get("briefTitle") or "")
            ),
            study_type=normalize_whitespace(str(design.get("studyType", ""))),
            phase=" | ".join(str(value) for value in design.get("phases", []) if value),
            recruitment_status=normalize_whitespace(str(status_module.get("overallStatus", ""))),
            conditions=tuple(
                normalize_whitespace(str(value))
                for value in conditions_module.get("conditions", [])
                if normalize_whitespace(str(value))
            ),
            interventions=interventions,
            enrollment=enrollment if isinstance(enrollment, int) else None,
            source_record_url=f"https://clinicaltrials.gov/study/{external_id}",
            http_status=status,
        )

    def lookup(self, normalized_trial_id: str) -> RegistryLookupResult:
        cached, cache_error_type, cache_error_message = self._read_cache(
            normalized_trial_id
        )
        if cached is not None:
            return cached
        if self.offline_cache_only:
            return self._failed(
                normalized_trial_id,
                error_type=cache_error_type or "offline_cache_miss",
                error_message=cache_error_message
                or "No cached authoritative registry response is available.",
            )

        url = f"{CLINICAL_TRIALS_GOV_API}/{normalized_trial_id}"
        for attempt in range(self.max_retries + 1):
            try:
                status, raw = self.transport(url, self.timeout_seconds)
                if status == 404:
                    result = self._not_found(normalized_trial_id, status)
                    self._write_cache(result)
                    return result
                if status == 429 or status >= 500:
                    if attempt < self.max_retries:
                        self.sleep(2**attempt)
                        continue
                    return self._failed(
                        normalized_trial_id,
                        error_type="retry_exhausted",
                        error_message=f"Registry request failed after retries with HTTP {status}.",
                        http_status=status,
                    )
                if status >= 400:
                    return self._failed(
                        normalized_trial_id,
                        error_type="http_error",
                        error_message=f"Registry request failed with HTTP {status}.",
                        http_status=status,
                    )
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("registry response was not a JSON object")
                result = self._parse_record(normalized_trial_id, payload, status)
                result = _validate_registry_result(
                    result,
                    normalized_trial_id,
                    CLINICAL_TRIALS_GOV,
                )
                self._write_cache(result)
                return result
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    result = self._not_found(normalized_trial_id)
                    self._write_cache(result)
                    return result
                if (exc.code == 429 or exc.code >= 500) and attempt < self.max_retries:
                    self.sleep(2**attempt)
                    continue
                return self._failed(
                    normalized_trial_id,
                    error_type="retry_exhausted" if exc.code == 429 or exc.code >= 500 else "http_error",
                    error_message=f"Registry request failed with HTTP {exc.code}.",
                    http_status=exc.code,
                )
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                if attempt < self.max_retries:
                    self.sleep(2**attempt)
                    continue
                return self._failed(
                    normalized_trial_id,
                    error_type="network_error",
                    error_message=type(exc).__name__,
                )
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
                return self._failed(
                    normalized_trial_id,
                    error_type="invalid_response",
                    error_message=normalize_whitespace(str(exc)),
                )
        return self._failed(
            normalized_trial_id,
            error_type="retry_exhausted",
            error_message="Registry request failed after retries.",
        )


def _registry_name(prefix: str) -> str:
    return REGISTRY_NAMES[prefix.upper()]


def _normalize_trial_id(prefix: str, body: str) -> str:
    normalized_prefix = prefix.upper()
    normalized_body = re.sub(r"\s+", "", body).upper()
    if normalized_prefix == "NCT":
        normalized_body = re.sub(r"[-_:]", "", normalized_body)
    return f"{normalized_prefix}{normalized_body}"


def _format_valid(prefix: str, normalized_trial_id: str) -> bool:
    if prefix.upper() == "NCT":
        return bool(NCT_VALID_RE.fullmatch(normalized_trial_id))
    patterns = {
        "ISRCTN": r"ISRCTN\d{8}",
        "ACTRN": r"ACTRN\d{14}",
        "EUCTR": r"EUCTR\d{4}-\d{6}-\d{2}",
        "EUDRACT": r"EUDRACT\d{4}-\d{6}-\d{2}",
        "CHICTR": r"CHICTR[A-Z0-9-]{6,}",
    }
    return bool(re.fullmatch(patterns[prefix.upper()], normalized_trial_id, re.IGNORECASE))


def _section_sentences(record: ParsedRecord) -> list[tuple[str, str]]:
    items = [("Title", record.title)]
    if record.abstract_sections:
        items.extend(
            (normalize_label(item.get("section", "")) or "Abstract", item.get("text", ""))
            for item in record.abstract_sections
        )
    else:
        items.append(("Abstract", record.abstract_text or record.raw_text))
    seen: set[tuple[str, str]] = set()
    output: list[tuple[str, str]] = []
    for section, text in items:
        for sentence in _sentence_split(text):
            key = section, sentence
            if key not in seen:
                seen.add(key)
                output.append(key)
    return output


def _id_claim(
    record: ParsedRecord,
    section: str,
    sentence: str,
    matched_text: str,
    prefix: str,
    body: str,
) -> TrialReferenceClaim:
    normalized = _normalize_trial_id(prefix, body)
    placeholder = bool(PLACEHOLDER_ID_RE.search(normalized))
    return TrialReferenceClaim(
        record_id=record.record_id,
        source_file=record.source_file,
        title=record.title,
        section=section,
        source_sentence=sentence,
        matched_text=matched_text,
        raw_trial_id=matched_text,
        normalized_trial_id=normalized,
        registry_name=_registry_name(prefix),
        extraction_method="registry_identifier_regex",
        format_valid=not placeholder and _format_valid(prefix, normalized),
        claim_type="placeholder_trial_id" if placeholder else "explicit_trial_id",
    )


def extract_trial_reference_claims(record: ParsedRecord) -> list[TrialReferenceClaim]:
    claims: list[TrialReferenceClaim] = []
    for section, sentence in _section_sentences(record):
        sentence_claims: list[TrialReferenceClaim] = []
        occupied: list[tuple[int, int]] = []
        for pattern in (NCT_REFERENCE_RE, OTHER_REGISTRY_RE):
            for match in pattern.finditer(sentence):
                if any(match.start() < end and start < match.end() for start, end in occupied):
                    continue
                prefix = "NCT" if pattern is NCT_REFERENCE_RE else match.group("prefix")
                sentence_claims.append(
                    _id_claim(
                        record, section, sentence, match.group(0), prefix, match.group("body")
                    )
                )
                occupied.append(match.span())

        # Reuse the repository's broader prefix matcher without duplicating matched spans.
        for match in TRIAL_PATTERN.finditer(sentence):
            if any(match.start() < end and start < match.end() for start, end in occupied):
                continue
            prefix_match = re.match(
                r"(?P<prefix>NCT|ISRCTN|ACTRN|EUCTR|EudraCT|ChiCTR)(?P<body>.+)",
                match.group(0),
                re.IGNORECASE,
            )
            if prefix_match:
                if prefix_match.group("prefix").upper() == "NCT":
                    continue
                sentence_claims.append(
                    _id_claim(
                        record,
                        section,
                        sentence,
                        match.group(0),
                        prefix_match.group("prefix"),
                        prefix_match.group("body"),
                    )
                )
                occupied.append(match.span())

        claims.extend(sentence_claims)
        if sentence_claims:
            continue
        if match := PLACEHOLDER_REGISTRATION_RE.search(sentence):
            claims.append(TrialReferenceClaim(
                record.record_id,
                record.source_file,
                record.title,
                section,
                sentence,
                match.group(0),
                match.group(0),
                "",
                "unspecified_registry",
                "explicit_registration_placeholder",
                False,
                "placeholder_trial_id",
            ))
        elif match := REGISTRATION_WITHOUT_ID_RE.search(sentence):
            if REGISTRY_DATA_SOURCE_RE.search(sentence):
                continue
            registry = (
                CLINICAL_TRIALS_GOV
                if "clinicaltrials.gov" in match.group(0).lower()
                else "unspecified_registry"
            )
            claims.append(TrialReferenceClaim(
                record.record_id,
                record.source_file,
                record.title,
                section,
                sentence,
                match.group(0),
                "",
                "",
                registry,
                "explicit_registration_phrase",
                False,
                "registration_claim_without_id",
            ))
    return claims


def _deduplicate_claims(claims: list[TrialReferenceClaim]) -> list[TrialReferenceClaim]:
    grouped: dict[tuple[str, str, str], list[TrialReferenceClaim]] = {}
    for claim in claims:
        identity = claim.normalized_trial_id or claim.claim_type
        grouped.setdefault((claim.record_id, claim.registry_name, identity), []).append(claim)
    output: list[TrialReferenceClaim] = []
    for group in grouped.values():
        first = group[0]
        output.append(replace(
            first,
            section=" | ".join(dict.fromkeys(item.section for item in group)),
            source_sentence=" | ".join(dict.fromkeys(item.source_sentence for item in group)),
            matched_text=" | ".join(dict.fromkeys(item.matched_text for item in group)),
        ))
    return output


def _local_lookup(claim: TrialReferenceClaim) -> RegistryLookupResult:
    if claim.claim_type == "placeholder_trial_id":
        status = "placeholder_id"
    elif claim.claim_type == "registration_claim_without_id":
        status = "registration_claim_without_id"
    elif not claim.format_valid:
        status = "invalid_format"
    else:
        status = "unsupported_registry"
    return RegistryLookupResult(
        registry_name=claim.registry_name,
        queried_trial_id=claim.normalized_trial_id,
        lookup_status=status,
        found=False,
    )


def _lookup_unique_ids(
    claims: list[TrialReferenceClaim],
    clients: Mapping[str, RegistryClient],
) -> dict[tuple[str, str], RegistryLookupResult]:
    results: dict[tuple[str, str], RegistryLookupResult] = {}
    for claim in claims:
        key = (
            claim.registry_name,
            claim.normalized_trial_id or claim.claim_type,
        )
        if key in results:
            continue
        if (
            claim.claim_type != "explicit_trial_id"
            or not claim.format_valid
            or claim.registry_name not in clients
        ):
            results[key] = _local_lookup(claim)
            continue
        try:
            raw_result = clients[claim.registry_name].lookup(claim.normalized_trial_id)
            results[key] = _validate_registry_result(
                raw_result,
                claim.normalized_trial_id,
                claim.registry_name,
            )
        except Exception as exc:  # Registry adapters are an operational trust boundary.
            results[key] = _failed_result(
                claim.normalized_trial_id,
                claim.registry_name,
                error_type="client_error",
                error_message=type(exc).__name__,
            )
    return results


def _classification(
    claim: TrialReferenceClaim,
    lookup: RegistryLookupResult,
) -> tuple[bool, str, str, str, str, str]:
    status = lookup.lookup_status
    if status == "verified":
        return (
            False,
            "trial_verified",
            "low",
            "very_high",
            f"{claim.normalized_trial_id} was verified in {lookup.registry_name}.",
            "No human review is required for registry existence.",
        )
    if status == "not_found":
        return (
            True,
            "trial_not_found",
            "high",
            "very_high",
            f"The abstract reports {claim.normalized_trial_id}, but a completed "
            f"{lookup.registry_name} lookup returned no matching registry record.",
            "Confirm the reported identifier and registration record before publication.",
        )
    if status == "invalid_format":
        expected_format = REGISTRY_FORMAT_DESCRIPTIONS.get(
            claim.registry_name,
            "the recognised registry-specific identifier format",
        )
        return (
            True,
            "invalid_trial_id_format",
            "medium",
            "very_high",
            f"The abstract reports {claim.raw_trial_id} for {claim.registry_name}, which "
            f"does not match the expected format: {expected_format}.",
            f"Check the {claim.registry_name} registration identifier and correct its format.",
        )
    if status == "registration_claim_without_id":
        return (
            True,
            "registration_claim_without_id",
            "medium",
            "high",
            f'The abstract states "{claim.matched_text}" but provides no usable registry identifier.',
            "Request the claimed registration identifier for manual verification.",
        )
    if status == "placeholder_id":
        return (
            True,
            "placeholder_trial_id",
            "medium",
            "very_high",
            f'The abstract contains the placeholder registration reference "{claim.matched_text}".',
            "Replace the placeholder with the final registration identifier.",
        )
    if status == "unsupported_registry":
        return (
            True,
            "unsupported_registry_manual_verification",
            "low",
            "high",
            f"The abstract reports {claim.raw_trial_id} from {claim.registry_name}; this V1 "
            "detector has no authoritative automated adapter for that registry.",
            "Verify the identifier manually; this result does not mean that the trial is absent.",
        )
    return (
        False,
        "registry_lookup_failed",
        "low",
        "low",
        f"The lookup for {claim.normalized_trial_id} could not be completed.",
        "Retry the authoritative registry lookup; this is an operational failure, not an integrity finding.",
    )


def detect_unverifiable_trials(
    records: list[ParsedRecord],
    *,
    registry_clients: Mapping[str, RegistryClient] | None = None,
) -> list[TrialVerificationResult]:
    claims = _deduplicate_claims([
        claim
        for record in records
        for claim in extract_trial_reference_claims(record)
    ])
    clients = (
        dict(registry_clients)
        if registry_clients is not None
        else {CLINICAL_TRIALS_GOV: ClinicalTrialsGovClient()}
    )
    lookups = _lookup_unique_ids(claims, clients)
    output: list[TrialVerificationResult] = []
    for claim in claims:
        lookup = lookups[
            claim.registry_name,
            claim.normalized_trial_id or claim.claim_type,
        ]
        triggered, finding_type, severity, confidence, evidence, reason = _classification(
            claim, lookup
        )
        verified = lookup.lookup_status == "verified" and lookup.found
        operational_error = ": ".join(
            value for value in (lookup.error_type, lookup.error_message) if value
        )
        output.append(TrialVerificationResult(
            finding_id=f"TRV-{len(output) + 1:05d}",
            check_type="unverifiable_clinical_trial",
            check_triggered=triggered,
            evidence=evidence,
            severity=severity,
            confidence=confidence,
            matched_source_type="clinical_trial_registry" if verified else "none",
            matched_source_id=lookup.external_record_id if verified else "",
            review_reason=reason,
            record_id=claim.record_id,
            source_file=claim.source_file,
            title=claim.title,
            finding_type=finding_type,
            verification_status=lookup.lookup_status,
            registry_name=claim.registry_name,
            raw_trial_id=claim.raw_trial_id,
            normalized_trial_id=claim.normalized_trial_id,
            format_valid=claim.format_valid,
            source_section=claim.section,
            source_sentence=claim.source_sentence,
            lookup_status=lookup.lookup_status,
            external_record_id=lookup.external_record_id,
            external_title=lookup.external_title,
            external_study_type=lookup.study_type,
            external_phase=lookup.phase,
            external_recruitment_status=lookup.recruitment_status,
            external_conditions=lookup.conditions,
            external_interventions=lookup.interventions,
            external_enrollment=lookup.enrollment,
            source_record_url=lookup.source_record_url,
            cache_hit=lookup.cache_hit,
            operational_error=operational_error,
            review_status="candidate" if triggered else "not_triggered",
        ))
    return output
