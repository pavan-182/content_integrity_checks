# Fix Plan for Codex — ASCO Integrity Pipeline Gaps

Four work items, ordered by dependency. Each has scope, files touched, and a concrete acceptance test so the agent can self-verify before moving to the next item.

---

## 0. Ground rules for the agent

- Do not change existing detector output schemas (`Finding`, `ParsedRecord`, cluster fields) — only add fields, never rename/remove.
- Every new capability must be annotate-only or additive to `risk_engine.py` — do not silently change existing High/Medium/Low outcomes for already-passing test cases.
- New code gets new unit tests in `tests/`, following the existing test file naming convention (`test_<module>.py`).
- Run `python -m unittest discover -s tests -v` after each item before proceeding to the next.

---

## Item 1 — Synthetic evaluation harness (do this first)

**Why first:** thresholds for template detection and future Level B tuning both depend on labelled ground truth. Build once, reuse for items 2 and 3.

**New files:**
```
tests/fixtures/eval_corpus/
├── positives/
│   ├── llm_trace_*.xml          (5-8 files, traces injected in different sections/fields)
│   ├── template_family_*/       (3-4 families, 3-5 members each: disease/drug/number swapped, structure held constant)
│   └── tortured_phrase_*.xml    (5-8 files, known fingerprint substituted into otherwise clean sentence)
├── negatives/
│   ├── clean_*.xml              (10+ normal abstracts)
│   ├── similar_legit_*/         (2-3 groups of legitimately similar multicentre-trial abstracts — same protocol language, different sites/data — must NOT cluster as template)
│   └── markdown_lookalike_*.xml (abstracts with numbered lists / headings that are NOT LLM residue)
└── labels.json                  (abstract_id -> expected_finding_types, expected_cluster_membership, expected_risk)
```

**New module:** `scripts/run_eval.py`
- Runs the pipeline against `tests/fixtures/eval_corpus/` only.
- Compares output against `labels.json`.
- Reports per-detector precision/recall/false-positive list (not just pass/fail) — print abstract IDs that were wrongly flagged or wrongly missed.
- Evaluates the production pair findings, visible families, and abstract flags directly. `--legacy-similarity-threshold` applies only to the temporary legacy comparison.

**Acceptance:**
- `python scripts/run_eval.py` runs clean, prints a report.
- The `similar_legit_*` negatives do not appear in any template cluster at the chosen threshold.
- All injected positives are recovered.
- Add one paragraph to `docs/EVALUATION_PLAN.md` (new file) stating what this harness does and does not prove — reuse the "what synthetic data can/cannot validate" framing already in the design doc, section 8.

---

## Item 2 — Level B: candidate nonsense/tortured-wording detector

**Scope:** dictionary-miss detection only. Never overrides or auto-confirms — same annotate/candidate pattern as `context_validator.py`.

**New file:** `content_integrity/detectors/nonsense_candidate.py`

Logic:
1. Run existing Level A dictionary matcher first (unchanged).
2. On sentences with **no** Level A match, apply cheap deterministic pre-filters before touching an LLM:
   - biomedical entity co-occurrence check (two entities that don't semantically pair — use a small allow-list of known valid drug-disease/gene-biomarker pairs already implied by the masking vocabulary in `template_detection.py`; reuse that entity list, don't build a new one)
   - minimum phrase length, exclude author/affiliation/reference sections entirely (see Item 4 — build that exclusion first, this detector depends on it)
   - skip sentences that are just section headers or boilerplate (reuse the boilerplate exclusion list from `template_detection.py`)
3. Sentences surviving the filters go to GPT-OSS via the **same** IntelliHub client used in `context_validator.py` (reuse the client, retry/backoff, and JSON-strict prompt pattern — don't fork a second HTTP client).
4. Prompt returns: `understandable: bool`, `suspected_phrase: str`, `explanation: str`, `confidence: enum`.
5. Wrap result as a `Finding` with:
   - `check_type = "nonsense_candidate"`
   - `severity = "low"` always (never high/medium — this is uncertain by construction)
   - `confidence` from model output
   - explicit evidence text = the flagged sentence

**risk_engine.py change:** add one line — `nonsense_candidate` findings count toward the existing "multiple low-severity findings → Medium" rule, nothing else. Do not add a new risk tier.

**Config flag:** gate behind `--detect-nonsense-candidates` (default off), same pattern as `--validate-llm`, since it costs API calls per sentence across 6,000 abstracts — this should be opt-in, not default-on.

**Acceptance:**
- New test file `tests/test_nonsense_candidate.py`: feed 3 clean sentences (expect no finding), 3 nonsense-injected sentences from the eval corpus (expect finding with `confidence` populated).
- Run `run_eval.py` — confirm no legit oncology sentences from `negatives/clean_*` get flagged.
- Update `docs/DETECTOR_RULES.md` (or create it if it doesn't exist) with the same explicit "must not classify the whole abstract" boundary already stated for the LLM trace validator.

---

## Item 3 — Dashboard sheet in the Excel workbook

**File:** `content_integrity/reporting.py`

Add one new sheet, `Dashboard`, built purely from data already computed in-memory during the run (no new detector calls, no new pass over records):

Tables to include (as separate small blocks on one sheet, stacked vertically, so it's genuinely pivot-usable — not a single pre-baked pivot table that hides the source rows):
1. Abstracts by review priority (High/Medium/Low/None) — count
2. Findings by check_type (llm_trace / tortured_phrase / template / nonsense_candidate if Item 2 shipped)
3. Template cluster count, and cluster-size distribution (count of clusters by size: 2, 3, 4, 5+)
4. Largest 10 clusters by size, with representative abstract ID
5. Parse failures / warnings count by warning type
6. Findings by abstract section (Background/Methods/Results/Conclusions/Title)

Implementation: build these as plain pandas `groupby().size()` on the same dataframes already assembled for `Abstract Summary`, `Integrity Findings`, and `Template Clusters` — don't requery raw records.

**Acceptance:**
- New test `tests/test_dashboard_sheet.py`: run pipeline on eval corpus, assert `Dashboard` sheet exists, assert row counts in each table sum correctly against the other sheets (e.g., sum of priority counts == total record count).
- Manual check: open the workbook, confirm sheet is present and each table has a clear header row (Excel filter/pivot needs clean single-row headers, not merged cells).

---

## Item 4 — Explicit author/affiliation/reference exclusion

**Do this before Item 2**, since Item 2 depends on it, but it's small enough to land as its own commit first.

**File:** `content_integrity/xml_parser.py` or `content_integrity/utils.py` (wherever section text is assembled into `combined raw detector text`)

Change:
- Confirm today: does `full_text` / detector input already exclude author lists, affiliations, and reference/citation blocks? If XML tags for these (e.g. `contrib-group`, `aff`, `ref-list`) are currently included in the text handed to detectors, exclude them explicitly at parse time — same way `table-wrap` is already excluded per section 4.2 of the design doc.
- Add a `excluded_sections: list[str]` field to `ParsedRecord` logging what was stripped, for auditability (same spirit as existing `warnings` field).

**Acceptance:**
- New test `tests/test_author_exclusion.py`: XML fixture with an author named e.g. "Chen J" and an LLM-trace phrase embedded only inside the reference list — assert the LLM trace detector does NOT fire on that reference-list content, and does fire when the same phrase is placed in the abstract body.
- Confirm existing tests still pass (this is a behavior change to text extraction, so re-run full suite, not just new test).

---

## Build order for Codex

```
1. Item 4 (author/affiliation/reference exclusion)   — small, unblocks Item 2
2. Item 1 (eval corpus + run_eval.py)                 — needed to validate everything after
3. Item 3 (dashboard sheet)                            — no dependencies, quick win, do while corpus work settles
4. Item 2 (nonsense candidate detector)                — depends on 1 and 4
5. Full suite + run_eval.py final pass
6. Update docs: EVALUATION_PLAN.md, DETECTOR_RULES.md, bump pipeline doc's "Current limitations" section to remove whichever items are now resolved
```

One instruction worth giving Codex directly: **do not touch `risk_engine.py`'s existing High/Medium/Low thresholds for LLM-trace and tortured-phrase findings** — those are proven; only the new nonsense-candidate signal and its narrow addition to the "multiple low severity" rule should change.
