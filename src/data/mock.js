/* ============================================================
   strata — data layer  (wired to the real API)
   ------------------------------------------------------------
   This file used to generate the dataset in-browser. It now hydrates the SAME
   `STRATA` object from the backend API (`/api/dataset`), so every surface keeps
   importing `{ STRATA }` unchanged — only the data SOURCE moved server-side.
   The pure display helpers (currency formatting, PPP, role lookup) stay here.
   Call `loadDataset()` once at startup before rendering (see main-*.jsx).
   ============================================================ */
import { fetchDataset } from "./api.js";

// ---- pure display helpers (read the live STRATA.C) ----
function groupIndian(n) {
  const s = String(n);
  if (s.length <= 3) return s;
  const last3 = s.slice(-3);
  let rest = s.slice(0, -3);
  rest = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",");
  return rest + "," + last3;
}
function fmtCur(v, code) {
  const c = STRATA.C[code];
  v = Math.round(v);
  const num = code === "IN" ? groupIndian(v) : v.toLocaleString("en-US");
  return c.cur + num;
}
function fmtCompact(v, code) {
  const c = STRATA.C[code];
  if (code === "IN") { return c.cur + (v / 100000).toFixed(v >= 1000000 ? 1 : 0) + "L"; }
  if (v >= 1000) return c.cur + (v / 1000).toFixed(v >= 100000 ? 0 : 0) + "k";
  return c.cur + Math.round(v);
}
function pppUSD(v, code) { return v / STRATA.C[code].pppRate; }

// ---- the singleton the whole app imports (data filled by hydrate) ----
export const STRATA = {
  COUNTRIES: [], C: {}, FAMILIES: [], roles: [], YEARS: [], FYEARS: [],
  marketPulse: {}, RESUME_SAMPLE: {}, RESUME_B: {}, isSeed: true,
  fmtCur, fmtCompact, pppUSD, groupIndian,
  roleById: (id) => STRATA.roles.find((r) => r.id === id),
};

// ---- hydrate STRATA from an API dataset payload (same shape as the old mock) ----
export function hydrate(data) {
  STRATA.COUNTRIES = data.countries;
  STRATA.C = Object.fromEntries(data.countries.map((c) => [c.code, c]));
  STRATA.FAMILIES = data.families;
  STRATA.roles = data.roles;
  STRATA.YEARS = data.years;
  STRATA.FYEARS = data.fyears;
  STRATA.RESUME_SAMPLE = data.resume_sample;
  STRATA.RESUME_B = data.resume_b;
  // new-signal maps the dashboard reads (adoption momentum, hedonic premiums, provenance
  // lineage); without these the panels that consume them render undefined.
  STRATA.skillAdoption = data.skillAdoption || {};
  STRATA.skillPremiums = data.skillPremiums || {};
  STRATA.provenance = data.provenance || {};
  STRATA.isSeed = !!data.is_seed;
  // marketPulse arrives as role-id arrays; reattach the role object references
  const byId = Object.fromEntries(data.roles.map((r) => [r.id, r]));
  const mp = {};
  for (const [code, groups] of Object.entries(data.marketPulse || {})) {
    mp[code] = {};
    for (const [k, ids] of Object.entries(groups)) mp[code][k] = ids.map((id) => byId[id]).filter(Boolean);
  }
  STRATA.marketPulse = mp;
  return STRATA;
}

// ---- fetch + hydrate once at startup ----
export async function loadDataset() {
  const data = await fetchDataset();
  return hydrate(data);
}
