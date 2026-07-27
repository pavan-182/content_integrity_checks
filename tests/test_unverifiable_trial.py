from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from dataclasses import asdict
from pathlib import Path

from asco_integrity.detectors.unverifiable_trial import (
    CLINICAL_TRIALS_GOV,
    ClinicalTrialsGovClient,
    RegistryLookupResult,
    detect_unverifiable_trials,
    extract_trial_reference_claims,
)
from asco_integrity.models import ParsedRecord
from scripts.detect_unverifiable_trials import CSV_COLUMNS


ROOT = Path(__file__).resolve().parents[1]


def _record(text: str, record_id: str = "TRIAL-1") -> ParsedRecord:
    return ParsedRecord(
        "trial.xml",
        record_id=record_id,
        title="Synthetic trial verification",
        abstract_text=text,
        abstract_sections=[{"section": "Methods", "text": text}],
    )


def _verified(
    trial_id: str = "NCT01234567",
    *,
    status: str = "RECRUITING",
    cache_hit: bool = False,
) -> RegistryLookupResult:
    return RegistryLookupResult(
        registry_name=CLINICAL_TRIALS_GOV,
        queried_trial_id=trial_id,
        lookup_status="verified",
        found=True,
        external_record_id=trial_id,
        external_title="A synthetic registered study",
        study_type="INTERVENTIONAL",
        phase="PHASE2",
        recruitment_status=status,
        conditions=("Lung Cancer",),
        interventions=("Examplemab",),
        enrollment=120,
        source_record_url=f"https://clinicaltrials.gov/study/{trial_id}",
        http_status=200,
        cache_hit=cache_hit,
    )


def _not_found(trial_id: str = "NCT01234567") -> RegistryLookupResult:
    return RegistryLookupResult(
        registry_name=CLINICAL_TRIALS_GOV,
        queried_trial_id=trial_id,
        lookup_status="not_found",
        found=False,
        http_status=404,
    )


class FakeClient:
    def __init__(self, results: dict[str, RegistryLookupResult]) -> None:
        self.results = results
        self.calls: list[str] = []

    def lookup(self, trial_id: str) -> RegistryLookupResult:
        self.calls.append(trial_id)
        return self.results[trial_id]


def _api_payload(trial_id: str = "NCT01234567", status: str = "RECRUITING") -> bytes:
    return json.dumps({
        "protocolSection": {
            "identificationModule": {
                "nctId": trial_id,
                "officialTitle": "A synthetic registered study",
            },
            "designModule": {
                "studyType": "INTERVENTIONAL",
                "phases": ["PHASE2"],
                "enrollmentInfo": {"count": 120},
            },
            "statusModule": {"overallStatus": status},
            "conditionsModule": {"conditions": ["Lung Cancer"]},
            "armsInterventionsModule": {
                "interventions": [{"name": "Examplemab"}],
            },
        }
    }).encode()


class UnverifiableTrialTests(unittest.TestCase):
    def test_valid_nct_identifier_found(self) -> None:
        client = FakeClient({"NCT01234567": _verified()})
        result = detect_unverifiable_trials(
            [_record("Registered as NCT01234567.")],
            registry_clients={CLINICAL_TRIALS_GOV: client},
        )[0]
        self.assertFalse(result.check_triggered)
        self.assertEqual(result.verification_status, "verified")
        self.assertEqual(result.matched_source_type, "clinical_trial_registry")
        self.assertEqual(result.matched_source_id, "NCT01234567")
        self.assertEqual(result.external_title, "A synthetic registered study")

    def test_valid_nct_identifier_not_found(self) -> None:
        result = detect_unverifiable_trials(
            [_record("Registered as NCT01234567.")],
            registry_clients={CLINICAL_TRIALS_GOV: FakeClient({"NCT01234567": _not_found()})},
        )[0]
        self.assertTrue(result.check_triggered)
        self.assertEqual(result.finding_type, "trial_not_found")
        self.assertEqual(result.severity, "high")
        self.assertEqual(result.confidence, "very_high")
        self.assertEqual(result.matched_source_type, "none")

    def test_invalid_nct_identifier(self) -> None:
        result = detect_unverifiable_trials([_record("Registration: NCT1234.")])[0]
        self.assertTrue(result.check_triggered)
        self.assertEqual(result.finding_type, "invalid_trial_id_format")
        self.assertEqual(result.lookup_status, "invalid_format")

    def test_identifier_normalisation(self) -> None:
        claim = extract_trial_reference_claims(_record("Registration was NCT 01234567."))[0]
        self.assertEqual(claim.normalized_trial_id, "NCT01234567")
        self.assertTrue(claim.format_valid)

    def test_placeholder_identifier(self) -> None:
        result = detect_unverifiable_trials([_record("Registration: NCTXXXXXXXX.")])[0]
        self.assertTrue(result.check_triggered)
        self.assertEqual(result.finding_type, "placeholder_trial_id")
        self.assertEqual(result.confidence, "very_high")

    def test_registration_claim_without_id(self) -> None:
        result = detect_unverifiable_trials([
            _record("Registered at ClinicalTrials.gov.")
        ])[0]
        self.assertTrue(result.check_triggered)
        self.assertEqual(result.finding_type, "registration_claim_without_id")
        self.assertEqual(result.confidence, "high")

    def test_trial_wording_without_registration_claim(self) -> None:
        self.assertEqual(
            detect_unverifiable_trials([_record("This was a randomized clinical trial.")]),
            [],
        )

    def test_unsupported_registry_requires_manual_verification(self) -> None:
        result = detect_unverifiable_trials([
            _record("The trial was registered as ISRCTN12345678.")
        ])[0]
        self.assertTrue(result.check_triggered)
        self.assertEqual(result.lookup_status, "unsupported_registry")
        self.assertEqual(result.finding_type, "unsupported_registry_manual_verification")
        self.assertEqual(result.severity, "low")
        self.assertIn("does not mean that the trial is absent", result.review_reason)

    def test_registry_timeout_is_operational(self) -> None:
        def timeout(_url: str, _timeout: float) -> tuple[int, bytes]:
            raise TimeoutError

        client = ClinicalTrialsGovClient(max_retries=0, transport=timeout)
        result = detect_unverifiable_trials(
            [_record("Registered as NCT01234567.")],
            registry_clients={CLINICAL_TRIALS_GOV: client},
        )[0]
        self.assertFalse(result.check_triggered)
        self.assertEqual(result.lookup_status, "lookup_failed")
        self.assertIn("network_error", result.operational_error)

    def test_malformed_registry_response_is_operational(self) -> None:
        client = ClinicalTrialsGovClient(
            max_retries=0,
            transport=lambda _url, _timeout: (200, b'{"unexpected": true}'),
        )
        result = detect_unverifiable_trials(
            [_record("Registered as NCT01234567.")],
            registry_clients={CLINICAL_TRIALS_GOV: client},
        )[0]
        self.assertFalse(result.check_triggered)
        self.assertEqual(result.lookup_status, "lookup_failed")
        self.assertIn("invalid_response", result.operational_error)

    def test_temporary_http_errors_are_retried(self) -> None:
        for temporary_status in (429, 503):
            calls = 0

            def transport(url: str, _timeout: float) -> tuple[int, bytes]:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise urllib.error.HTTPError(
                        url, temporary_status, "temporary", None, None
                    )
                return 200, _api_payload()

            with self.subTest(status=temporary_status):
                result = ClinicalTrialsGovClient(
                    max_retries=1,
                    transport=transport,
                    sleep=lambda _seconds: None,
                ).lookup("NCT01234567")
                self.assertEqual(result.lookup_status, "verified")
                self.assertEqual(calls, 2)

    def test_permanent_not_found_is_not_retried(self) -> None:
        calls = 0

        def transport(url: str, _timeout: float) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError(url, 404, "not found", None, None)

        result = ClinicalTrialsGovClient(
            max_retries=3,
            transport=transport,
            sleep=lambda _seconds: None,
        ).lookup("NCT01234567")
        self.assertEqual(result.lookup_status, "not_found")
        self.assertEqual(calls, 1)

    def test_multiple_and_duplicate_ids(self) -> None:
        client = FakeClient({
            "NCT01234567": _verified("NCT01234567"),
            "NCT87654321": _not_found("NCT87654321"),
        })
        results = detect_unverifiable_trials(
            [_record(
                "NCT01234567 was registered. NCT87654321 was also reported. "
                "The parent identifier was NCT01234567."
            )],
            registry_clients={CLINICAL_TRIALS_GOV: client},
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(set(client.calls), {"NCT01234567", "NCT87654321"})
        verified = next(item for item in results if item.normalized_trial_id == "NCT01234567")
        self.assertIn("parent identifier", verified.source_sentence)

    def test_repeated_id_across_abstracts_uses_one_request(self) -> None:
        client = FakeClient({"NCT01234567": _verified()})
        results = detect_unverifiable_trials(
            [
                _record("Registered as NCT01234567.", "A"),
                _record("Parent trial NCT01234567.", "B"),
            ],
            registry_clients={CLINICAL_TRIALS_GOV: client},
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(client.calls, ["NCT01234567"])

    def test_cache_hit_avoids_external_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def transport(_url: str, _timeout: float) -> tuple[int, bytes]:
                nonlocal calls
                calls += 1
                return 200, _api_payload()

            first = ClinicalTrialsGovClient(
                cache_dir=directory,
                transport=transport,
            ).lookup("NCT01234567")
            second = ClinicalTrialsGovClient(
                cache_dir=directory,
                offline_cache_only=True,
                transport=transport,
            ).lookup("NCT01234567")
            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(calls, 1)

    def test_withdrawn_or_terminated_record_is_still_verified(self) -> None:
        for status in ("WITHDRAWN", "TERMINATED"):
            with self.subTest(status=status):
                result = detect_unverifiable_trials(
                    [_record("Registered as NCT01234567.")],
                    registry_clients={
                        CLINICAL_TRIALS_GOV: FakeClient({
                            "NCT01234567": _verified(status=status),
                        })
                    },
                )[0]
                self.assertFalse(result.check_triggered)
                self.assertEqual(result.external_recruitment_status, status)

    def test_cli_offline_cache_and_findings_only(self) -> None:
        xml = """
        <article article-type="meeting-abstract">
          <front><article-meta>
            <article-id custom-type="abstract-id">TRIAL-CLI</article-id>
            <title-group><article-title>Trial verification CLI</article-title></title-group>
            <abstract><sec><title>Methods</title>
              <p>Registrations were NCT01234567 and NCT1234.</p>
            </sec></abstract>
          </article-meta></front>
        </article>
        """
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            input_dir = base / "input"
            cache_dir = base / "cache"
            input_dir.mkdir()
            cache_dir.mkdir()
            (input_dir / "record.xml").write_text(xml, encoding="utf-8")
            (cache_dir / "NCT01234567.json").write_text(
                json.dumps(asdict(_verified())),
                encoding="utf-8",
            )
            full_csv = base / "full.csv"
            findings_csv = base / "findings.csv"
            common = [
                sys.executable,
                str(ROOT / "scripts" / "detect_unverifiable_trials.py"),
                "--input-dir", str(input_dir),
                "--cache-dir", str(cache_dir),
                "--offline-cache-only",
            ]
            subprocess.run(
                [*common, "--output-csv", str(full_csv)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [*common, "--output-csv", str(findings_csv), "--findings-only"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            with full_csv.open(newline="", encoding="utf-8") as handle:
                full_reader = csv.DictReader(handle)
                full_rows = list(full_reader)
            with findings_csv.open(newline="", encoding="utf-8") as handle:
                findings_reader = csv.DictReader(handle)
                finding_rows = list(findings_reader)
            self.assertEqual(full_reader.fieldnames, CSV_COLUMNS)
            self.assertEqual(findings_reader.fieldnames, CSV_COLUMNS)
            self.assertEqual(len(full_rows), 2)
            self.assertEqual(len(finding_rows), 1)
            self.assertEqual(finding_rows[0]["finding_type"], "invalid_trial_id_format")


if __name__ == "__main__":
    unittest.main()
