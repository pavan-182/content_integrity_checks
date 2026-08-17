import assert from "node:assert/strict";
import test from "node:test";
import {
  authorAffectedCount,
  authorEvidenceSummary,
  emptyAuthorshipReport,
  hasAuthorFinding,
  normalizeAuthorshipReport,
} from "./authorship.js";

test("maps DOI-keyed authorship report into dashboard records", () => {
  const report = normalizeAuthorshipReport({
    "10.1000/example": {
      title: "Example paper",
      abstract_id: "A-1",
      checks: [
        {
          check_name: "submission_volume",
          result: {
            level: "HIGH",
            supporting_data: [{
              authors: { full_name: "Jane Doe" },
              dois: ["10.1000/other-1", "10.1000/other-2"],
            }],
            comment: "Jane Doe submitted 3 times.",
          },
        },
        { check_name: "author_count_deviation", result: { level: "LOW", supporting_data: [{ avg_count: 7, found_count: 5 }], comment: "" } },
        { check_name: "affiliance_relevance", result: { level: "LOW", supporting_data: [], comment: "" } },
        { check_name: "author_network", result: { level: "LOW", supporting_data: [], comment: "" } },
        { check_name: "retraction_history", result: { level: "LOW", supporting_data: [], comment: "" } },
      ],
    },
  });

  assert.equal(report.summary.total_submissions, 1);
  assert.equal(report.summary.high_risk, 1);
  assert.equal(report.abstracts[0].overall_author_risk, "High");
  assert.equal(report.abstracts[0].checks.submission_volume.flagged, true);
  assert.equal(report.abstracts[0].checks.author_count_deviation.flagged, false);
  assert.ok(hasAuthorFinding(report.abstracts[0]));
  assert.equal(authorAffectedCount(report.abstracts), 1);
});

test("summarizes evidence for each author check", () => {
  assert.match(
    authorEvidenceSummary("submission_volume", {
      findings: [{ authors: { full_name: "Jane Doe" }, dois: ["10.1/a", "10.1/b"] }],
    }),
    /Jane Doe · 2 related submissions/,
  );
  assert.equal(
    authorEvidenceSummary("author_count_deviation", { findings: [{ avg_count: 7, found_count: 14 }] }),
    "Average 7, found 14",
  );
  assert.match(
    authorEvidenceSummary("affiliance_relevance", {
      findings: [{ author: "Jane Doe", institution_name: "Example University" }],
    }),
    /Jane Doe · Example University/,
  );
  assert.match(
    authorEvidenceSummary("author_network", {
      findings: [{ authors: ["Jane Doe", "John Smith"], co_occurrence_count: 4 }],
    }),
    /Jane Doe & John Smith · 4 co-submissions/,
  );
  assert.match(
    authorEvidenceSummary("retraction_history", {
      findings: [{ matched_name: "Jane Doe", retraction_record: { journal: "Example Journal" } }],
    }),
    /Jane Doe · Example Journal/,
  );
});

test("returns an empty authorship report", () => {
  const report = emptyAuthorshipReport();
  assert.equal(report.summary.total_submissions, 0);
  assert.deepEqual(report.abstracts, []);
});
