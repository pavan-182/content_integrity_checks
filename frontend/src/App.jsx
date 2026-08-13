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
  return <span className={`risk risk-${risk.toLowerCase()}`}>{risk === "Low" ? "Low · no action" : risk}</span>;
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
            <th>High-confidence flags</th><th>Corroborating flags</th>
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
              <td>{evidenceCheck ? item.checks[evidenceCheck].evidence : item.why_flagged}</td>
              {evidenceCheck === "templating" && <td>{item.checks.templating.reason || "—"}</td>}
              <td>{item.high_confidence_flags}</td>
              <td>{item.corroborating_flags}</td>
              {showCheckFlags && checks.map((check) => <td key={check.id}>{item.checks[check.id].flagged ? "Y" : "N"}</td>)}
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
  const flagged = abstracts.filter((item) => item.overall_risk !== "Low");

  return (
    <>
      <PageHead title="Editor Triage Overview" subtitle="See what requires editor judgement, then move into the review queue." />
      <div className="metrics">
        <Metric label="Total submissions" value={summary.total_submissions} help="Every abstract appears once" />
        <Metric label="Needs editor judgement" value={summary.requires_editor_judgement} help="Moderate + High" />
        <Metric label="High priority" value={summary.high_risk} help="At least one content check" />
        <Metric label="Cleared automatically" value={summary.cleared_without_manual_review} help="No significant flags" />
      </div>
      <section className="section-card checks-summary">
        <div className="section-title">
          <div><h2>Content integrity</h2><div className="domain-label">{number.format(flagged.length)} submissions have a content finding</div></div>
          <button className="link-btn" onClick={() => onNavigate("content")}>View checks →</button>
        </div>
        <div className="simple-list">
          {checks.map((check) => (
            <div className="simple-row" key={check.id}>
              <div>{check.label}<small>High-confidence check</small></div>
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
  const risks = ["High", "Moderate", "Low"];
  const items = abstracts.filter((item) => item.overall_risk === risk && matches(item, query));

  return (
    <>
      <PageHead title="Review Queue" subtitle="High, Moderate and Low pipeline results in one editor view." />
      <div className="queue-tabs">
        {risks.map((item) => <button key={item} className={`tab-btn ${risk === item ? "active" : ""}`} onClick={() => setRisk(item)}>{item === "Low" ? "Low · no action" : item} ({abstracts.filter((a) => a.overall_risk === item).length})</button>)}
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
            {['All', 'High', 'Moderate', 'Low'].map((value) => <option key={value}>{value}</option>)}
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
  const affected = abstracts.filter((item) => active === "All" ? checks.some((check) => item.checks[check.id].flagged) : item.checks[active].flagged);

  return (
    <>
      <PageHead title="Content Integrity Checks" subtitle="Checks applied to abstract text and cross-submission content." />
      <div className="check-grid">
        <button className={`check-tile ${active === "All" ? "active" : ""}`} onClick={() => setActive("All")}><strong>All content findings</strong><span>{number.format(affectedCount(abstracts))} submissions</span></button>
        {checks.map((check) => <button key={check.id} className={`check-tile ${active === check.id ? "active" : ""}`} onClick={() => setActive(check.id)}><strong>{check.label}</strong><span>High-confidence · {number.format(abstracts.filter((item) => item.checks[check.id].flagged).length)} flagged</span></button>)}
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
      <section className="detail-section">
        <h2>Content integrity</h2>
        {checks.map((check) => <Finding key={check.id} definition={check} result={abstract.checks[check.id]} />)}
      </section>
    </>
  );
}

function Finding({ definition, result }) {
  return (
    <article className="finding">
      <div className="finding-top"><strong>{definition.label}</strong><span className={result.flagged ? "status-flag" : "status-clear"}>Flag: {result.flagged ? "Y" : "N"}</span></div>
      <p><strong>Evidence:</strong> {result.evidence || "None"}</p>
      {result.reason && <p><strong>Reason:</strong> {result.reason}</p>}
    </article>
  );
}

function HowItWorks() {
  return (
    <>
      <PageHead title="How It Works" subtitle="The triage logic represented by the current content-integrity report." />
      <div className="logic">
        <article><h3><RiskBadge risk="High" /> High</h3><p>Any one high-confidence content check requires editor review.</p></article>
        <article><h3><RiskBadge risk="Moderate" /> Moderate</h3><p>Reserved by the report format; this pipeline run contains no corroborating checks.</p></article>
        <article><h3><RiskBadge risk="Low" /> Low</h3><p>No content check fired, so no manual action is required.</p></article>
      </div>
      <section className="rule-section"><h2>Checks in this report</h2><div className="rule-note">Any one flagged → High priority</div>{checks.map((check) => <div className="rule-row" key={check.id}><strong>{check.label}<span>Content</span></strong>{check.description}</div>)}</section>
    </>
  );
}

function PageHead({ title, subtitle }) { return <header className="page-head"><h1>{title}</h1><p>{subtitle}</p></header>; }
function Search({ value, onChange }) { return <input className="search" type="search" placeholder="Search submissions…" aria-label="Search submissions" value={value} onChange={(e) => onChange(e.target.value)} />; }
function matches(item, query) { return `${item.abstract_id} ${item.title} ${item.corresponding_author} ${item.why_flagged}`.toLowerCase().includes(query.trim().toLowerCase()); }
function affectedCount(abstracts) { return abstracts.filter((item) => checks.some((check) => item.checks[check.id].flagged)).length; }

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
