/* ============================================================
   strata — desktop entry point
   ============================================================ */
import React from "react";
import { createRoot } from "react-dom/client";

import "./styles/tokens.css";
import "./styles/app.css";

import { loadDataset } from "./data/mock.js";
import { API_BASE } from "./data/api.js";
import { StrataApp } from "./app/main.jsx";

// Fetch the dataset from the real API, then render (preserves all components).
loadDataset()
  .then(() => createRoot(document.getElementById("root")).render(<StrataApp />))
  .catch((err) => {
    document.getElementById("root").innerHTML =
      `<div style="position:fixed;inset:0;display:grid;place-items:center;padding:24px;text-align:center;background:#05060a;color:rgba(255,255,255,0.7);font-family:'Plus Jakarta Sans',sans-serif;">
        <div><div style="font-size:18px;font-weight:700;color:#fff;margin-bottom:8px;">Can't reach the strata API</div>
        <div style="font-size:14px;color:rgba(255,255,255,0.5);max-width:440px;line-height:1.6;">Tried <code>${API_BASE}</code>. Start the backend with <code>python -m backend.cli serve</code>, then reload.<br/><span style="color:rgba(255,255,255,0.3);font-size:12px;">${err.message}</span></div></div>
      </div>`;
  });
