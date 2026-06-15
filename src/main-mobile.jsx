/* ============================================================
   strata — mobile entry point
   ============================================================ */
import React from "react";
import { createRoot } from "react-dom/client";

import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/mobile.css";

import { loadDataset } from "./data/mock.js";
import { API_BASE } from "./data/api.js";
import { MobileApp } from "./app/mobile.jsx";

// Fetch the dataset from the real API, then render (preserves all components).
loadDataset()
  .then(() => createRoot(document.getElementById("mroot")).render(<MobileApp />))
  .catch((err) => {
    document.getElementById("mroot").innerHTML =
      `<div style="position:absolute;inset:0;display:grid;place-items:center;padding:20px;text-align:center;color:#5C564A;font-family:'IBM Plex Mono',monospace;">
        <div><div style="font-size:16px;font-weight:700;color:#14110C;margin-bottom:8px;">Can't reach the strata API</div>
        <div style="font-size:13px;color:#7A7264;line-height:1.6;">Tried <code>${API_BASE}</code>. Run <code>python -m backend.cli serve</code>.<br/><span style="color:#9B937F;font-size:11px;">${err.message}</span></div></div>
      </div>`;
  });
