import assert from "node:assert/strict";
import test from "node:test";
import { mergeReports } from "./mergedReports.js";

test("merges content and authorship why_flagged values at the report root", () => {
  const merged = mergeReports(
    {
      abstracts: [
        { abstract_id: "A-1", why_flagged: "LLM Response Trace, Template Detection" },
        { abstract_id: "A-2", why_flagged: "No review required." },
      ],
    },
    {
      abstracts: [
        { abstract_id: "A-1", why_flagged: "Submission Volume, Retraction History" },
        { abstract_id: "A-2", why_flagged: "No active authorship finding." },
      ],
    },
  );

  assert.equal(
    merged.content.abstracts[0].why_flagged,
    "LLM Response Trace, Template Detection, Submission Volume, Retraction History",
  );
  assert.equal(
    merged.authorship.abstracts[0].why_flagged,
    "LLM Response Trace, Template Detection, Submission Volume, Retraction History",
  );
  assert.equal(merged.content.abstracts[1].why_flagged, "No review required.");
  assert.equal(merged.authorship.abstracts[1].why_flagged, "No review required.");
});
