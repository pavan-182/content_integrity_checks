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
  assert.ok(report.abstracts.every((item) => Object.keys(item.checks).length === 6));
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
  assert.equal(report.abstracts[0].checks.design_contradiction.operational_failure, true);
});
