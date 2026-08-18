export const checks = [
  {
    id: "tortured_phrases",
    label: "Nonsense and tortured phrases",
    description: "Known paraphrase-evasion patterns detected in the abstract text.",
  },
  {
    id: "llm_response_trace",
    label: "LLM response trace",
    description: "Residual AI-assistant response language detected in the abstract text.",
  },
  {
    id: "templating",
    label: "Templating",
    description: "Pair-level template evidence with record-level corroborating checks.",
  },
];

const validationChecks = new Set(["tortured_phrases", "llm_response_trace"]);

export function normalizeReport(report) {
  if (report?.summary && Array.isArray(report.abstracts)) return normalizeLegacyReport(report);
  if (!report || Array.isArray(report) || typeof report !== "object") {
    throw new Error("The pipeline report must be a DOI-keyed object.");
  }

  const abstracts = Object.entries(report).map(([recordKey, submission]) => {
    if (!submission || !Array.isArray(submission.checks)) {
      throw new Error(`Submission ${recordKey} must contain a checks array.`);
    }
    const byName = Object.fromEntries(submission.checks.map((check) => [check.check_name, check]));
    const summary = byName.content_integrity_summary?.result;
    const summaryData = summary?.supporting_data?.[0] || {};
    const template = byName.template_detection?.result || missingResult("Template result missing from the pipeline report.");
    const scopes = template.supporting_data || [];
    const pairScopes = scopes.filter((item) => item.evidence_scope === "PAIR");
    const recordScope = scopes.find((item) => item.evidence_scope === "RECORD") || {};

    return {
      abstract_id: submission.abstract_id,
      doi: recordKey,
      title: submission.title,
      corresponding_author: submission.corresponding_author || "Not provided",
      overall_risk: titleRisk(summaryData.overall_content_risk || summary?.level),
      overall_content_risk: titleRisk(summaryData.overall_content_risk || summary?.level),
      review_required: Boolean(summaryData.review_required),
      why_flagged: summary?.comment || "No review required.",
      review_reason: summary?.comment || "",
      finding_ids: summaryData.finding_ids || [],
      template_pair_ids: summaryData.template_pair_ids || [],
      operational_issues: summaryData.operational_issues || [],
      checks: {
        tortured_phrases: normalizeFindingCheck(byName.tortured_phrases?.result),
        llm_response_trace: normalizeFindingCheck(byName.llm_response_trace?.result),
        templating: {
          flagged: pairScopes.length > 0,
          review_candidate: false,
          match_count: pairScopes.length,
          evidence: pairScopes.map((pair) => `${pair.submitted_evidence} || ${pair.matched_evidence}`).filter(Boolean).join(" | "),
          reason: template.comment || "",
          findings: pairScopes,
          evidence_pairs: pairScopes.map((pair) => ({
            ...pair,
            submitted_abstract_id: submission.abstract_id,
            section: subCheckData(pair, "section_similarity")?.strongest_section || "",
            similarity: subCheckData(pair, "section_similarity")?.strongest_section_similarity || 0,
          })),
          supporting_checks: recordScope.supporting_checks || [],
        },
      },
    };
  });

  const riskCount = (risk) => abstracts.filter((item) => item.overall_risk === risk).length;
  return {
    run: {},
    summary: {
      total_submissions: abstracts.length,
      high_risk: riskCount("High"),
      moderate_risk: riskCount("Medium"),
      low_risk: riskCount("Low"),
      no_risk: riskCount("None"),
      requires_editor_judgement: abstracts.filter((item) => item.review_required).length,
      cleared_without_manual_review: abstracts.filter((item) => !item.review_required).length,
    },
    abstracts,
  };
}

function normalizeLegacyReport(report) {

  return {
    ...report,
    abstracts: report.abstracts.map((abstract) => ({
      ...abstract,
      corresponding_author: abstract.corresponding_author || "Not provided",
      checks: Object.fromEntries(
        checks.map((check) => [
          check.id,
          abstract.checks?.[check.id] || {
            flagged: false,
            review_candidate: false,
            match_count: 0,
            evidence: "",
            reason: "Check result missing from the pipeline report.",
            findings: [],
          },
        ]),
      ),
    })),
  };
}

function missingResult(comment) {
  return { level: "UNKNOWN", supporting_data: [], comment };
}

function titleRisk(value) {
  const normalized = String(value || "NONE").toUpperCase();
  return normalized === "NONE" ? "None" : `${normalized[0]}${normalized.slice(1).toLowerCase()}`;
}

function isFlagged(result) {
  return Boolean(result?.supporting_data?.some((finding) => finding.active !== false));
}

function normalizeFindingCheck(result = missingResult("Check result missing from the pipeline report.")) {
  const findings = result.supporting_data || [];
  return {
    flagged: isFlagged(result),
    review_candidate: findings.some((finding) => finding.review_candidate),
    match_count: findings.filter((finding) => finding.active !== false).length,
    evidence: result.comment || "",
    reason: result.comment || "",
    findings,
  };
}

function subCheckData(pair, name) {
  return pair.sub_checks?.find((check) => check.check_name === name)?.result?.supporting_data?.[0];
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
          doi: abstract.doi || "",
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
