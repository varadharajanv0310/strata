/* ============================================================
   strata — mobile entry point
   ============================================================ */
import React from "react";
import { createRoot } from "react-dom/client";

import "./styles/tokens.css";
import "./styles/app.css";
import "./styles/mobile.css";

import { MobileApp } from "./app/mobile.jsx";

createRoot(document.getElementById("mroot")).render(<MobileApp />);
