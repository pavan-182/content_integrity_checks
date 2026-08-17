import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { normalizeReport, validationRows } from "./report.js";

test("maps the pipeline report into dashboard records", async () => {
  const source = new URL("../../outputs/test_real/content_integrity_results.json", import.meta.url);
  const raw = JSON.parse(await readFile(source, "utf8"));
  const report = normalizeReport(raw);

  assert.equal(report.abstracts.length, report.summary.total_submissions);
  assert.equal(report.abstracts.filter((item) => item.overall_risk === "High").length, report.summary.high_risk);
  assert.ok(report.abstracts.every((item) => Object.keys(item.checks).length === 3));
  assert.ok(report.abstracts.every((item) => item.corresponding_author));

  const flagged = report.abstracts.flatMap((item) =>
    Object.values(item.checks).filter((check) => check.flagged).map((check) => ({ item, check })),
  );
  assert.ok(flagged.length > 0);
  assert.ok(flagged.every(({ check }) => check.evidence));
  assert.ok(flagged.every(({ item, check }) => check.evidence !== item.why_flagged));
  assert.equal(validationRows(report).length, 0);
});

test("extracts validator judgments for the evals tab", () => {
  const report = normalizeReport({
    summary: { total_submissions: 1 },
    abstracts: [
      {
        abstract_id: "A-1",
        title: "Example",
        corresponding_author: "Author",
        checks: {
          tortured_phrases: {
            flagged: true,
            match_count: 1,
            evidence: "nervous network → neural network",
            findings: [
              {
                matched_phrase: "nervous network",
                evidence_snippet: "nervous network in the background section",
                validation_status: "rejected",
                validation_reason: "Confirmed as a phrase match only.",
                validated_by: "gpt-oss-20b:context_validator_v2",
                rule_id: "TP-001",
              },
            ],
          },
        },
      },
    ],
  });

  assert.deepEqual(validationRows(report), [
    {
      finding_id: "",
      abstract_id: "A-1",
      title: "Example",
      check_id: "tortured_phrases",
      check_label: "Tortured Phrases",
      matched_phrase: "nervous network",
      expected_term: "",
      evidence_snippet: "nervous network in the background section",
      section: "",
      severity: "",
      confidence: "",
      validation_status: "rejected",
      validation_reason: "Confirmed as a phrase match only.",
      validated_by: "gpt-oss-20b:context_validator_v2",
      rule_id: "TP-001",
    },
  ]);
});

test("preserves authoritative risk and treats a missing check as unknown", () => {
  const report = normalizeReport({
    summary: { total_submissions: 1 },
    abstracts: [{
      abstract_id: "A-1",
      title: "Rejected finding",
      overall_risk: "None",
      review_required: false,
      checks: {
        tortured_phrases: {
          flagged: false,
          findings: [{ severity: "high", validation_status: "rejected", active: false }],
        },
      },
    }],
  });

  assert.equal(report.abstracts[0].overall_risk, "None");
  assert.equal(report.abstracts[0].checks.tortured_phrases.flagged, false);
  assert.equal(report.abstracts[0].checks.templating.operational_failure, true);
});

test("normalizes DOI-keyed template sub-checks and record support", () => {
  const report = normalizeReport({
    "10.1000/example": {
      title: "Example",
      abstract_id: "A-1",
      checks: [
        {
          check_name: "content_integrity_summary",
          result: {
            level: "HIGH",
            supporting_data: [{ overall_content_risk: "HIGH", review_required: true }],
            comment: "LLM Response Trace, Template Detection",
          },
        },
        { check_name: "tortured_phrases", result: { level: "LOW", supporting_data: [], comment: "Clear." } },
        { check_name: "llm_response_trace", result: { level: "LOW", supporting_data: [], comment: "Clear." } },
        {
          check_name: "template_detection",
          result: {
            level: "HIGH",
            comment: "Possible template reuse.",
            supporting_data: [
              {
                evidence_scope: "PAIR",
                pair_id: "PAIR-A-1--B-1",
                matched_abstract_id: "B-1",
                submitted_evidence: "Left text",
                matched_evidence: "Right text",
                sub_checks: [{
                  check_name: "section_similarity",
                  result: { supporting_data: [{ strongest_section: "results", strongest_section_similarity: 0.96 }] },
                }],
              },
              {
                evidence_scope: "RECORD",
                supporting_checks: [{
                  check_name: "numerical_contradiction",
                  evidence_role: "CORROBORATING",
                  result: { level: "MEDIUM", supporting_data: [{ finding_id: "F-1" }], comment: "Mismatch." },
                }],
              },
            ],
          },
        },
      ],
    },
  });

  assert.equal(report.summary.total_submissions, 1);
  assert.equal(report.abstracts[0].overall_risk, "High");
  assert.equal(report.abstracts[0].why_flagged, "LLM Response Trace, Template Detection");
  assert.equal(report.abstracts[0].checks.templating.evidence_pairs[0].matched_abstract_id, "B-1");
  assert.equal(report.abstracts[0].checks.templating.evidence_pairs[0].section, "results");
  assert.equal(report.abstracts[0].checks.templating.supporting_checks[0].check_name, "numerical_contradiction");
});
