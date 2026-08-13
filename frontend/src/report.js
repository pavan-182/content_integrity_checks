export const checks = [
  {
    id: "tortured_phrases",
    label: "Tortured Phrases",
    description: "Known paraphrase-evasion patterns detected in the abstract text.",
  },
  {
    id: "llm_response_trace",
    label: "LLM Response Trace",
    description: "Residual AI-assistant response language detected in the abstract text.",
  },
  {
    id: "numerical_contradiction",
    label: "Numerical Contradiction",
    description: "Conflicting numeric claims detected within the abstract text.",
  },
  {
    id: "design_contradiction",
    label: "Design Contradiction",
    description: "Conflicting study-design claims detected within the abstract text.",
  },
  {
    id: "unverifiable_trial",
    label: "Unverifiable Clinical Trial",
    description: "Trial-registration details could not be verified locally or against a registry.",
  },
  {
    id: "templating",
    label: "Templating (Cross-Author)",
    description: "Strong structural overlap with another submission was detected.",
  },
];

const validationChecks = new Set(["tortured_phrases", "llm_response_trace"]);

export function normalizeReport(report) {
  if (!report?.summary || !Array.isArray(report.abstracts)) {
    throw new Error("The pipeline report must contain summary and abstracts.");
  }

  return {
    ...report,
    abstracts: report.abstracts.map((abstract) => ({
      ...abstract,
      corresponding_author: abstract.corresponding_author || "Not provided",
      checks: Object.fromEntries(
        checks.map((check) => [
          check.id,
          abstract.checks?.[check.id] || { flagged: false, match_count: 0, evidence: "", reason: "", findings: [] },
        ]),
      ),
    })),
  };
}

export function validationRows(report) {
  return report.abstracts.flatMap((abstract) =>
    checks.flatMap((check) => {
      const result = abstract.checks?.[check.id];
      if (!validationChecks.has(check.id) || !result?.findings?.length) return [];
      return result.findings
        .filter((finding) => finding.validated_by)
        .map((finding) => ({
          finding_id: finding.finding_id || "",
          abstract_id: abstract.abstract_id,
          title: abstract.title,
          check_id: check.id,
          check_label: check.label,
          matched_phrase: finding.matched_phrase || "",
          expected_term: finding.expected_term || "",
          evidence_snippet: finding.evidence_snippet || "",
          section: finding.section || "",
          severity: finding.severity || "",
          confidence: finding.confidence ?? "",
          validation_status: finding.validation_status || "",
          validation_reason: finding.validation_reason || "",
          validated_by: finding.validated_by || "",
          rule_id: finding.rule_id || "",
        }));
    }),
  );
}
