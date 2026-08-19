from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from content_integrity.validators.context_validator import IntelliHubGPTOSSClient


class _FakeResponse:
    """Minimal stand-in for the context-manager object urllib.request.urlopen() returns."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _client(cache_dir: str | Path | None = None) -> IntelliHubGPTOSSClient:
    return IntelliHubGPTOSSClient(api_key="test-key", cache_dir=cache_dir)


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
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
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
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                client.complete(system="sys", user="hi")
            self.assertEqual(len(calls), 1)
            self.assertEqual(client.call_stats.request_count, 1)

            # Identical payload -> cache hit: no live call, CallStats left untouched.
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
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
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
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
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.complete(system="sys", user="hi")
            client.complete(system="sys", user="hi")

        self.assertEqual(len(calls), 2)
        self.assertEqual(client.call_stats.request_count, 2)
        self.assertEqual(client.call_stats.success_count, 2)


if __name__ == "__main__":
    unittest.main()
