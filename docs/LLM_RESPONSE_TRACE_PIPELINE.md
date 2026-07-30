# LLM Response Trace Pipeline

## Scope

This detector finds explicit residue from an LLM or chatbot interaction, such as role labels, editing wrappers, capability disclaimers, leaked prompts, and copied interface controls. It does not classify AI-authored prose. Style, polish, perplexity, burstiness, and generic academic vocabulary are not evidence.

Findings are investigation signals for human editorial review. They do not prove AI authorship, misconduct, scientific quality, or grounds for acceptance or rejection.

## Architecture

```text
ASCO XML
  → lossless trace blocks
  → shared versioned YAML catalogue
  → deterministic known-pattern detection + optional semantic discovery
  → exact source verification
  → candidate fusion
  → selective validation
  → existing Finding model
  → detailed CSV, compact CSV, and Excel Integrity Findings
```

The parser continues to expose normalized `title`, `abstract_sections`, and flattened `abstract_text` for existing consumers. LLM response-trace detection instead uses optional `TraceTextBlock` records containing the field, exact section label, block type and order, `source_text`, and a minimally transformed `detection_text`.

`source_text` preserves logical submitted text: capitalization, punctuation, NFC Unicode characters, line breaks, blank lines, paragraph blocks, Markdown markers, code fences, repeated hyphens, quotes, inline XML text, section order, and title text. Newline forms are canonicalized to `\n`; outer XML formatting whitespace is removed. It does not provide raw XML byte offsets.

Excluded XML content remains limited to known non-abstract metadata: author/contributor blocks, affiliations, references, and table wrappers. If trace blocks cannot be reconstructed, the detector uses the best existing parsed section text, adds `llm_trace_preprocessing_fallback`, and increments run metadata.

## Shared rule catalogue

`asco_integrity/rules/llm_response_trace_rules.yaml` is the only production rule catalogue. The loader validates required fields, controlled categories, severity, signal level, regex syntax, descriptions, and duplicate IDs. Deterministic regexes and semantic prompt descriptions are generated from it. Run metadata records its version, SHA-256 checksum, and rule count.

Rules are classified as:

- `strong`: high-precision explicit residue;
- `contextual`: meaningful but context-sensitive residue;
- `supporting`: formatting or generic framing that cannot independently raise priority.

## Detection and evidence

The deterministic layer scans the exact title and each preserved abstract block. Line-aware patterns therefore retain the distinction between a standalone `User:` label and normal prose such as “the user requested medical advice.”

The optional semantic layer inspects every eligible abstract, including records with no deterministic finding. It returns:

- `semantic_variant`, mapped to a compatible known rule; or
- `novel_pattern_candidate`, kept outside the permanent catalogue.

Model output must contain exactly one result per submitted record and use a strict schema. Plain, fenced, and single embedded JSON objects are supported; ambiguous multiple objects are rejected. Evidence is located case-insensitively only inside the declared preserved section. The occurrence must resolve uniquely. The model’s capitalization is discarded and exact source characters are restored before reporting.

Sentence and quotation context is derived after a source span is found. `evidence_snippet` is the complete containing sentence, or the full paragraph when reliable sentence boundaries are unavailable. Quoted, blockquoted, and code-fenced evidence is retained and sent to validation when enabled.

Novel findings use `LLM-NOVEL-OCC-<HASH>`, derived from record, section, block, exact normalized evidence, and category. This is an occurrence signature, not a semantic family ID. Semantically equivalent phrases may receive different IDs.

## Fusion and validation

Fusion uses record, exact section, block index, overlapping source spans, and category. A deterministic and semantic hit on the same evidence becomes one editor-facing finding; the deterministic span, exact text, and known rule ID win. Separate sections and records remain separate.

Validation is selective:

- strong, unquoted deterministic rules with `requires_validation: false` use `not_required`;
- semantic variants, novel candidates, contextual/supporting rules, and quoted evidence use `pending` unless validation is enabled;
- validation produces `confirmed`, `rejected`, or `uncertain`.

Rejected findings remain in detailed audit output with `review_status=excluded_by_validation`, but do not affect active priority. Supporting findings use `supporting_only` and cannot independently affect priority. `validated_by` contains only the validation model and prompt version; catalogue and discovery provenance remain in run metadata and internal candidates.

## Priority

LLM reviewer priority is evidence-aware:

| Evidence | Priority |
|---|---|
| Strong deterministic known trace | High |
| Confirmed semantic variant of a strong rule | High |
| Confirmed contextual trace | Medium |
| Confirmed novel candidate | Medium |
| Pending or uncertain semantic/novel candidate | Low |
| Supporting-only | None independently |
| Rejected | None |

The LLM contribution is reduced to one priority signal before it enters the existing cross-detector risk engine, preventing multiple weak/supporting hits from becoming Medium by count alone.

## Finding and output contract

All paths use the existing public `Finding` dataclass.

| Path | `detector_type` | `check_type` |
|---|---|---|
| Deterministic known rule | `llm_response_trace` | `known_pattern` |
| Semantic known variant | `llm_response_trace` | `semantic_variant` |
| Novel occurrence | `llm_response_trace` | `novel_pattern_candidate` |

Legacy `check_type=llm_trace` is normalized to `known_pattern`.

Detailed CSV and Excel output retain all findings, including rejected and supporting findings. The compact `integrity_findings.csv` includes LLM source file, category, exact matched text, evidence, section, validation fields, rule ID, review status, and reporting-layer `title`, `editor_label`, and `editor_notes`. Template-specific columns remain available and may be blank for LLM findings.

## Configuration and failure behavior

- Neither flag: lossless preparation plus deterministic detection; no model client.
- `--detect-llm-semantic`: deterministic and semantic discovery; semantic findings remain pending without validation.
- `--validate-llm`: selectively validate eligible deterministic findings.
- Both flags: run discovery, fusion, and selective validation.

Semantic calls retry, split invalid multi-record batches, retain successful sub-batches, and record failed records without discarding deterministic findings. Run metadata records coverage and failure counts. The standalone `scripts/detect_llm_response_traces_llm.py` CLI uses the same reusable detector.

Only record ID, exact title, and preserved abstract blocks are sent to the model. Authors, affiliations, email addresses, and unrelated metadata are not included.

## Privacy

Semantic discovery and validation are disabled by default. ASCO data must not be sent to an endpoint until retention, request/response logging, access, model-training usage, deletion, encryption, processing region, and subprocessor terms are approved.

## Current limitations

- Novel candidate precision is not calibrated.
- Semantic family clustering (`LLM-NOVEL-FAM-*`) is not implemented.
- Rule promotion remains a manual, editor-confirmed process.
- Privacy approval is required before enabling model calls on ASCO content.
- Thresholds require labelled ASCO evaluation.
- Lossless reconstructed text preserves logical submitted text, not raw XML byte offsets.

Novel candidates are investigation signals. They are not automatically promoted to production rules and do not prove AI authorship or misconduct.
