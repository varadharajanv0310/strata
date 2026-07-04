"""Populate the v2 ladder/experience tables inside the fuse (GRID_PLAN §3).

Called by ``build_warehouse_from_staging`` on an open DuckDB connection, inside its
transaction. Pure derivation + staging reads — NO network, NO GPU. Idempotent: clears
then reloads its own tables. Everything it writes is source-labeled and per-kind
(never blended across lenses).

Inputs (all optional — each block skips cleanly when its staging is absent):
* ``staging/analytics/*.json`` (posting_attributes) → fact_role_attributes,
  bridge_seniority_yoe, fact_demand_yoe.
* ``staging/ambitionbox/estimates.json`` → fact_salary_yoe_obs (kind=estimate, IN).
* fact_salary_yoe_obs → fact_salary_curve via the isotonic+PCHIP fitter.
"""
from __future__ import annotations

import json

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("warehouse.ladder_build")

def _staging_json(rel: str):
    p = settings.staging_dir / rel
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("ladder_build: %s unreadable (%s)", rel, e)
        return None


def _reset(con, *tables):
    for t in tables:
        try:
            con.execute(f"DELETE FROM {t}")
        except Exception:  # noqa: BLE001 — table may not exist on an old warehouse
            pass


def build_ladder_v2(con) -> dict:
    """Load analytics + estimates + fitted curves into the v2 tables. Returns counts."""
    counts = {"role_attributes": 0, "seniority_yoe": 0, "demand_yoe": 0,
              "yoe_obs": 0, "curves": 0}
    _reset(con, "fact_role_attributes", "bridge_seniority_yoe", "fact_demand_yoe",
           "fact_salary_yoe_obs", "fact_salary_curve")

    # ---- 1) posting_attributes analytics --------------------------------------
    ra = _staging_json("analytics/role_attributes.json") or []
    for r in ra:
        con.execute(
            "INSERT INTO fact_role_attributes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (role_id, country_code) DO NOTHING",
            (r["role_id"], r["country_code"], r["n_postings"], r["remote_share"],
             r["onsite_share"], r["hybrid_share"], r["remote_pay_gap"],
             r["degree_required_share"], r["degree_optional_share"], r["degree_pay_gap"],
             r["top_certifications"], r["oncall_share"], r["source_id"]))
    counts["role_attributes"] = len(ra)

    sy = _staging_json("analytics/seniority_yoe.json") or []
    for r in sy:
        con.execute(
            "INSERT INTO bridge_seniority_yoe VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (role_id, country_code, seniority) DO NOTHING",
            (r["role_id"], r["country_code"], r["seniority"],
             r["yoe_p25"], r["yoe_p50"], r["yoe_p75"], r["n"]))
    counts["seniority_yoe"] = len(sy)

    dy = _staging_json("analytics/demand_yoe.json") or []
    for r in dy:
        con.execute(
            "INSERT INTO fact_demand_yoe VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (role_id, country_code, yoe_bucket, year) DO NOTHING",
            (r["role_id"], r["country_code"], r["yoe_bucket"], r["year"],
             r["postings"], r["share"], r["source_id"]))
    counts["demand_yoe"] = len(dy)

    # ---- 2) aggregator estimate observations (AmbitionBox: IN, per-year) -------
    obs_rows: list[dict] = []
    ab = _staging_json("ambitionbox/estimates.json") or []
    for r in ab:
        if not r.get("role_id"):            # unknown-role estimates wait for the catalog
            continue
        yoe = r.get("yoe")
        rec = {"role_id": r["role_id"], "country_code": r.get("country", "IN"),
               "yoe_min": yoe, "yoe_max": yoe, "level_label": None, "year": 2026,
               "median": r.get("median"), "p25": r.get("p25"), "p75": r.get("p75"),
               "currency_code": r.get("currency", "INR"), "basis": r.get("basis", "ctc-annual"),
               "sample_size": int(r.get("n") or 0), "kind": "estimate",
               "confidence": "high" if (r.get("n") or 0) >= 100 else "med",
               "source_id": "ambitionbox", "retrieved_at": r.get("retrieved", "")}
        obs_rows.append(rec)
        con.execute(
            "INSERT INTO fact_salary_yoe_obs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec["role_id"], rec["country_code"], rec["yoe_min"], rec["yoe_max"],
             rec["level_label"], rec["year"], rec["median"], rec["p25"], rec["p75"],
             rec["currency_code"], rec["basis"], rec["sample_size"], rec["kind"],
             rec["confidence"], rec["source_id"], rec["retrieved_at"]))
    counts["yoe_obs"] = len(obs_rows)

    # ---- 3) fit pay-vs-experience curves from the observations -----------------
    if obs_rows:
        try:
            from backend.ml.salary_curve import fit_curves
            for c in fit_curves(obs_rows):
                con.execute(
                    "INSERT INTO fact_salary_curve VALUES (?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (role_id, country_code, kind, yoe) DO NOTHING",
                    (c["role_id"], c["country_code"], c["kind"], c["yoe"],
                     c["fit_median"], c["lo"], c["hi"], c["support"],
                     c["n_effective"], c["method"]))
                counts["curves"] += 1
        except Exception as e:  # noqa: BLE001 — sklearn/scipy absent → skip curves
            log.warning("ladder_build: curve fit skipped (%s)", e)

    log.info("ladder_build v2: %s", counts)
    return counts
