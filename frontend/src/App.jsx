import { useEffect, useMemo, useRef, useState } from "react";
import {
  authorAffectedCount,
  authorChecks,
  authorEvidenceSummary,
  emptyAuthorshipReport,
  hasAuthorFinding,
  normalizeAuthorshipReport,
} from "./authorship.js";
import { mergeReports } from "./mergedReports.js";
import { checks, normalizeReport, validationRows } from "./report.js";

const pages = [
  ["overview", "▦", "Overview"],
  ["content", "◫", "Content Checks"],
  ["authors", "◉", "Author Checks"],
  ["evals", "⚑", "Evals"],
  ["how", "?", "How It Works"],
];

const number = new Intl.NumberFormat();
const MAX_RETRACTION_HISTORY = 10;
const formatDate = (value) =>
  value
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
    : "Unknown";

function RiskBadge({ risk }) {
  return <span className={`risk risk-${risk.toLowerCase()}`}>{risk === "None" ? "None · no active risk" : risk}</span>;
}

function CheckStatus({ definition, result }) {
  if (result.review_candidate && !result.flagged) return <span className="status-flag">● Editor review candidate</span>;
  if (!result.flagged) return <span className="status-clear">✓ No active finding</span>;
  const count = definition.id === "templating"
    ? result.evidence_pairs?.length || result.match_count || 0
    : result.match_count || result.findings?.length || 0;
  const unit = definition.id === "templating" ? `pair${count === 1 ? "" : "s"}` : `finding${count === 1 ? "" : "s"}`;
  return <span className="status-flag">● Finding detected{count ? ` · ${count} ${unit}` : ""}</span>;
}

function AuthorCheckStatus({ result }) {
  if (!result.flagged) return <span className="status-clear">✓ No active finding</span>;
  const count = result.match_count || result.findings?.length || 0;
  return <span className="status-flag">● {result.level} · {count} finding{count === 1 ? "" : "s"}</span>;
}

function Metric({ label, value, help, onClick, active }) {
  const Tag = onClick ? "button" : "article";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      className={`metric-card${onClick ? " metric-card-btn" : ""}${active ? " active" : ""}`}
      onClick={onClick}
      aria-pressed={onClick ? active : undefined}
    >
      <div className="metric-label">{label}</div>
      <div className="metric-value">{number.format(value)}</div>
      <div className="metric-help">{help}</div>
    </Tag>
  );
}

function SubmissionTable({ items, onOpen, evidenceCheck, showCheckFlags = false }) {
  if (!items.length) return <div className="empty">No submissions match this view.</div>;

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Priority</th><th>Submission</th><th>Abstract ID</th>
            <th>{evidenceCheck ? "Evidence" : "Why flagged"}</th>
            {evidenceCheck === "templating" && <th>Reason</th>}
            {showCheckFlags && checks.map((check) => <th key={check.id}>{check.label}</th>)}
            <th><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.abstract_id}>
              <td><RiskBadge risk={item.overall_risk} /></td>
              <td className="title-cell"><strong>{item.title}</strong><span className="secondary">{item.doi}</span></td>
              <td>{item.abstract_id}</td>
              <td>{evidenceCheck === "templating"
                ? `${item.checks.templating.evidence_pairs?.length || item.checks.templating.match_count || 0} matched pair(s) — open for evidence`
                : evidenceCheck ? item.checks[evidenceCheck].evidence : item.why_flagged}</td>
              {evidenceCheck === "templating" && <td>{item.checks.templating.reason || "—"}</td>}
              {showCheckFlags && checks.map((check) => <td key={check.id}><CheckStatus definition={check} result={item.checks[check.id]} /></td>)}
              <td><button className="open-btn" onClick={() => onOpen(item.abstract_id)}>Open</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TableCard({ title, children, actions }) {
  return (
    <section className="table-card">
      <div className="table-head"><h2>{title}</h2>{actions}</div>
      {children}
    </section>
  );
}

function Overview({ report, onOpen }) {
  const { summary, abstracts } = report;
  const [showQueue, setShowQueue] = useState(false);

  return (
    <>
      <PageHead title="Editor Triage Overview" subtitle="See what requires editor judgement, then move into the review queue." />
      <div className="metrics">
        <Metric label="Total submissions" value={summary.total_submissions} help="Every abstract appears once" />
        <Metric
          label="Needs editor judgement"
          value={summary.requires_editor_judgement}
          help="Moderate + High · click to view queue"
          onClick={() => setShowQueue((value) => !value)}
          active={showQueue}
        />
        <Metric label="Cleared automatically" value={summary.cleared_without_manual_review} help="Review not required" />
      </div>
      {showQueue && <ReviewQueue abstracts={abstracts} onOpen={onOpen} />}
    </>
  );
}

function ReviewQueue({ abstracts, onOpen }) {
  const [risk, setRisk] = useState("High");
  const [query, setQuery] = useState("");
  const risks = ["High", "Medium", "Low", "None"];
  const items = abstracts.filter((item) => item.review_required && item.overall_risk === risk && matches(item, query));

  return (
    <>
      <PageHead title="Review Queue" subtitle="High, Moderate and Low pipeline results in one editor view." />
      <div className="queue-tabs">
        {risks.map((item) => <button key={item} className={`tab-btn ${risk === item ? "active" : ""}`} onClick={() => setRisk(item)}>{item} ({abstracts.filter((a) => a.review_required && a.overall_risk === item).length})</button>)}
      </div>
      <TableCard title={`${number.format(items.length)} ${risk.toLowerCase()} priority submissions`} actions={<Search value={query} onChange={setQuery} />}>
        <SubmissionTable items={items} onOpen={onOpen} />
      </TableCard>
    </>
  );
}

function ContentChecks({ abstracts, onOpen }) {
  const [active, setActive] = useState("All");
  const affected = abstracts.filter((item) => active === "All" ? hasContentFinding(item) : item.checks[active].flagged);

  return (
    <>
      <PageHead title="Content Integrity Checks" subtitle="Checks applied to abstract text and cross-submission content." />
      <div className="check-grid content-check-grid">
        <button className={`check-tile ${active === "All" ? "active" : ""}`} onClick={() => setActive("All")}><strong>All content findings</strong><span>{number.format(affectedCount(abstracts))} submissions</span></button>
        {checks.map((check) => <button key={check.id} className={`check-tile ${active === check.id ? "active" : ""}`} onClick={() => setActive(check.id)}><strong>{check.label}</strong><span>{number.format(abstracts.filter((item) => item.checks[check.id].flagged).length)} active</span></button>)}
      </div>
      <TableCard title={active === "All" ? "Affected submissions" : checks.find((item) => item.id === active).label}>
        <SubmissionTable items={affected} onOpen={onOpen} evidenceCheck={active === "All" ? undefined : active} />
      </TableCard>
    </>
  );
}

function AuthorSubmissionTable({ items, onOpen, evidenceCheck }) {
  if (!items.length) return <div className="empty">No submissions match this author check.</div>;

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Priority</th>
            <th>Submission</th>
            <th>Abstract ID</th>
            <th>{evidenceCheck ? "Evidence" : "Why flagged"}</th>
            <th><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.abstract_id}>
              <td><RiskBadge risk={item.overall_author_risk} /></td>
              <td className="title-cell"><strong>{item.title}</strong><span className="secondary">{item.doi}</span></td>
              <td>{item.abstract_id}</td>
              <td>{evidenceCheck
                ? authorEvidenceSummary(evidenceCheck, item.checks[evidenceCheck])
                : item.why_flagged}</td>
              <td><button className="open-btn" onClick={() => onOpen(item.abstract_id)}>Open</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AuthorChecks({ abstracts, onOpen }) {
  const [active, setActive] = useState("All");
  const affected = abstracts.filter((item) => active === "All" ? hasAuthorFinding(item) : item.checks[active].flagged);

  return (
    <>
      <PageHead title="Author Integrity Checks" subtitle="Checks applied to authorship patterns, affiliations, and prior retraction history." />
      <div className="check-grid author-check-grid">
        <button className={`check-tile ${active === "All" ? "active" : ""}`} onClick={() => setActive("All")}><strong>All author findings</strong><span>{number.format(authorAffectedCount(abstracts))} submissions</span></button>
        {authorChecks.map((check) => <button key={check.id} className={`check-tile ${active === check.id ? "active" : ""}`} onClick={() => setActive(check.id)}><strong>{check.label}</strong><span>{number.format(abstracts.filter((item) => item.checks[check.id].flagged).length)} active</span></button>)}
      </div>
      <TableCard title={active === "All" ? "Affected submissions" : authorChecks.find((item) => item.id === active).label}>
        <AuthorSubmissionTable items={affected} onOpen={onOpen} evidenceCheck={active === "All" ? undefined : active} />
      </TableCard>
    </>
  );
}

function Evals({ report }) {
  const rows = useMemo(() => validationRows(report), [report]);

  return (
    <>
      <PageHead title="Evals" subtitle="Validation-layer judgments for tortured phrases and LLM response traces." />
      <TableCard title={`${number.format(rows.length)} validated findings`}>
        {!rows.length ? (
          <div className="empty">Run the pipeline with --validate-llm to populate this tab.</div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Submission</th>
                  <th>Abstract ID</th>
                  <th>Check</th>
                  <th>Matched text</th>
                  <th>Validation</th>
                  <th>Reason</th>
                  <th>Validated by</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.finding_id || `${row.abstract_id}-${row.check_id}-${row.rule_id}-${row.matched_phrase}`}>
                    <td className="title-cell"><strong>{row.title}</strong><span className="secondary">{row.doi}</span></td>
                    <td>{row.abstract_id}</td>
                    <td>{row.check_label}</td>
                    <td>{row.matched_phrase || row.evidence_snippet}</td>
                    <td>{row.validation_status || "not_validated"}</td>
                    <td>{row.validation_reason || "—"}</td>
                    <td>{row.validated_by || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </TableCard>
    </>
  );
}

function Detail({ abstract, authorship, onBack, focus = "content" }) {
  const contentSection = (
    <section className="detail-section">
      <h2>Content integrity</h2>
      {checks.map((check) => <Finding key={check.id} definition={check} result={abstract.checks[check.id]} />)}
    </section>
  );
  const authorSection = authorship ? (
    <section className="detail-section">
      <div className="section-title">
        <div>
          <h2>Author integrity</h2>
          <div className="domain-label"><RiskBadge risk={authorship.overall_author_risk} /> · {authorship.why_flagged}</div>
        </div>
      </div>
      {authorChecks.map((check) => <AuthorFinding key={check.id} definition={check} result={authorship.checks[check.id]} />)}
    </section>
  ) : null;

  return (
    <>
      <button className="back-btn" onClick={onBack}>← Back</button>
      <div className="detail-head">
        <div><div className="detail-title"><h1>{abstract.title}</h1><RiskBadge risk={abstract.overall_risk} /></div><div className="detail-meta">{abstract.abstract_id} · {abstract.corresponding_author}</div></div>
      </div>
      <section className="why-box"><h2>Why this submission is here</h2><p>{abstract.why_flagged}</p></section>
      {!!abstract.operational_issues?.length && (
        <section className="why-box"><h2>Operational issues</h2><p>{abstract.operational_issues.map((issue) => `${issue.component}: ${issue.message}`).join(" · ")}</p></section>
      )}
      {focus === "authors" ? <>{authorSection}{contentSection}</> : <>{contentSection}{authorSection}</>}
    </>
  );
}

function Finding({ definition, result }) {
  const hasFinding = result.flagged || result.review_candidate;
  if (definition.id === "templating") {
    return (
      <article className="finding">
        <div className="finding-top"><strong>{definition.label}</strong><CheckStatus definition={definition} result={result} /></div>
        {hasFinding && (
          <>
            <p><strong>Reason:</strong> {result.reason || "—"}</p>
            <div className="template-pairs">
              {(result.evidence_pairs || []).map((pair) => (
                <details className="template-pair" key={pair.pair_id} open>
                  <summary>{pair.submitted_abstract_id} ↔ {pair.matched_abstract_id} · {pair.section || "Abstract"}</summary>
                  <div className="template-pair-meta"><span>{pair.reason}</span><span>{pair.same_author_group ? "Same author group" : "Different author group"}</span></div>
                  <div className="template-evidence-grid">
                    <div><strong>{pair.submitted_abstract_id}</strong><p>{pair.submitted_evidence || "No matched text available."}</p></div>
                    <div><strong>{pair.matched_abstract_id}</strong><p>{pair.matched_evidence || "No matched text available."}</p></div>
                  </div>
                  <div className="nested-checks">
                    {(pair.sub_checks || []).map((check) => <NestedCheck key={check.check_name} check={check} />)}
                  </div>
                </details>
              ))}
              {!result.evidence_pairs?.length && <p><strong>Evidence:</strong> {result.evidence || "None"}</p>}
            </div>
          </>
        )}
      </article>
    );
  }
  return (
    <article className="finding">
      <div className="finding-top"><strong>{definition.label}</strong><CheckStatus definition={definition} result={result} /></div>
      {hasFinding && <p><strong>Evidence:</strong> {result.evidence || "None"}</p>}
    </article>
  );
}

function NestedCheck({ check }) {
  const evidenceCount = check.result?.supporting_data?.length || 0;
  return (
    <div className="nested-check">
      <div><strong>{check.check_name.replaceAll("_", " ")}</strong><span>{check.evidence_role || "SUPPORTING"} · {check.result?.level || "UNKNOWN"}</span></div>
      <p>{check.result?.comment || "No result comment."}{evidenceCount ? ` · ${evidenceCount} evidence item(s)` : ""}</p>
    </div>
  );
}

function AuthorFinding({ definition, result }) {
  const hasFinding = result.flagged;
  return (
    <article className="finding">
      <div className="finding-top"><strong>{definition.label}</strong><AuthorCheckStatus result={result} /></div>
      {hasFinding && result.reason && <p><strong>Summary:</strong> {result.reason}</p>}
      {hasFinding && !result.findings?.length && <p><strong>Evidence:</strong> {result.evidence || "None"}</p>}
      {definition.id === "submission_volume" && !!result.findings?.length && (
        <div className="author-findings">
          {result.findings.map((finding) => (
            <details className="author-finding-card" key={`${finding.authors?.local_id || finding.authors?.full_name}-${finding.dois?.[0] || "finding"}`} open={result.findings.length === 1}>
              <summary>{finding.authors?.full_name || "Unknown author"} · {finding.dois?.length || 0} related submission{(finding.dois?.length || 0) === 1 ? "" : "s"}</summary>
              {finding.authors?.affiliations?.[0]?.institution && <p><strong>Affiliation:</strong> {finding.authors.affiliations[0].institution}</p>}
              {finding.authors?.match_scores?.confidence != null && <p><strong>Match confidence:</strong> {Math.round(finding.authors.match_scores.confidence * 100)}%</p>}
              {!!finding.dois?.length && (
                <div className="tag-list">
                  {finding.dois.map((doi) => <span className="tag" key={doi}>{doi}</span>)}
                </div>
              )}
            </details>
          ))}
        </div>
      )}
      {definition.id === "author_count_deviation" && !!result.findings?.length && (
        <div className="author-stats">
          <Metric label="Dataset average" value={result.findings[0].avg_count} help="Average author count across submissions" />
          <Metric label="This submission" value={result.findings[0].found_count} help="Authors listed on this paper" />
        </div>
      )}
      {definition.id === "affiliance_relevance" && !!result.findings?.length && (
        <div className="author-findings">
          {result.findings.map((finding) => (
            <div className="author-finding-card flat" key={`${finding.author}-${finding.institution_id || finding.institution_name}`}>
              <strong>{finding.author || "Author"}</strong>
              <p>{finding.institution_name || "Unknown institution"}</p>
              <div className="author-finding-meta">
                <span>{finding.resolved ? "Resolved institution" : "Unresolved institution"}</span>
                {finding.retraction_rate != null && <span>Retraction rate {Math.round(finding.retraction_rate * 1000) / 10}%</span>}
                {finding.works_count != null && <span>{number.format(finding.works_count)} works</span>}
                {finding.retracted_count != null && <span>{number.format(finding.retracted_count)} retractions</span>}
              </div>
            </div>
          ))}
        </div>
      )}
      {definition.id === "author_network" && !!result.findings?.length && (
        <div className="author-findings">
          {result.findings.map((finding) => (
            <details className="author-finding-card" key={(finding.authors || []).join("-")} open={result.findings.length === 1}>
              <summary>{(finding.authors || []).join(" · ")} · {finding.co_occurrence_count || 0} co-submission{(finding.co_occurrence_count || 0) === 1 ? "" : "s"}</summary>
              {!!finding.dois?.length && (
                <div className="tag-list">
                  {finding.dois.map((doi) => <span className="tag" key={doi}>{doi}</span>)}
                </div>
              )}
            </details>
          ))}
        </div>
      )}
      {definition.id === "retraction_history" && !!result.findings?.length && (
        <div className="author-findings">
          {result.findings.slice(0, MAX_RETRACTION_HISTORY).map((finding) => (
            <details className="author-finding-card" key={`${finding.matched_name}-${finding.retraction_record?.record_id || finding.match_score}`} open={result.findings.length === 1}>
              <summary>{finding.matched_name} · {finding.match_confidence || "Retraction Watch match"}</summary>
              {finding.retraction_record && (
                <>
                  <p><strong>{finding.retraction_record.title}</strong></p>
                  <div className="author-finding-meta">
                    <span>{finding.retraction_record.journal}</span>
                    <span>{finding.retraction_record.retraction_date}</span>
                    <span>{finding.retraction_record.retraction_nature}</span>
                  </div>
                  {finding.retraction_record.reason && <p className="retraction-reason">{finding.retraction_record.reason.replaceAll("+", " · ")}</p>}
                  <div className="tag-list">
                    {finding.retraction_record.original_paper_doi && <span className="tag">Original {finding.retraction_record.original_paper_doi}</span>}
                    {finding.retraction_record.retraction_doi && <span className="tag">Retraction {finding.retraction_record.retraction_doi}</span>}
                  </div>
                </>
              )}
            </details>
          ))}
          {result.findings.length > MAX_RETRACTION_HISTORY && (
            <p className="finding-limit-note">Showing {MAX_RETRACTION_HISTORY} of {result.findings.length} retraction records.</p>
          )}
        </div>
      )}
    </article>
  );
}

function HowItWorks() {
  return (
    <>
      <PageHead title="How It Works" subtitle="The triage logic represented by the current content-integrity report." />
      <div className="logic">
        <article><h3><RiskBadge risk="High" /> High</h3><p>Any one high-confidence content check requires editor review.</p></article>
        <article><h3><RiskBadge risk="Medium" /> Medium</h3><p>Medium evidence or multiple low-severity signals require editor review.</p></article>
        <article><h3><RiskBadge risk="Low" /> Low</h3><p>A low-severity signal may require editor review; clean submissions retain the authoritative None result.</p></article>
      </div>
      <section className="rule-section"><h2>Checks in this report</h2><div className="rule-note">Template evidence is pair-scoped; contradictions and trial checks are record-scoped support.</div>{checks.map((check) => <div className="rule-row" key={check.id}><strong>{check.label}<span>Content</span></strong>{check.description}</div>)}</section>
      <section className="rule-section"><h2>Author checks in this report</h2><div className="rule-note">Authorship findings are record-scoped and can be reviewed independently from content checks.</div>{authorChecks.map((check) => <div className="rule-row" key={check.id}><strong>{check.label}<span>Author</span></strong>{check.description}</div>)}</section>
    </>
  );
}

function PageHead({ title, subtitle }) { return <header className="page-head"><h1>{title}</h1><p>{subtitle}</p></header>; }
function Search({ value, onChange }) { return <input className="search" type="search" placeholder="Search submissions…" aria-label="Search submissions" value={value} onChange={(e) => onChange(e.target.value)} />; }
function matches(item, query) { return `${item.abstract_id} ${item.title} ${item.corresponding_author} ${item.why_flagged}`.toLowerCase().includes(query.trim().toLowerCase()); }
function hasContentFinding(item) {
  return checks.some((check) => item.checks[check.id].flagged) ||
    item.checks.templating.supporting_checks?.some((check) => check.result?.supporting_data?.length);
}
function affectedCount(abstracts) { return abstracts.filter(hasContentFinding).length; }

export default function App({ report }) {
  const [page, setPage] = useState("overview");
  const [lastPage, setLastPage] = useState("overview");
  const [detailFocus, setDetailFocus] = useState("content");
  const [selectedId, setSelectedId] = useState(null);
  const [currentReport, setCurrentReport] = useState(report);
  const [authorshipReport, setAuthorshipReport] = useState(emptyAuthorshipReport());
  const [runState, setRunState] = useState({ running: false, message: "" });
  const mainRef = useRef(null);
  const mergedReports = useMemo(() => mergeReports(currentReport, authorshipReport), [currentReport, authorshipReport]);
  const selected = mergedReports.content.abstracts.find((item) => item.abstract_id === selectedId);
  const selectedAuthorship = mergedReports.authorship.abstracts.find((item) => item.abstract_id === selectedId);
  const open = (id, focus = "content") => {
    setLastPage(page);
    setDetailFocus(focus);
    setSelectedId(id);
    setPage("detail");
    mainRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };
  const navigate = (next) => { setPage(next); setSelectedId(null); };
  useEffect(() => {
    const refreshReports = async () => {
      const [contentResponse, authorshipResponse] = await Promise.all([
        fetch("/api/report"),
        fetch("/api/authorship-report"),
      ]);

      if (contentResponse.ok) {
        const body = await contentResponse.json();
        setCurrentReport(normalizeReport(body.report));
      }
      if (authorshipResponse.ok) {
        const body = await authorshipResponse.json();
        setAuthorshipReport(normalizeAuthorshipReport(body.report));
      }
    };

    refreshReports().catch(() => {});
  }, []);
  const runPipeline = async () => {
    setRunState({ running: true, message: "Running pipeline…" });
    try {
      const response = await fetch("/api/run-pipeline", { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Pipeline failed.");
      setCurrentReport(normalizeReport(body.report));
      const authorshipResponse = await fetch("/api/authorship-report");
      if (authorshipResponse.ok) {
        const authorshipBody = await authorshipResponse.json();
        setAuthorshipReport(normalizeAuthorshipReport(authorshipBody.report));
      }
      setSelectedId(null);
      setPage("overview");
      setRunState({ running: false, message: `Completed: ${body.report.summary.total_submissions} submissions.` });
    } catch (error) {
      setRunState({ running: false, message: error.message });
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>INTEGRITY</span><span>CENTRAL</span></div>
        <nav aria-label="Product navigation">{pages.map(([id, icon, name]) => <button key={id} className={page === id ? "active" : ""} onClick={() => navigate(id)}><span aria-hidden="true">{icon}</span>{name}</button>)}</nav>
        <div className="run-meta"><span>Report generated</span><strong>{formatDate(currentReport.run?.generated_at)}</strong><code>{currentReport.run?.git_revision?.slice(0, 8)}</code></div>
      </aside>
      <main ref={mainRef}>
        <header className="topbar">
          <span>ASCO Editor Triage</span>
          <div className="pipeline-action">
            <span className="run-status" aria-live="polite">{runState.message}</span>
            <button className="run-btn" disabled={runState.running} onClick={runPipeline}>{runState.running ? "Running…" : "Run pipeline"}</button>
          </div>
        </header>
        <div className="page">
          {page === "overview" && <Overview report={mergedReports.content} onOpen={open} />}
          {page === "content" && <ContentChecks abstracts={mergedReports.content.abstracts} onOpen={(id) => open(id, "content")} />}
          {page === "authors" && <AuthorChecks abstracts={mergedReports.authorship.abstracts} onOpen={(id) => open(id, "authors")} />}
          {page === "evals" && <Evals report={mergedReports.content} />}
          {page === "how" && <HowItWorks />}
          {page === "detail" && selected && <Detail key={selected.abstract_id} abstract={selected} authorship={selectedAuthorship} focus={detailFocus} onBack={() => navigate(lastPage)} />}
        </div>
      </main>
    </div>
  );
}
