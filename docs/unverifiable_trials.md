# Unverifiable Clinical Trial V1

This standalone check extracts explicit registration references from abstract titles and
section-aware text. It verifies trial existence against authoritative registry responses; it
does not assess scientific validity or misconduct and is not integrated into the main pipeline
or consolidated workbook.

## Registry support

ClinicalTrials.gov is the only fully automated V1 adapter. Its canonical identifier is `NCT`
followed by exactly eight digits. The adapter uses the official
`GET https://clinicaltrials.gov/api/v2/studies/{nctId}` endpoint.

The extractor also recognises `ISRCTN`, `ACTRN`, `EUCTR`/`EudraCT`, and `ChiCTR` references.
They produce `unsupported_registry` audit results requiring manual verification because V1 has
no authoritative adapter for them.

## Verification statuses

- `verified`: the authoritative registry returned a study record. Withdrawn and terminated
  studies remain verified.
- `not_found`: a valid NCT identifier received a completed authoritative not-found response.
- `invalid_format`: an explicit NCT reference is not `NCT` plus eight digits.
- `placeholder_id`: the abstract contains a pending, masked, or unfinished identifier.
- `registration_claim_without_id`: registration is explicitly claimed without a usable ID.
- `unsupported_registry`: the prefix is recognised but no automated adapter exists.
- `lookup_failed`: the request timed out, retries were exhausted, the service failed, the
  response was malformed, or offline mode lacked a cache entry. This is operational and does
  not trigger an integrity finding.

## Retry, cache, network, and privacy behaviour

Timeouts, HTTP 429, and temporary 5xx responses are retried with bounded exponential delays.
Permanent 4xx responses are not retried; HTTP 404 is a confirmed not-found result. Verified and
confirmed-not-found JSON responses are cached atomically by NCT identifier. `--offline-cache-only`
disables network access. No credentials are used or logged; requests contain only the reported
NCT identifier.

The authoritative registry response determines existence. Metadata is retained for review but
V1 does not trigger phase, enrollment, condition, intervention, or title mismatches.

## Known limitations

Extraction is phrase- and sentence-based. Complex multi-study abstracts, unusual identifier
punctuation, registry aliases, and registrations without a recognised prefix may require manual
review. Non-NCT registries need future authoritative adapters. Real ASCO data still requires
calibration.
