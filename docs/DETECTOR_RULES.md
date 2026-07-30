# Detector Rules and Boundaries

## Level A — known tortured phrases

The deterministic tortured-phrase detector checks titles and abstract text against the configured CSV dictionary. It honors supported `AND`, `OR`, and `NOT` context clauses, records the matched and expected phrases, and emits auditable medium/high-confidence findings. It cannot discover wording absent from the dictionary.

## Level B — nonsense candidates

The opt-in `--detect-nonsense-candidates` detector looks only for possible dictionary misses. It first removes sentences already matched by Level A, skips short text, headings, boilerplate, and parser-excluded author/affiliation/reference content, then uses the gene and drug patterns already maintained by template detection as a cheap biomedical co-occurrence gate. Only surviving individual sentences are sent to GPT-OSS through the existing IntelliHub client.

GPT-OSS must return whether that sentence is understandable, the shortest suspected phrase, one explanation, and low/medium/high confidence. A rejected sentence becomes an additive `nonsense_candidate` finding with low severity and the complete sentence as evidence. Model errors and understandable sentences create no finding.

This detector **must not classify the whole abstract**, infer AI authorship, judge scientific quality, or auto-confirm misconduct. It proposes low-severity review candidates only. One candidate produces Low record risk; multiple low-severity findings use the existing Medium rule, and validation never overrides deterministic Level A findings.

## LLM response traces

The deterministic LLM-trace detector loads one shared YAML catalogue and matches explicit assistant residue, prompt leakage, conversation/interface text, and supporting formatting against preserved source blocks. The opt-in semantic layer can add source-verified known variants and novel occurrence candidates. It does not classify ordinary prose as AI-generated text. Quoted examples are retained for validation, not silently discarded.

Supporting-only and validator-rejected findings remain in audit output but do not independently affect reviewer priority. See `docs/LLM_RESPONSE_TRACE_PIPELINE.md`.

## Template clusters

Template detection compares masked and original wording only within the current batch. Every cluster remains a `candidate` with component scores and metadata context for editorial review; similarity alone is not evidence of misconduct.
