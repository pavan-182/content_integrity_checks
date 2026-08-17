import { useEffect, useMemo, useState } from "react";
import { checks, normalizeReport, validationRows } from "./report.js";

const pages = [
  ["overview", "▦", "Overview"],
  ["queue", "☷", "Review Queue"],
  ["all", "□", "All Submissions"],
  ["content", "◫", "Content Checks"],
  ["evals", "⚑", "Evals"],
  ["how", "?", "How It Works"],
];

const number = new Intl.NumberFormat();
const formatDate = (value) =>
  value
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
    : "Unknown";

function RiskBadge({ risk }) {
  return <span className={`risk risk-${risk.toLowerCase()}`}>{risk === "None" ? "None · no active risk" : risk}</span>;
}

function CheckStatus({ definition, result }) {
  if (result.operational_failure) return <span className="status-flag">! Check failed</span>;
  if (result.review_candidate && !result.flagged) return <span className="status-flag">● Editor review candidate</span>;
  if (!result.flagged) return <span className="status-clear">✓ No active finding</span>;
  const count = definition.id === "templating"
    ? result.evidence_pairs?.length || result.match_count || 0
    : result.match_count || result.findings?.length || 0;
  const unit = definition.id === "templating" ? `pair${count === 1 ? "" : "s"}` : `finding${count === 1 ? "" : "s"}`;
  return <span className="status-flag">● Finding detected{count ? ` · ${count} ${unit}` : ""}</span>;
}

function Metric({ label, value, help }) {
  return (
    <article className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{number.format(value)}</div>
      <div className="metric-help">{help}</div>
    </article>
  );
}

function SubmissionTable({ items, onOpen, evidenceCheck, showCheckFlags = false }) {
  if (!items.length) return <div className="empty">No submissions match this view.</div>;

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Priority</th><th>Submission</th><th>Corresponding author</th>
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
              <td className="title-cell"><strong>{item.title}</strong><span className="secondary">{item.abstract_id}</span></td>
              <td>{item.corresponding_author}</td>
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

function Overview({ report, onNavigate, onOpen }) {
  const { summary, abstracts } = report;
  const flagged = abstracts.filter((item) => item.review_required);

  return (
    <>
      <PageHead title="Editor Triage Overview" subtitle="See what requires editor judgement, then move into the review queue." />
      <div className="metrics">
        <Metric label="Total submissions" value={summary.total_submissions} help="Every abstract appears once" />
        <Metric label="Needs editor judgement" value={summary.requires_editor_judgement} help="Moderate + High" />
        <Metric label="High content risk" value={summary.high_risk} help="Assigned by pipeline aggregation" />
        <Metric label="Cleared automatically" value={summary.cleared_without_manual_review} help="Review not required" />
      </div>
      <section className="section-card checks-summary">
        <div className="section-title">
          <div><h2>Content integrity</h2><div className="domain-label">{number.format(flagged.length)} submissions have a content finding</div></div>
          <button className="link-btn" onClick={() => onNavigate("content")}>View checks →</button>
        </div>
        <div className="simple-list">
          {checks.map((check) => (
            <div className="simple-row" key={check.id}>
              <div>{check.label}</div>
              <strong>{number.format(abstracts.filter((item) => item.checks[check.id].flagged).length)}</strong>
            </div>
          ))}
        </div>
      </section>
      <TableCard title="Review next" actions={<button className="link-btn" onClick={() => onNavigate("queue")}>View full queue →</button>}>
        <SubmissionTable items={flagged.slice(0, 5)} onOpen={onOpen} />
      </TableCard>
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

function AllSubmissions({ abstracts, onOpen }) {
  const [query, setQuery] = useState("");
  const [risk, setRisk] = useState("All");
  const [check, setCheck] = useState("All");
  const items = useMemo(() => abstracts.filter((item) =>
    (risk === "All" || item.overall_risk === risk) &&
    (check === "All" || item.checks[check].flagged) &&
    matches(item, query)), [abstracts, risk, check, query]);

  return (
    <>
      <PageHead title="All Submissions" subtitle="The complete set of abstracts in the pipeline report." />
      <TableCard title={`${number.format(items.length)} submissions`} actions={
        <div className="table-actions">
          <Search value={query} onChange={setQuery} />
          <select aria-label="Priority filter" value={risk} onChange={(e) => setRisk(e.target.value)}>
            {['All', 'High', 'Medium', 'Low', 'None'].map((value) => <option key={value}>{value}</option>)}
          </select>
          <select aria-label="Check filter" value={check} onChange={(e) => setCheck(e.target.value)}>
            <option value="All">All checks</option>
            {checks.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </div>
      }>
        <SubmissionTable items={items} onOpen={onOpen} showCheckFlags />
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
      <div className="check-grid">
        <button className={`check-tile ${active === "All" ? "active" : ""}`} onClick={() => setActive("All")}><strong>All content findings</strong><span>{number.format(affectedCount(abstracts))} submissions</span></button>
        {checks.map((check) => <button key={check.id} className={`check-tile ${active === check.id ? "active" : ""}`} onClick={() => setActive(check.id)}><strong>{check.label}</strong><span>{number.format(abstracts.filter((item) => item.checks[check.id].flagged).length)} active</span></button>)}
      </div>
      <TableCard title={active === "All" ? "Affected submissions" : checks.find((item) => item.id === active).label}>
        <SubmissionTable items={affected} onOpen={onOpen} evidenceCheck={active === "All" ? undefined : active} />
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
                    <td className="title-cell"><strong>{row.title}</strong><span className="secondary">{row.abstract_id}</span></td>
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

function Detail({ abstract, onBack }) {
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
      <section className="detail-section">
        <h2>Content integrity</h2>
        {checks.map((check) => <Finding key={check.id} definition={check} result={abstract.checks[check.id]} />)}
      </section>
    </>
  );
}

function isClear(result, hasSupporting = false) {
  return !result.operational_failure && !result.flagged && !result.review_candidate && !hasSupporting;
}

function Finding({ definition, result }) {
  if (definition.id === "templating") {
    const hasPairs = (result.evidence_pairs || []).length > 0;
    const hasSupporting = (result.supporting_checks || []).some((check) => check.result?.supporting_data?.length);
    const clear = isClear(result, hasSupporting);
    return (
      <article className="finding">
        <div className="finding-top"><strong>{definition.label}</strong><CheckStatus definition={definition} result={result} /></div>
        {!clear && <p><strong>Evidence:</strong> {result.evidence || "None"}</p>}
        {!clear && <p><strong>Reason:</strong> {result.reason || "—"}</p>}
        {(hasPairs || hasSupporting) && (
          <div className="template-pairs">
            {result.evidence_pairs.map((pair) => (
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
            {hasSupporting && (
              <div className="nested-checks">
                <strong>Record-level supporting checks</strong>
                {result.supporting_checks.map((check) => <NestedCheck key={check.check_name} check={check} />)}
              </div>
            )}
          </div>
        )}
      </article>
    );
  }
  return (
    <article className="finding">
      <div className="finding-top"><strong>{definition.label}</strong><CheckStatus definition={definition} result={result} /></div>
      {!isClear(result) && <p><strong>Evidence:</strong> {result.evidence || "None"}</p>}
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
  const [selectedId, setSelectedId] = useState(null);
  const [currentReport, setCurrentReport] = useState(report);
  const [runState, setRunState] = useState({ running: false, message: "" });
  const selected = currentReport.abstracts.find((item) => item.abstract_id === selectedId);
  const open = (id) => { setSelectedId(id); setPage("detail"); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const navigate = (next) => { setPage(next); setSelectedId(null); };
  useEffect(() => {
    fetch("/api/report")
      .then((response) => response.ok ? response.json() : null)
      .then((body) => body && setCurrentReport(normalizeReport(body.report)))
      .catch(() => {});
  }, []);
  const runPipeline = async () => {
    setRunState({ running: true, message: "Running pipeline…" });
    try {
      const response = await fetch("/api/run-pipeline", { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Pipeline failed.");
      const nextReport = normalizeReport(body.report);
      setCurrentReport(nextReport);
      setSelectedId(null);
      setPage("overview");
      setRunState({ running: false, message: `Completed: ${nextReport.summary.total_submissions} submissions.` });
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
      <main>
        <header className="topbar">
          <span>ASCO Editor Triage</span>
          <div className="pipeline-action">
            <span className="run-status" aria-live="polite">{runState.message}</span>
            <button className="run-btn" disabled={runState.running} onClick={runPipeline}>{runState.running ? "Running…" : "Run pipeline"}</button>
          </div>
        </header>
        <div className="page">
          {page === "overview" && <Overview report={currentReport} onNavigate={navigate} onOpen={open} />}
          {page === "queue" && <ReviewQueue abstracts={currentReport.abstracts} onOpen={open} />}
          {page === "all" && <AllSubmissions abstracts={currentReport.abstracts} onOpen={open} />}
          {page === "content" && <ContentChecks abstracts={currentReport.abstracts} onOpen={open} />}
          {page === "evals" && <Evals report={currentReport} />}
          {page === "how" && <HowItWorks />}
          {page === "detail" && selected && <Detail key={selected.abstract_id} abstract={selected} onBack={() => navigate("queue")} />}
        </div>
      </main>
    </div>
  );
}
