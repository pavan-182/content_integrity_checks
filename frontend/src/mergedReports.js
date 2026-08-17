const NO_CONTENT_REASON = "No review required.";
const NO_AUTHOR_REASON = "No active authorship finding.";

function mergeWhyFlagged(...reasons) {
  const meaningful = reasons
    .map((reason) => String(reason || "").trim())
    .filter((reason) => reason && reason !== NO_CONTENT_REASON && reason !== NO_AUTHOR_REASON);

  if (meaningful.length) {
    return [...new Set(meaningful)].join(", ");
  }

  return reasons.find((reason) => String(reason || "").trim()) || NO_CONTENT_REASON;
}

function indexByAbstractId(abstracts = []) {
  return new Map(abstracts.map((abstract) => [abstract.abstract_id, abstract]));
}

export function mergeReports(contentReport, authorshipReport) {
  const contentById = indexByAbstractId(contentReport?.abstracts);
  const authorshipById = indexByAbstractId(authorshipReport?.abstracts);

  const content = {
    ...contentReport,
    abstracts: (contentReport?.abstracts || []).map((abstract) => {
      const authorship = authorshipById.get(abstract.abstract_id);
      return {
        ...abstract,
        authorship_why_flagged: authorship?.why_flagged || "",
        why_flagged: mergeWhyFlagged(abstract.why_flagged, authorship?.why_flagged),
      };
    }),
  };

  const authorship = {
    ...authorshipReport,
    abstracts: (authorshipReport?.abstracts || []).map((abstract) => {
      const contentAbstract = contentById.get(abstract.abstract_id);
      return {
        ...abstract,
        content_why_flagged: contentAbstract?.why_flagged || "",
        why_flagged: mergeWhyFlagged(contentAbstract?.why_flagged, abstract.why_flagged),
      };
    }),
  };

  return { content, authorship };
}
