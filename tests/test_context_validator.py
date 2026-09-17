from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from content_integrity.validators.context_validator import IntelliHubGPTOSSClient, TruncatedResponseError, build_gpt_oss_client


class _FakeResponse:
    """Minimal stand-in for the context-manager response the client's transport seam returns."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self._unread = body

    def __enter__(self) -> "_FakeResponse":
        self._unread = self._body  # A shared return_value is re-entered once per call.
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        body, self._unread = self._unread, b""
        return body


def _client(cache_dir: str | Path | None = None) -> IntelliHubGPTOSSClient:
    return IntelliHubGPTOSSClient(
        api_key="test-key", base_url="https://gateway.invalid/v1", model_name="test-model",
        cache_dir=cache_dir, backoff_seconds=0,
    )


def _gateway_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class GPTOSSClientCacheTests(unittest.TestCase):
    def test_cache_miss_writes_entry_and_makes_live_call(self) -> None:
        calls: list[object] = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(request)
            return _FakeResponse(json.dumps(_gateway_payload("hello")).encode("utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            client = _client(cache_dir=directory)
            with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=fake_urlopen):
                result = client.complete(system="sys", user="hi")

            self.assertEqual(result, "hello")
            self.assertEqual(len(calls), 1)
            self.assertEqual(client.call_stats.request_count, 1)
            self.assertEqual(client.call_stats.success_count, 1)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 1)

    def test_cache_hit_skips_live_call_and_does_not_touch_call_stats(self) -> None:
        calls: list[object] = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(request)
            return _FakeResponse(json.dumps(_gateway_payload("hello")).encode("utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            client = _client(cache_dir=directory)
            with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=fake_urlopen):
                client.complete(system="sys", user="hi")
            self.assertEqual(len(calls), 1)
            self.assertEqual(client.call_stats.request_count, 1)

            # Identical payload -> cache hit: no live call, CallStats left untouched.
            with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=fake_urlopen):
                result = client.complete(system="sys", user="hi")

            self.assertEqual(result, "hello")
            self.assertEqual(len(calls), 1)
            self.assertEqual(client.call_stats.request_count, 1)
            self.assertEqual(client.call_stats.success_count, 1)

    def test_different_payload_is_a_cache_miss(self) -> None:
        calls: list[object] = []

        def fake_urlopen(request, timeout=None, context=None):
            content = "first" if not calls else "second"
            calls.append(request)
            return _FakeResponse(json.dumps(_gateway_payload(content)).encode("utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            client = _client(cache_dir=directory)
            with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=fake_urlopen):
                first = client.complete(system="sys", user="question one")
                second = client.complete(system="sys", user="question two")

            self.assertEqual(first, "first")
            self.assertEqual(second, "second")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(list(Path(directory).glob("*.json"))), 2)

    def test_caching_disabled_by_default_hits_network_every_time(self) -> None:
        calls: list[object] = []

        def fake_urlopen(request, timeout=None, context=None):
            calls.append(request)
            return _FakeResponse(json.dumps(_gateway_payload("hello")).encode("utf-8"))

        client = _client()  # no cache_dir passed
        self.assertIsNone(client.cache_dir)
        with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=fake_urlopen):
            client.complete(system="sys", user="hi")
            client.complete(system="sys", user="hi")

        self.assertEqual(len(calls), 2)
        self.assertEqual(client.call_stats.request_count, 2)
        self.assertEqual(client.call_stats.success_count, 2)


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize("changed", [
    {"base_url": "https://other.invalid/v1"}, {"model_name": "other-model"},
    {"api_key": "other-test-key"}, {"system": "prompt-v2"}, {"user": "other input"},
    {"max_tokens": 151}, {"temperature": 0.5},
])
def test_cache_isolates_every_response_affecting_input(tmp_path, changed):
    settings = dict(api_key="test-key", base_url="https://gateway.invalid/v1", model_name="test-model", cache_dir=tmp_path)
    request = dict(system="prompt-v1", user="input", max_tokens=150, temperature=0.0)
    with patch.object(IntelliHubGPTOSSClient, "_open", return_value=_FakeResponse(json.dumps(_gateway_payload("answer")).encode())) as transport:
        IntelliHubGPTOSSClient(**settings).complete(**request)
        for key, value in changed.items():
            (settings if key in settings else request)[key] = value
        IntelliHubGPTOSSClient(**settings).complete(**request)
        assert transport.call_count == 2
        IntelliHubGPTOSSClient(**settings).complete(**request)
        assert transport.call_count == 2


@pytest.mark.parametrize("corruption", ["{broken", "[]", "{}", '{"choices": []}'])
def test_corrupt_cache_is_replaced_by_valid_response(tmp_path, corruption):
    client = _client(tmp_path)
    with patch.object(IntelliHubGPTOSSClient, "_open", return_value=_FakeResponse(json.dumps(_gateway_payload("answer")).encode())) as transport:
        client.complete(system="sys", user="hi")
        path = next(tmp_path.glob("*.json"))
        path.write_text(corruption, encoding="utf-8")
        assert client.complete(system="sys", user="hi") == "answer"
        assert transport.call_count == 2
        assert json.loads(path.read_text()) == _gateway_payload("answer")


def test_expired_cache_is_invalidated(tmp_path):
    client = IntelliHubGPTOSSClient(api_key="test", base_url="https://gateway.invalid", model_name="test", cache_dir=tmp_path, max_cache_age_seconds=60)
    with patch.object(IntelliHubGPTOSSClient, "_open", return_value=_FakeResponse(json.dumps(_gateway_payload("answer")).encode())) as transport:
        client.complete(system="sys", user="hi")
        os.utime(next(tmp_path.glob("*.json")), (0, 0))
        client.complete(system="sys", user="hi")
        assert transport.call_count == 2


def test_concurrent_clients_share_only_compatible_atomic_cache_entries(tmp_path):
    def response(request, **kwargs):
        model = json.loads(request.data)["model"]
        return _FakeResponse(json.dumps(_gateway_payload(model)).encode())

    def run(model):
        client = IntelliHubGPTOSSClient(api_key="test", base_url="https://gateway.invalid", model_name=model, cache_dir=tmp_path)
        return client.complete(system="sys", user="hi")

    models = ["gene", "protein"] * 8
    with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=response), ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(run, models)) == models
    assert len(list(tmp_path.iterdir())) == 2
    with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=AssertionError("expected disk cache hit")):
        assert run("gene") == "gene"
        assert run("protein") == "protein"


def test_configuration_files_do_not_mutate_environment_or_later_clients(tmp_path):
    with patch.dict(os.environ, {}, clear=True):
        for model in ("first", "second"):
            path = tmp_path / f"{model}.env"
            path.write_text(f"INTELLIHUB_API_KEY=test\nINTELLIHUB_BASE_URL=https://{model}.invalid/v1\nINTELLIHUB_MODEL={model}\n", encoding="utf-8")
            client = build_gpt_oss_client(path)
            assert client.model_name == client.model_id == model
            assert client.base_url == f"https://{model}.invalid/v1"
            assert not os.environ
        with patch.dict(os.environ, {"INTELLIHUB_MODEL": "override"}):
            assert build_gpt_oss_client(path).model_name == "override"


@pytest.mark.parametrize("base_url", ["", "/v1", "file:///tmp/model", "https://user:password@gateway.invalid", "https://gateway.invalid?token=test"])
def test_invalid_gateway_configuration_fails_before_transport(base_url):
    with pytest.raises(ValueError, match="base_url"):
        IntelliHubGPTOSSClient(api_key="test", base_url=base_url, model_name="test")


def test_truncation_is_not_cached_or_retried_as_a_transport_failure(tmp_path):
    client = _client(tmp_path)
    truncated = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    with patch.object(IntelliHubGPTOSSClient, "_open", side_effect=[
        _FakeResponse(json.dumps(truncated).encode()),
        _FakeResponse(json.dumps(_gateway_payload("valid")).encode()),
    ]) as transport:
        with pytest.raises(TruncatedResponseError):
            client.complete(system="sys", user="hi")
        assert not list(tmp_path.iterdir())
        assert client.complete(system="sys", user="hi") == "valid"
        assert client.complete(system="sys", user="hi") == "valid"
        assert transport.call_count == 2
    assert client.call_stats.request_count == 2
    assert client.call_stats.success_count == client.call_stats.failure_count == 1
    assert client.call_stats.retry_count == 0


def test_shared_client_telemetry_counts_concurrent_requests():
    client = _client()
    with patch.object(IntelliHubGPTOSSClient, "_open", return_value=_FakeResponse(json.dumps(_gateway_payload("valid")).encode())):
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(lambda n: client.complete(system="sys", user=str(n)), range(12))) == ["valid"] * 12
    assert client.call_stats.request_count == client.call_stats.success_count == 12
    assert client.call_stats.failure_count == client.call_stats.retry_count == 0
