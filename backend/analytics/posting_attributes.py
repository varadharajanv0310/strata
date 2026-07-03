"""Zero-collection analytics over the LLM-extracted postings (GRID_PLAN Step 1).

Pure derivation — NO network, NO GPU. Reads what is already on disk
(``staging/extracted/postings_extracted.parquet`` joined to
``staging/common_crawl/postings.parquet`` for country) and produces three
role-level artifacts that unlock new product surfaces without a single fetch:

1. ``bridge_seniority_yoe`` — the market's own definition of a seniority word in
   YEARS, per role (and global fallback), learned from (seniority × years_required).
   This is the adapter that later places level-labeled salary observations onto the
   experience axis (GRID_PLAN §3 Layer 3).
2. ``fact_role_attributes`` — arrangement mix (remote/onsite/hybrid), credential
   requirements (degree required vs optional), top certifications, and on-call share
   per role. Pay-GAP fields (remote/degree) are left NULL: this corpus carries a
   ``has_salary`` flag but no per-posting salary amount, so a within-role pay gap
   can't be computed here — it needs a salary-bearing posting corpus (flagged, not faked).
3. ``fact_demand_yoe`` — the experience the market ASKS for: share of postings per
   years-of-experience bucket, per role.

Outputs land as staging JSON (the fuse loads them into the v2 warehouse tables);
``load_frames()`` returns them as DataFrames for direct inspection/testing.
ROLES-ONLY: designation-level aggregates; the posting ``employer`` column is never read.
"""
from __future__ import annotations

import json
from collections import Counter

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("analytics.posting_attributes")

# buckets must tile [0,∞) with NO hole — 5-9 covers [5,10) so a y=9 posting isn't dropped
YOE_BUCKETS = [("0-1", 0, 1), ("1-3", 1, 3), ("3-5", 3, 5), ("5-9", 5, 10), ("10+", 10, 99)]
_ONCALL = {"on_call_rotation", "shift_work", "night_shift", "weekend_coverage"}
_DEGREE_REQ = {"associate_or_diploma", "bachelors", "masters", "phd"}
MIN_N = 8         # per-cell floor before we publish a stat (honesty over coverage)
DATA_YEAR = 2026  # stamp — kept consistent with the estimate observations in ladder_build


def _staging_dir():
    d = settings.staging_dir / "analytics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_joined():
    """Extraction ⋈ common_crawl (on the shared 0-based posting_id) → one frame with
    role_id (resolved from disambiguated_role) + country_code + the extracted fields."""
    import pandas as pd

    ext = pd.read_parquet(settings.staging_dir / "extracted" / "postings_extracted.parquet")
    cc_path = settings.staging_dir / "common_crawl" / "postings.parquet"
    if cc_path.exists():
        cc = pd.read_parquet(cc_path, columns=["country_code"]).reset_index(drop=True)
        cc.insert(0, "posting_id", range(len(cc)))
        ext = ext.merge(cc, on="posting_id", how="left")
    else:
        ext["country_code"] = None

    from backend.warehouse.taxonomy import match_title_to_role
    ext["role_id"] = ext["disambiguated_role"].astype(str).map(
        lambda t: match_title_to_role(t) if t and t != "unknown" else None)
    ext["country_code"] = ext["country_code"].where(ext["country_code"].notna(), None)
    return ext


def _percentile(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return float(s[i])


def compute() -> dict:
    """Compute the three artifacts. Returns {seniority_yoe, role_attributes, demand_yoe}."""
    import pandas as pd

    df = _load_joined()
    n_total = len(df)

    # ---- 1) seniority → YoE concordance (global + per-role) ----------------------
    seniority_yoe: list[dict] = []

    def _senio_rows(sub, role_id, country):
        for sen, g in sub.groupby("seniority"):
            if sen in ("unknown", None):
                continue
            yrs = [float(y) for y in g["years_required"].dropna().tolist() if 0 <= float(y) <= 60]
            if len(yrs) < MIN_N:
                continue
            seniority_yoe.append({
                "role_id": role_id, "country_code": country, "seniority": sen,
                "yoe_p25": _percentile(yrs, 0.25), "yoe_p50": _percentile(yrs, 0.50),
                "yoe_p75": _percentile(yrs, 0.75), "n": len(yrs)})

    _senio_rows(df, "*", "*")
    for rid, g in df[df["role_id"].notna()].groupby("role_id"):
        _senio_rows(g, rid, "*")

    # ---- 2) role attributes (arrangement / credentials / certs / on-call) --------
    role_attributes: list[dict] = []

    def _attr_row(sub, role_id, country):
        n = len(sub)
        if n < MIN_N:
            return
        wa = sub["work_arrangement"]
        known = wa[wa.isin(["remote", "onsite", "hybrid"])]
        share = lambda v: round(float((known == v).sum()) / len(known), 4) if len(known) else None
        edu = sub["education_requirement"]
        edu_known = edu[edu != "unknown"]
        deg_req = round(float(edu_known.isin(_DEGREE_REQ).sum()) / len(edu_known), 4) if len(edu_known) else None
        deg_opt = round(float((edu_known == "none_required").sum()) / len(edu_known), 4) if len(edu_known) else None
        certs = Counter()
        for arr in sub["certifications_required"]:
            if arr is None or isinstance(arr, str) or not hasattr(arr, "__iter__"):
                continue                          # skip None / scalar-NaN / str cells safely
            for c in list(arr):
                cc = str(c).strip()
                if cc:
                    certs[cc] += 1
        oc = sub["on_call_or_shift"]
        oc_known = oc[oc != "unknown"]
        oncall = round(float(oc_known.isin(_ONCALL).sum()) / len(oc_known), 4) if len(oc_known) else None
        role_attributes.append({
            "role_id": role_id, "country_code": country, "n_postings": n,
            "remote_share": share("remote"), "onsite_share": share("onsite"),
            "hybrid_share": share("hybrid"),
            "remote_pay_gap": None,          # needs a salary-bearing corpus (flagged, not faked)
            "degree_required_share": deg_req, "degree_optional_share": deg_opt,
            "degree_pay_gap": None,
            "top_certifications": json.dumps([{"name": k, "count": v} for k, v in certs.most_common(6)]),
            "oncall_share": oncall, "source_id": "llm-extract"})

    _attr_row(df, "*", "*")
    for rid, g in df[df["role_id"].notna()].groupby("role_id"):
        _attr_row(g, rid, "*")

    # ---- 3) demand by YoE bucket (what experience the market asks for) ------------
    demand_yoe: list[dict] = []

    def _demand_rows(sub, role_id, country):
        yrs = [float(y) for y in sub["years_required"].dropna().tolist() if 0 <= float(y) <= 60]
        if len(yrs) < MIN_N:
            return
        total = len(yrs)
        for name, lo, hi in YOE_BUCKETS:
            c = sum(1 for y in yrs if lo <= y < hi or (name == "10+" and y >= 10))
            if c:
                demand_yoe.append({
                    "role_id": role_id, "country_code": country, "yoe_bucket": name,
                    "year": DATA_YEAR,
                    "postings": c, "share": round(c / total, 4), "source_id": "llm-extract"})

    _demand_rows(df, "*", "*")
    for rid, g in df[df["role_id"].notna()].groupby("role_id"):
        _demand_rows(g, rid, "*")

    out = {"seniority_yoe": seniority_yoe, "role_attributes": role_attributes,
           "demand_yoe": demand_yoe, "n_postings": n_total,
           "n_role_resolved": int(df["role_id"].notna().sum())}
    return out


def run(**kw) -> dict:
    """Compute + persist staging JSON (connector-style entrypoint; no network/GPU)."""
    data = compute()
    sd = _staging_dir()
    for key in ("seniority_yoe", "role_attributes", "demand_yoe"):
        (sd / f"{key}.json").write_text(json.dumps(data[key]), encoding="utf-8")
    summary = {"seniority_yoe": len(data["seniority_yoe"]),
               "role_attributes": len(data["role_attributes"]),
               "demand_yoe": len(data["demand_yoe"]),
               "postings": data["n_postings"], "role_resolved": data["n_role_resolved"]}
    log.info("posting_attributes: %s", summary)
    return summary


def load_frames() -> dict:
    """Read the persisted artifacts back as DataFrames (for fuse / inspection)."""
    import pandas as pd

    sd = _staging_dir()
    out = {}
    for key in ("seniority_yoe", "role_attributes", "demand_yoe"):
        p = sd / f"{key}.json"
        out[key] = pd.DataFrame(json.loads(p.read_text(encoding="utf-8"))) if p.exists() else pd.DataFrame()
    return out


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
