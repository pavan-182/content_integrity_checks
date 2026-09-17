"""Deterministic GPT-OSS gateway test double for load, failure-injection, and recovery tests.

It serves the OpenAI-compatible `/chat/completions` endpoint the real client calls, so runs
exercise the production transport, retries, timeouts, cache, and concurrency limits. Replies are
schema-valid and deterministic for each call site (identified by the prompt version in the
system message). It never logs request content.

This double proves pipeline control flow under repeatable responses and failures. It does not
measure real GPT-OSS latency, throughput, quality, or service capacity.

Failure injection is seeded by request content and the per-content occurrence number, so a
retry of the same request can succeed and a rerun reproduces the same sequence:
    --failure-rate 0.05 --failure-modes 429,500,503,timeout,reset,malformed

Usage (serves until interrupted, printing the base URL):
    python scripts/fake_gpt_oss_gateway.py --port 8765 --latency-ms 50 --max-concurrency 4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_KEY = "fake-gateway-key"
MODEL = "fake/gpt-oss-test-double"
FAILURE_MODES = ("429", "500", "503", "timeout", "reset", "malformed")
_GENE_RE = re.compile(r"\b(?:EGFR|ALK|KRAS|BRAF|HER2|BRCA[12]|PIK3CA|TP53|MET|RET|ROS1|NTRK|PD-L1)\b")
_DRUG_RE = re.compile(r"\b[a-z]{4,}(?:mab|nib|parib|ciclib|lutamide)\b")


@dataclass
class GatewayBehaviour:
    latency_seconds: float = 0.0
    failure_rate: float = 0.0
    failure_modes: tuple[str, ...] = ("429", "500", "503")
    # Attempts beyond this many simultaneous requests receive HTTP 429 (a quota the client must respect).
    max_concurrency: int | None = None
    # Sleep used by the "timeout" mode; must exceed the client's read timeout under test.
    hang_seconds: float = 5.0
    # Fail every request with this status (for example 401) regardless of rate.
    force_status: int | None = None
    # Deterministic tests: the first N occurrences of each distinct request fail, cycling modes.
    fail_first: int = 0
    # When set, injected failures apply only to requests whose body contains this marker.
    failure_marker: str | None = None
    retry_after: str = "0"


@dataclass
class GatewayCounters:
    lock: threading.Lock = field(default_factory=threading.Lock)
    outcomes: Counter = field(default_factory=Counter)
    call_sites: Counter = field(default_factory=Counter)
    occurrences: Counter = field(default_factory=Counter)
    # Counted on arrival, so a client that has already given up (timeout) is still visible.
    received: int = 0
    in_flight: int = 0
    max_in_flight: int = 0

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "requests": self.received,
                "outcomes": dict(sorted(self.outcomes.items())),
                "call_sites": dict(sorted(self.call_sites.items())),
                "max_in_flight": self.max_in_flight,
            }


def _call_site(system: str) -> str:
    match = re.search(r"Prompt version: (\S+)", system)
    return match.group(1) if match else "unknown"


def reply_content(system: str, user: str) -> str:
    """Schema-valid, deterministic model content for each pipeline call site."""
    site = _call_site(system)
    if site.startswith("entity_extraction"):
        entities = [{"text": value, "type": "gene"} for value in dict.fromkeys(_GENE_RE.findall(user))]
        entities += [{"text": value, "type": "drug"} for value in dict.fromkeys(_DRUG_RE.findall(user))]
        return json.dumps({"entities": entities})
    if site.startswith("llm_trace_semantic"):
        records = json.loads(user)["records"]
        return json.dumps({"results": [{"record_id": item["record_id"], "traces": []} for item in records]})
    if site.startswith("nonsense_candidate"):
        sentences = json.loads(user)["sentences"]
        return json.dumps({"results": [
            {"id": item["id"], "understandable": True, "suspected_phrase": "", "explanation": "", "confidence": "low"}
            for item in sentences
        ]})
    if site.startswith("llm_trace_validator"):
        return json.dumps({"status": "uncertain", "reason": "Test double cannot judge context."})
    if site.startswith(("context_validator", "design_contradiction")):
        return json.dumps({"status": "uncertain", "confidence": 0.5, "reason": "Test double cannot judge context."})
    return json.dumps({})


def _injected_failure(behaviour: GatewayBehaviour, digest: str, occurrence: int, body: bytes) -> str | None:
    if not behaviour.failure_modes:
        return None
    if behaviour.failure_marker is not None and behaviour.failure_marker.encode() not in body:
        return None
    if occurrence <= behaviour.fail_first:
        return behaviour.failure_modes[(occurrence - 1) % len(behaviour.failure_modes)]
    if behaviour.failure_rate <= 0:
        return None
    roll = hashlib.sha256(f"{digest}:{occurrence}".encode()).digest()
    if int.from_bytes(roll[:4], "big") / 2**32 >= behaviour.failure_rate:
        return None
    return behaviour.failure_modes[roll[4] % len(behaviour.failure_modes)]


def make_handler(behaviour: GatewayBehaviour, counters: GatewayCounters) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
            return  # Request lines and bodies may contain manuscript text; never log them.

        _counted = False

        def _leave(self) -> None:
            # Leave the in-flight count before the reply is written: once the client has the
            # bytes it may reuse its slot, and counting that overlap would fake a quota breach.
            if self._counted:
                with counters.lock:
                    counters.in_flight -= 1
                self._counted = False

        def _send(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
            self._leave()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/stats"):
                self._send(200, json.dumps(counters.snapshot()).encode())
            else:
                self._send(404, b"{}")

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._send(404, b"{}")
                return
            with counters.lock:
                counters.received += 1
                counters.in_flight += 1
                counters.max_in_flight = max(counters.max_in_flight, counters.in_flight)
                over_quota = behaviour.max_concurrency is not None and counters.in_flight > behaviour.max_concurrency
            self._counted = True
            outcome = "ok"
            try:
                if self.headers.get("x-litellm-api-key") != API_KEY:
                    outcome = "401"
                    self._send(401, b'{"error": "unauthorized"}')
                    return
                if behaviour.force_status is not None:
                    outcome = str(behaviour.force_status)
                    self._send(behaviour.force_status, b'{"error": "forced"}')
                    return
                if over_quota:
                    outcome = "429_quota"
                    self._send(429, b'{"error": "concurrency quota"}', {"Retry-After": behaviour.retry_after})
                    return
                payload = json.loads(body)
                messages = {item["role"]: item["content"] for item in payload["messages"]}
                digest = hashlib.sha256(body).hexdigest()
                with counters.lock:
                    counters.occurrences[digest] += 1
                    occurrence = counters.occurrences[digest]
                    counters.call_sites[_call_site(messages["system"])] += 1
                failure = _injected_failure(behaviour, digest, occurrence, body)
                if behaviour.latency_seconds:
                    time.sleep(behaviour.latency_seconds)
                outcome = failure or "ok"
                if failure in {"429", "500", "503"}:
                    self._send(int(failure), b'{"error": "injected"}', {"Retry-After": behaviour.retry_after} if failure != "500" else None)
                elif failure == "timeout":
                    time.sleep(behaviour.hang_seconds)
                    self.close_connection = True
                elif failure == "reset":
                    self.close_connection = True  # Close without a status line: a connection failure.
                elif failure == "malformed":
                    self._send(200, b'{"choices": [ {"message": ')
                else:
                    content = reply_content(messages["system"], messages["user"])
                    self._send(200, json.dumps({
                        "model": MODEL,
                        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": len(body) // 4, "completion_tokens": len(content) // 4},
                    }).encode())
            finally:
                self._leave()
                with counters.lock:
                    counters.outcomes[outcome] += 1

    return Handler


class FakeGateway:
    """Context manager running the double on 127.0.0.1 in a background thread."""

    def __init__(
        self, behaviour: GatewayBehaviour | None = None, port: int = 0, tls_context: ssl.SSLContext | None = None,
    ) -> None:
        self.behaviour = behaviour or GatewayBehaviour()
        self.counters = GatewayCounters()
        self.server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.behaviour, self.counters))
        self.server.daemon_threads = True
        self.scheme = "http"
        if tls_context is not None:
            # Serves HTTPS, for example with a self-signed certificate to exercise verify_ssl=false.
            self.server.socket = tls_context.wrap_socket(self.server.socket, server_side=True)
            self.scheme = "https"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://127.0.0.1:{self.server.server_address[1]}/v1"

    def env(self) -> dict[str, str]:
        """Gateway settings for a pipeline subprocess or build_gpt_oss_client()."""
        return {"INTELLIHUB_API_KEY": API_KEY, "INTELLIHUB_BASE_URL": self.base_url, "INTELLIHUB_MODEL": MODEL, "INTELLIHUB_ENV_FILE": "/dev/null"}

    def __enter__(self) -> "FakeGateway":
        self.thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--latency-ms", type=float, default=0.0)
    parser.add_argument("--failure-rate", type=float, default=0.0)
    parser.add_argument("--failure-modes", default="429,500,503")
    parser.add_argument("--max-concurrency", type=int, default=None)
    args = parser.parse_args(argv)
    modes = tuple(mode for mode in args.failure_modes.split(",") if mode)
    if unknown := set(modes) - set(FAILURE_MODES):
        parser.error(f"unknown failure modes: {', '.join(sorted(unknown))}")
    behaviour = GatewayBehaviour(args.latency_ms / 1000, args.failure_rate, modes, args.max_concurrency)
    with FakeGateway(behaviour, args.port) as gateway:
        print(json.dumps(gateway.env()), flush=True)
        try:
            gateway.thread.join()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
