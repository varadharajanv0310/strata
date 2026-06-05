/* ============================================================
   strata — API client
   Thin fetch layer over the FastAPI backend. Base URL is configurable via
   VITE_API_BASE (defaults to the local backend). Every payload already carries
   native currency + confidence + provenance, matching the old mock shapes.
   ============================================================ */
export const API_BASE =
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.VITE_API_BASE) ||
  "http://127.0.0.1:8000";

async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`strata API ${path} → ${res.status}`);
  return res.json();
}

// the full bundle the frontend hydrates from
export const fetchDataset = () => getJSON("/api/dataset");

// granular endpoints (available for future incremental loading)
export const fetchRole = (id) => getJSON(`/api/roles/${encodeURIComponent(id)}`);
export const fetchJobScore = (country) => getJSON(`/api/jobscore?country=${country}`);
export const fetchProvenance = (role, country) =>
  getJSON(`/api/provenance?role=${encodeURIComponent(role)}&country=${country}`);
