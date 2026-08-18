import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { createEmptyReport } from "./report.js";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App report={createEmptyReport()} />
  </StrictMode>,
);
