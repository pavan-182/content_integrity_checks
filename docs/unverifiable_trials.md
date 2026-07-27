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
no authoritative adapter for them. Expected extracted formats are:

- ClinicalTrials.gov: `NCT` plus exactly eight digits.
- ISRCTN: `ISRCTN` plus exactly eight digits.
- ANZCTR: `ACTRN` plus the expected fourteen-digit registration number.
- EU Clinical Trials Register: recognised `EUCTR` or `EudraCT` format.
- Chinese Clinical Trial Registry: recognised `ChiCTR` format.

Local claims are classified in this order: placeholder, explicit registration claim without an
ID, invalid registry-specific format, then valid-but-unsupported registry. A malformed non-NCT
identifier is therefore an invalid-format finding rather than an unsupported-registry result.

## Verification statuses

- `verified`: the authoritative registry returned a study record. Withdrawn and terminated
  studies remain verified.
- `not_found`: a valid NCT identifier received a completed authoritative not-found response.
- `invalid_format`: an explicit identifier fails its detected registry's expected format.
- `placeholder_id`: the abstract contains a pending, masked, or unfinished identifier.
- `registration_claim_without_id`: registration is explicitly claimed without a usable ID.
- `unsupported_registry`: the prefix is recognised but no automated adapter exists.
- `lookup_failed`: the request timed out, retries were exhausted, the service failed, the
  response was malformed, a registry result failed invariant validation, or offline mode lacked
  a usable cache entry. This is operational and does not trigger an integrity finding.

`not_found` means a valid identifier received a completed authoritative absence response.
`lookup_failed` means existence was not determined and an operational retry is required; it
must never be interpreted as trial absence.

Every live, cached, or injected result is validated before classification. Verified results
must report `found=true`, the selected registry, and matching requested, queried, and external
identifiers. Not-found results must report `found=false` with the selected registry and queried
identifier. Lookup failures cannot expose a matched external record. Inconsistent results are
converted to non-triggering operational failures.

## Retry, cache, network, and privacy behaviour

Timeouts, HTTP 429, and temporary 5xx responses are retried with bounded exponential delays.
Permanent non-429 4xx responses, malformed successful responses, and cache-write failures are
not retried; HTTP 404 is a confirmed not-found result. Verified and confirmed-not-found JSON
responses are cached atomically by NCT identifier. Cache writes are best-effort: local write
failure never changes or retries an authoritative result.

Cached content receives the same invariant validation as live results. Invalid cached content
falls back to a live request. In `--offline-cache-only` mode, a missing, malformed, mismatched,
or expired cache entry becomes a non-triggering operational failure.

By default, valid cache entries are retained indefinitely to preserve V1 behaviour. Set
`--max-cache-age-seconds` to enforce an optional age limit; expired entries use live fallback,
or produce an operational failure in offline mode. Separate verified/not-found expiry policies
are not yet supported.

`--offline-cache-only` disables network access. No credentials are used or logged; requests
contain only the reported NCT identifier.

The authoritative registry response determines existence. Metadata is retained for review but
V1 does not trigger phase, enrollment, condition, intervention, or title mismatches.

## Known limitations

Extraction is phrase- and sentence-based. Complex multi-study abstracts, unusual identifier
punctuation, registry aliases, and registrations without a recognised prefix may require manual
review. Non-NCT registries need future authoritative adapters. Real ASCO data still requires
calibration. V1 does not judge misconduct or scientific validity.
