"""The synthetic load datasets are reproducible and parse to the counts their manifests declare."""

import json
from pathlib import Path
from threading import Barrier

from content_integrity.entity_extraction import EntityExtractor
from content_integrity.utils import dedupe_records
from content_integrity.xml_parser import discover_xml_files, parse_xml_records
from scripts.generate_load_dataset import PROFILES, generate


def _parsed(directory: Path):
    return [record for path in discover_xml_files(directory) for record in parse_xml_records(path)]


def test_same_seed_reproduces_identical_bytes_and_other_seed_differs(tmp_path):
    first = generate("ci", tmp_path / "first", seed=11)
    second = generate("ci", tmp_path / "second", seed=11)
    other = generate("ci", tmp_path / "other", seed=12)
    assert first["dataset_sha256"] == second["dataset_sha256"] != other["dataset_sha256"]
    assert json.loads((tmp_path / "first" / "manifest.json").read_text())["generator_version"] == first["generator_version"]


def test_valid_profile_parses_to_unique_records_matching_the_manifest(tmp_path):
    manifest = generate("ci", tmp_path / "ci")
    records = _parsed(tmp_path / "ci")
    assert len(records) == manifest["expected"]["input_records"] == PROFILES["ci"].records
    assert len({record.record_id for record in records}) == len(records)
    assert all(record.parse_status == "parsed" and len(record.abstract_sections) >= 3 for record in records)


def test_malformed_profile_declares_every_input_and_duplicate(tmp_path):
    manifest = generate("malformed", tmp_path / "bad")
    records = _parsed(tmp_path / "bad")
    retained, warnings = dedupe_records(records)
    assert len(records) == manifest["expected"]["input_records"]
    assert sum(warning["reason"] == "ingestion_duplicate" for warning in warnings) == manifest["expected"]["duplicate_records"]
    assert sum(record.parse_status == "failed" for record in records) == 4  # truncated, encoding, empty, not XML
    assert len(retained) == len(records) - manifest["expected"]["duplicate_records"]


def test_concurrent_requests_for_the_same_text_share_one_inference():
    barrier = Barrier(8)

    class SlowClient:
        calls = 0

        def complete(self, **kwargs):
            SlowClient.calls += 1
            return '{"entities": [{"text": "Novelmarker", "type": "gene"}]}'

    from concurrent.futures import ThreadPoolExecutor

    extractor = EntityExtractor(SlowClient())

    def spans(_):
        barrier.wait(timeout=10)
        return extractor.spans("Novelmarker expression was measured.")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(spans, range(8)))
    assert results == [(("Novelmarker", "gene"),)] * 8
    assert SlowClient.calls == extractor.inference_count == 1
