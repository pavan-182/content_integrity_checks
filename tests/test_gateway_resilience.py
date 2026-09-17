"""Failure injection against the real client over loopback HTTP(S), using the gateway test double.

These prove retry, timeout, backpressure, circuit, and TLS behaviour of the production transport.
They do not measure real GPT-OSS latency or capacity.
"""

from __future__ import annotations

import shutil
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from content_integrity.validators.context_validator import GatewayRequestError, IntelliHubGPTOSSClient
from scripts.fake_gpt_oss_gateway import API_KEY, MODEL, FakeGateway, GatewayBehaviour

SYSTEM = "Test prompt\nPrompt version: context_validator_v3"


def _client(gateway: FakeGateway, **overrides) -> IntelliHubGPTOSSClient:
    settings = dict(
        api_key=API_KEY, base_url=gateway.base_url, model_name=MODEL, timeout_seconds=2.0,
        connect_timeout_seconds=1.0, backoff_seconds=0.01, max_backoff_seconds=0.05,
        circuit_failure_threshold=100,
    )
    return IntelliHubGPTOSSClient(**{**settings, **overrides})


def _complete(client: IntelliHubGPTOSSClient, user: str = "input") -> str:
    return client.complete(system=SYSTEM, user=user, max_tokens=64)


@pytest.mark.parametrize("mode", ["429", "500", "503", "timeout", "reset", "malformed"])
def test_transient_failure_recovers_within_retry_policy(mode):
    behaviour = GatewayBehaviour(fail_first=2, failure_modes=(mode,), hang_seconds=1.0)
    with FakeGateway(behaviour) as gateway:
        client = _client(gateway, max_attempts=3, timeout_seconds=0.3)
        assert '"status": "uncertain"' in _complete(client)
    assert gateway.counters.snapshot()["requests"] == 3
    assert client.call_stats.success_count == 1
    assert client.call_stats.retry_count == 2
    assert sum(client.call_stats.attempt_errors.values()) == 2


@pytest.mark.parametrize(("mode", "category"), [
    ("429", "rate_limit"), ("503", "server_error"), ("timeout", "timeout"),
    ("reset", "connection"), ("malformed", "invalid_response"),
])
def test_exhausted_transient_failure_is_categorized_and_bounded(mode, category):
    behaviour = GatewayBehaviour(fail_first=99, failure_modes=(mode,), hang_seconds=1.0)
    with FakeGateway(behaviour) as gateway:
        client = _client(gateway, max_attempts=3, timeout_seconds=0.3)
        with pytest.raises(GatewayRequestError) as raised:
            _complete(client)
    assert (raised.value.category, raised.value.retryable, raised.value.attempts) == (category, True, 3)
    assert gateway.counters.snapshot()["requests"] == 3
    assert client.call_stats.request_failures == {category: 1}


def test_authentication_failure_is_not_retried_and_disables_the_client():
    with FakeGateway() as gateway:
        client = _client(gateway, api_key="wrong-key", max_attempts=5)
        for _ in range(3):
            with pytest.raises(GatewayRequestError) as raised:
                _complete(client)
            assert (raised.value.category, raised.value.retryable) == ("authentication", False)
    assert gateway.counters.snapshot()["requests"] == 1


def test_permanent_request_error_is_not_retried():
    with FakeGateway(GatewayBehaviour(force_status=400)) as gateway:
        client = _client(gateway, max_attempts=5)
        with pytest.raises(GatewayRequestError) as raised:
            _complete(client)
    assert (raised.value.category, raised.value.retryable, raised.value.status) == ("invalid_request", False, 400)
    assert gateway.counters.snapshot()["requests"] == 1


def test_concurrency_is_bounded_across_threads_so_the_quota_is_never_exceeded():
    behaviour = GatewayBehaviour(latency_seconds=0.03, max_concurrency=2)
    with FakeGateway(behaviour) as gateway:
        client = _client(gateway, max_concurrent_requests=2)
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda index: _complete(client, f"input {index}"), range(40)))
    snapshot = gateway.counters.snapshot()
    assert len(results) == 40
    assert snapshot["max_in_flight"] <= 2
    assert "429_quota" not in snapshot["outcomes"]
    assert client.call_stats.max_in_flight == 2
    assert client.call_stats.max_waiting > 0


def test_rate_limit_retry_after_pauses_other_threads():
    behaviour = GatewayBehaviour(fail_first=1, failure_modes=("429",), failure_marker="throttled", retry_after="0.6")
    with FakeGateway(behaviour) as gateway:
        client = _client(gateway, max_backoff_seconds=5.0)
        throttled = threading.Thread(target=_complete, args=(client, "throttled request"))
        throttled.start()
        deadline = time.monotonic() + 5
        while "429" not in gateway.counters.snapshot()["outcomes"] and time.monotonic() < deadline:
            time.sleep(0.01)
        started = time.monotonic()
        _complete(client, "unrelated request")
        waited = time.monotonic() - started
        throttled.join()
    assert waited >= 0.4
    assert client.call_stats.backoff_seconds >= 0.6


def test_circuit_opens_after_consecutive_failures_then_probes_after_cooldown():
    behaviour = GatewayBehaviour(force_status=503)
    with FakeGateway(behaviour) as gateway:
        client = _client(gateway, max_attempts=1, circuit_failure_threshold=3, circuit_cooldown_seconds=0.3)
        for _ in range(3):
            with pytest.raises(GatewayRequestError, match="server_error"):
                _complete(client)
        with pytest.raises(GatewayRequestError) as raised:
            _complete(client)
        assert (raised.value.category, raised.value.retryable) == ("circuit_open", True)
        assert gateway.counters.snapshot()["requests"] == 3
        behaviour.force_status = None
        time.sleep(0.35)
        assert _complete(client)
        assert _complete(client, "after recovery")
    assert client.call_stats.circuit_open_events == 1
    assert client.call_stats.circuit_rejections == 1


def test_connect_timeout_does_not_limit_slow_model_replies_but_read_timeout_does():
    with FakeGateway(GatewayBehaviour(latency_seconds=0.4)) as gateway:
        assert _complete(_client(gateway, connect_timeout_seconds=0.1, timeout_seconds=2.0, max_attempts=1))
        with pytest.raises(GatewayRequestError) as raised:
            _complete(_client(gateway, connect_timeout_seconds=2.0, timeout_seconds=0.1, max_attempts=1), "other")
    assert raised.value.category == "timeout"


def test_disk_cache_hit_makes_no_gateway_request(tmp_path):
    with FakeGateway() as gateway:
        first = _client(gateway, cache_dir=tmp_path)
        second = _client(gateway, cache_dir=tmp_path)
        assert _complete(first) == _complete(second)
    assert gateway.counters.snapshot()["requests"] == 1
    assert (second.call_stats.cache_hits, second.call_stats.request_count) == (1, 0)


@pytest.fixture(scope="module")
def self_signed_tls(tmp_path_factory):
    if shutil.which("openssl") is None:
        pytest.skip("openssl is required to create a self-signed certificate")
    directory = tmp_path_factory.mktemp("tls")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
         "-keyout", str(directory / "key.pem"), "-out", str(directory / "cert.pem")],
        check=True, capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "cert.pem", directory / "key.pem")
    return context


def test_verify_ssl_false_reaches_a_self_signed_gateway(self_signed_tls):
    # The deployed IntelliHub gateway is used with INTELLIHUB_VERIFY_SSL=false.
    with FakeGateway(tls_context=self_signed_tls) as gateway:
        assert gateway.base_url.startswith("https://")
        assert _complete(_client(gateway, verify_ssl="false"))


def test_certificate_verification_failure_is_a_non_retryable_configuration_error(self_signed_tls):
    with FakeGateway(tls_context=self_signed_tls) as gateway:
        with pytest.raises(GatewayRequestError) as raised:
            _complete(_client(gateway, verify_ssl="true", max_attempts=3))
    assert (raised.value.category, raised.value.retryable, raised.value.attempts) == ("configuration", False, 1)


class _GatewayDownClient:
    model_name = "test/down"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **_: object) -> str:
        self.calls += 1
        raise GatewayRequestError("circuit_open", "Gateway circuit is open", retryable=True)


class _InvalidContentClient(_GatewayDownClient):
    def complete(self, **_: object) -> str:
        self.calls += 1
        return "not json"


def _records(count: int):
    from content_integrity.models import ParsedRecord

    sentences = [
        f"ERBB2 expression was associated with pembrolizumab response in treated cohort {index}."
        for index in range(count)
    ]
    return [
        ParsedRecord(source_file=f"r{index}.xml", record_id=f"R{index}", title=f"Title {index}",
                     abstract_text=sentence, abstract_sections=[{"section": "Results", "text": sentence}])
        for index, sentence in enumerate(sentences)
    ]


def test_semantic_batch_fails_once_on_gateway_error_but_splits_on_invalid_content():
    from content_integrity.detectors.llm_trace_semantic import detect_semantic_traces

    down = _GatewayDownClient()
    _, stats = detect_semantic_traces(down, _records(4), max_concurrent_batches=1)
    assert down.calls == 1
    assert sorted(stats.failed_record_ids) == ["R0", "R1", "R2", "R3"]
    assert set(stats.failure_categories.values()) == {"circuit_open"}

    invalid = _InvalidContentClient()
    _, stats = detect_semantic_traces(invalid, _records(4), max_concurrent_batches=1)
    assert invalid.calls > 2  # retried and split down to single records
    assert set(stats.failure_categories.values()) == {"invalid_response"}


def test_nonsense_batch_fails_once_on_gateway_error():
    from content_integrity.detectors.nonsense_candidate import NonsenseCandidateDetector

    down = _GatewayDownClient()
    findings, stats = NonsenseCandidateDetector(down, max_concurrent_batches=1).detect_records(_records(4))
    assert not findings
    assert down.calls == 1
    assert set(stats.failed_sentence_record_ids) == {"R0", "R1", "R2", "R3"}
    assert set(stats.failure_categories.values()) == {"circuit_open"}
