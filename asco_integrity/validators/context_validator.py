from __future__ import annotations

import json
import os
import ssl
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from ..models import Finding, ParsedRecord, ValidationResult
from ..utils import normalize_whitespace


PROMPT_VERSION = "context_validator_v1"
MODEL_ID = "gpt-oss-20b"
DEFAULT_MODEL_NAME = "prod/gpt-oss-20b"
DEFAULT_BASE_URL = "https://intellihub.tnq.co.in/llm_gateway/api/v1"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0
# The validator model is reasoning-heavy, so leave enough budget for the final JSON answer.
VALIDATION_MAX_TOKENS = 2048

SYSTEM_PROMPT = (
    "You are checking whether an automatically flagged phrase in a scientific abstract is a "
    "genuine content-integrity concern or a false positive. You are given the matched phrase, "
    "what the rule-based system expected it to be a substitution for (if applicable), the "
    "surrounding sentence, and which section it came from.\n\n"
    "Decide:\n"
    '- "confirmed": the flag is a plausible integrity concern - the phrase reads as an odd or '
    "distorted substitution, or as genuine leftover chatbot/AI-assistant text.\n"
    '- "rejected": the flag is very likely a false positive - e.g. a proper noun, a standard '
    "domain term that only superficially overlaps the pattern, an implausible synonym pairing, "
    "or coincidental phrasing with no plausible link to AI-generated residue.\n"
    '- "uncertain": you cannot confidently decide either way from the given context.\n\n'
    'Respond with strict JSON only: {"status": "...", "reason": "..."}\n'
    "The reason must be one plain sentence an editor with no technical background can read directly.\n"
    'Do not use the words "hallucination", "token", or "embedding".'
)


class GPTOSSClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str: ...


def _normalize_env_value(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def _load_dotenv_file(path: str | Path) -> None:
    resolved = Path(path)
    if not resolved.exists():
        return
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _normalize_env_value(value)


def _load_validator_settings(env_file: str | Path = ".env") -> dict[str, str]:
    _load_dotenv_file(env_file)
    settings = {
        "api_key": os.getenv("INTELLIHUB_API_KEY") or os.getenv("api_key", ""),
        "base_url": os.getenv("INTELLIHUB_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        "model_name": os.getenv("INTELLIHUB_MODEL", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME,
        "verify_ssl": os.getenv("INTELLIHUB_VERIFY_SSL", "true").lower(),
        "ca_bundle": os.getenv("INTELLIHUB_CA_BUNDLE", "").strip(),
    }
    return settings


def _ssl_context(verify_ssl: str, ca_bundle: str) -> ssl.SSLContext | None:
    if verify_ssl in {"0", "false", "no"}:
        return ssl._create_unverified_context()
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    return ssl.create_default_context()


def _extract_response_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Validator response did not include choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise RuntimeError("Validator response choice was not an object")

    message = first_choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = first_choice.get("text")

    if isinstance(content, str):
        cleaned = content.strip()
        if cleaned:
            return cleaned
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    pieces.append(item["text"])
                elif isinstance(item.get("content"), str):
                    pieces.append(item["content"])
            elif isinstance(item, str):
                pieces.append(item)
        cleaned = "".join(pieces).strip()
        if cleaned:
            return cleaned
    raise RuntimeError("Validator response did not include message content")


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return cleaned


def _first_balanced_json_object(text: str) -> str:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return ""


def _validator_payload_candidates(raw: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        cleaned = candidate.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            candidates.append(cleaned)

    stripped = raw.strip()
    add(raw)
    add(stripped)
    fenced = _strip_json_fence(raw)
    add(fenced)
    add(_first_balanced_json_object(raw))
    add(_first_balanced_json_object(stripped))
    add(_first_balanced_json_object(fenced))
    return candidates


def _parse_validator_payload(raw: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for candidate in _validator_payload_candidates(raw):
        try:
            parsed: Any = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(parsed, str):
            nested = parsed.strip()
            if nested and nested != candidate:
                try:
                    parsed = json.loads(nested)
                except json.JSONDecodeError as exc:
                    last_error = exc
                    continue
        if isinstance(parsed, dict):
            return parsed
        last_error = ValueError("validator payload was not a JSON object")
    raise ValueError("validator response did not contain a JSON object") from last_error


class DisabledGPTOSSClient:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str:
        raise RuntimeError(self.reason)


class IntelliHubGPTOSSClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model_name: str = DEFAULT_MODEL_NAME,
        verify_ssl: str = "true",
        ca_bundle: str = "",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.verify_ssl = verify_ssl
        self.ca_bundle = ca_bundle
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds

    def _request(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-litellm-api-key": self.api_key,
        }
        context = _ssl_context(self.verify_ssl, self.ca_bundle)
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                request = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(request, timeout=self.timeout_seconds, context=context) as response:
                    raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError("Validator response was not a JSON object")
                return parsed
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt == self.max_attempts:
                    raise RuntimeError(f"Validator request failed with HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if attempt == self.max_attempts:
                    raise RuntimeError("Validator request failed") from exc
            if attempt < self.max_attempts:
                time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        if last_error is not None:
            raise RuntimeError("Validator request failed") from last_error
        raise RuntimeError("Validator request failed")

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        response = self._request("/chat/completions", payload)
        return _extract_response_content(response)


def build_gpt_oss_client(env_file: str | Path = ".env") -> GPTOSSClient:
    settings = _load_validator_settings(env_file)
    api_key = settings["api_key"]
    if not api_key:
        return DisabledGPTOSSClient(
            "GPT-OSS validator is not configured. Set INTELLIHUB_API_KEY or api_key in .env."
        )
    return IntelliHubGPTOSSClient(
        api_key=api_key,
        base_url=settings["base_url"],
        model_name=settings["model_name"],
        verify_ssl=settings["verify_ssl"],
        ca_bundle=settings["ca_bundle"],
    )


class ContextValidator:
    applies_to = {"tortured_phrase", "llm_response_trace"}

    def __init__(
        self,
        client: GPTOSSClient,
        *,
        model_id: str = MODEL_ID,
        prompt_version: str = PROMPT_VERSION,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.model_id = model_id
        self.prompt_version = prompt_version
        self.system_prompt = system_prompt

    def validate(self, finding: Finding, record: ParsedRecord) -> ValidationResult:
        if finding.detector_type not in self.applies_to:
            raise ValueError(f"ContextValidator does not apply to {finding.detector_type}")

        user_payload = {
            "matched_text": finding.matched_text,
            "expected_term": finding.expected_term,
            "evidence_snippet": finding.evidence_snippet,
            "section_or_field": finding.section_or_field,
            "detector_type": finding.detector_type,
        }

        try:
            raw = self.client.complete(
                system=self.system_prompt,
                user=json.dumps(user_payload, ensure_ascii=False),
                max_tokens=VALIDATION_MAX_TOKENS,
                temperature=0,
            )
            parsed = _parse_validator_payload(raw)
            status = normalize_whitespace(str(parsed["status"])).lower()
            reason = normalize_whitespace(str(parsed["reason"]))
            if status not in {"confirmed", "rejected", "uncertain"} or not reason:
                raise ValueError("validator payload missing required fields")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            status = "uncertain"
            reason = "Validator response could not be parsed."

        return ValidationResult(
            finding_id=finding.finding_id,
            status=status,
            reason=reason,
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )

