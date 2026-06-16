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
import { initFX } from "./app/fx.js";

initFX(); // the pen — presentation-only crosshair (see app/fx.js)

// Fetch the dataset from the real API, then render (preserves all components).
loadDataset()
  .then(() => createRoot(document.getElementById("root")).render(<StrataApp />))
  .catch((err) => {
    document.getElementById("root").innerHTML =
      `<div style="position:fixed;inset:0;display:grid;place-items:center;padding:24px;text-align:center;background:#0b0d12;color:#9fb4dc;font-family:'IBM Plex Mono',monospace;">
        <div><div style="font-size:18px;font-weight:700;color:#f3f6fc;margin-bottom:8px;">Can't reach the strata API</div>
        <div style="font-size:14px;color:#8893ab;max-width:440px;line-height:1.6;">Tried <code>${API_BASE}</code>. Start the backend with <code>python -m backend.cli serve</code>, then reload.<br/><span style="color:#5d6680;font-size:12px;">${err.message}</span></div></div>
      </div>`;
  });
