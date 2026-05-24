/* ============================================================
   strata — desktop entry point
   ============================================================ */
import React from "react";
import { createRoot } from "react-dom/client";

import "./styles/tokens.css";
import "./styles/app.css";

import { StrataApp } from "./app/main.jsx";

createRoot(document.getElementById("root")).render(<StrataApp />);
