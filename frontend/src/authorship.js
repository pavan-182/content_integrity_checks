export const authorChecks = [
  {
    id: "submission_volume",
    label: "Submission Volume",
    description: "Authors who appear on more submissions in this cycle than the configured threshold.",
  },
  {
    id: "author_count_deviation",
    label: "Author Count Deviation",
    description: "Submissions whose author count is significantly above or below the dataset average.",
  },
  {
    id: "affiliance_relevance",
    label: "Affiliation Relevance",
    description: "Affiliations that could not be verified or belong to institutions with notable retraction rates.",
  },
  {
    id: "author_network",
    label: "Author Network",
    description: "Author groups that have repeatedly co-authored together across multiple submissions.",
  },
  {
    id: "retraction_history",
    label: "Retraction History",
    description: "Authors with prior appearances in the Retraction Watch database.",
  },
];

export function normalizeAuthorshipReport(report) {
  if (report?.summary && Array.isArray(report.abstracts)) return normalizeLegacyAuthorshipReport(report);
  if (!report || Array.isArray(report) || typeof report !== "object") {
    throw new Error("The authorship report must be a DOI-keyed object.");
  }

  const abstracts = Object.entries(report).map(([doi, submission]) => {
    if (!submission || !Array.isArray(submission.checks)) {
      throw new Error(`Submission ${doi} must contain a checks array.`);
    }
    const byName = Object.fromEntries(submission.checks.map((check) => [check.check_name, check]));
    const checks = Object.fromEntries(
      authorChecks.map((definition) => [definition.id, normalizeAuthorCheck(byName[definition.id]?.result, definition.id)]),
    );
    const levels = authorChecks.map((definition) => checks[definition.id].level);
    const overallRisk = highestRisk(levels);
    const flaggedChecks = authorChecks.filter((definition) => checks[definition.id].flagged);

    return {
      doi,
      abstract_id: submission.abstract_id,
      title: submission.title,
      corresponding_author: submission.corresponding_author || "Not provided",
      overall_author_risk: overallRisk,
      author_review_required: overallRisk === "High" || overallRisk === "Medium",
      why_flagged: flaggedChecks.length
        ? flaggedChecks.map((definition) => definition.label).join(", ")
        : "No active authorship finding.",
      checks,
    };
  });

  const riskCount = (risk) => abstracts.filter((item) => item.overall_author_risk === risk).length;
  return {
    summary: {
      total_submissions: abstracts.length,
      high_risk: riskCount("High"),
      moderate_risk: riskCount("Medium"),
      low_risk: riskCount("Low"),
      no_risk: riskCount("None"),
      requires_review: abstracts.filter((item) => item.author_review_required).length,
    },
    abstracts,
  };
}

function normalizeLegacyAuthorshipReport(report) {
  return {
    ...report,
    abstracts: report.abstracts.map((abstract) => ({
      ...abstract,
      corresponding_author: abstract.corresponding_author || "Not provided",
      checks: Object.fromEntries(
        authorChecks.map((definition) => [
          definition.id,
          abstract.checks?.[definition.id] || missingAuthorCheck("Check result missing from the authorship report."),
        ]),
      ),
    })),
  };
}

function missingAuthorCheck(comment) {
  return {
    flagged: false,
    level: "Unknown",
    match_count: 0,
    evidence: "",
    reason: comment,
    findings: [],
  };
}

function normalizeAuthorCheck(result, checkId) {
  if (!result) return missingAuthorCheck("Check result missing from the authorship report.");
  const findings = result.supporting_data || [];
  const level = titleRisk(result.level);
  const flagged = result.level === "HIGH" || result.level === "MEDIUM";
  return {
    flagged,
    level,
    match_count: findings.length,
    evidence: result.comment || authorEvidenceSummary(checkId, { findings, comment: result.comment }),
    reason: result.comment || "",
    findings,
  };
}

function titleRisk(value) {
  const normalized = String(value || "NONE").toUpperCase();
  if (normalized === "NONE" || normalized === "UNKNOWN") return normalized === "UNKNOWN" ? "Unknown" : "None";
  return `${normalized[0]}${normalized.slice(1).toLowerCase()}`;
}

function highestRisk(levels) {
  const order = ["High", "Medium", "Low", "None", "Unknown"];
  for (const risk of order) {
    if (levels.includes(risk)) return risk;
  }
  return "None";
}

export function hasAuthorFinding(item) {
  return authorChecks.some((check) => item.checks[check.id].flagged);
}

export function authorAffectedCount(abstracts) {
  return abstracts.filter(hasAuthorFinding).length;
}

export function authorEvidenceSummary(checkId, result) {
  const findings = result.findings || [];
  switch (checkId) {
    case "submission_volume":
      return findings
        .map((finding) => {
          const author = finding.authors?.full_name || "Unknown author";
          const count = finding.dois?.length || 0;
          return `${author} · ${count} related submission${count === 1 ? "" : "s"}`;
        })
        .join(" | ");
    case "author_count_deviation": {
      const stats = findings[0];
      if (!stats) return result.comment || "";
      return `Average ${stats.avg_count}, found ${stats.found_count}`;
    }
    case "affiliance_relevance":
      return findings
        .map((finding) => `${finding.author || "Author"} · ${finding.institution_name || "Unknown institution"}`)
        .join(" | ");
    case "author_network":
      return findings
        .map((finding) => {
          const names = (finding.authors || []).join(" & ");
          return `${names} · ${finding.co_occurrence_count || 0} co-submission${finding.co_occurrence_count === 1 ? "" : "s"}`;
        })
        .join(" | ");
    case "retraction_history":
      return findings
        .map((finding) => `${finding.matched_name || "Author"} · ${finding.retraction_record?.journal || "Retraction Watch match"}`)
        .join(" | ");
    default:
      return result.comment || "";
  }
}

export function emptyAuthorshipReport() {
  return normalizeAuthorshipReport({});
}
