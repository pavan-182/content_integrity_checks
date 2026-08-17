import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { normalizeReport } from "./report.js";
import "./styles.css";

const emptyReport = normalizeReport({
  run: {},
  summary: {
    total_submissions: 0,
    high_risk: 0,
    moderate_risk: 0,
    low_risk: 0,
    requires_editor_judgement: 0,
    cleared_without_manual_review: 0,
  },
  abstracts: [],
});

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App report={emptyReport} />
  </StrictMode>,
);
