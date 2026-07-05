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
    """Drop + recreate each v2 table from its schema DDL.

    The v2 tables are fully derived (cleared and reloaded every fuse), so dropping is
    lossless — and unlike DELETE it also migrates an old warehouse whose table predates
    a column addition (CREATE TABLE IF NOT EXISTS alone never alters an existing table).
    """
    from backend.warehouse.schema import LADDER_V2
    for t in tables:
        try:
            con.execute(f"DROP TABLE IF EXISTS {t}")
            if t in LADDER_V2:
                con.execute(LADDER_V2[t])
        except Exception as e:  # noqa: BLE001
            log.warning("ladder_build: could not recreate %s (%s)", t, e)


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

    # ---- 2) aggregator / official salary-by-experience observations ------------
    # Every estimate connector emits the SAME row shape into staging/<src>/estimates.json:
    # {source, kind, role_id, country, currency, basis, yoe(int|null), level_label(str|null),
    #  median, p25, p75, n, retrieved}. yoe → yoe_min/max for year-based sources (AmbitionBox);
    # level_label carries native tiers (PayScale brackets, levels.fyi levels, Robert Half tiers)
    # with yoe NULL — years are never invented from a level.
    ESTIMATE_SOURCES = ["ambitionbox", "itjobswatch", "seek_jobstreet", "talent",
                        "payscale", "levelsfyi", "gehalt_de", "canada_jobbank", "roberthalf"]
    # Second-pass role recovery for rows a connector's own title dict left role_id=null.
    # EXACT only: resolve on the native occupation code the source carries (NOC/ANZSCO/
    # ISCO/SOC/…) via the curated GOV_CROSSWALK — never fuzzy-guess a role from a label.
    # Rows with no code and no crosswalk hit stay dropped (roles-only 16-role charter).
    _recovered = 0
    _code_maps: dict[str, dict[str, str]] = {}
    try:
        from backend.warehouse.taxonomy import GOV_CROSSWALK
        for _rid, _sysd in GOV_CROSSWALK.items():
            for _sysn, (_code, _l) in _sysd.items():
                _code_maps.setdefault(_sysn.lower(), {}).setdefault(str(_code), _rid)
    except Exception as e:  # noqa: BLE001
        log.warning("ladder_build: GOV_CROSSWALK unavailable for recovery (%s)", e)
    # row code-field name → crosswalk system key
    _CODE_FIELDS = {"noc_code": "noc2021", "noc": "noc2021", "anzsco_code": "anzsco",
                    "anzsco": "anzsco", "isco_code": "isco08", "isco08": "isco08",
                    "ssoc_code": "ssoc", "ssoc": "ssoc", "kldb_code": "kldb", "kldb": "kldb"}

    def _recover_role(r: dict) -> str | None:
        extra = r.get("extra") if isinstance(r.get("extra"), dict) else {}
        for fld, sysn in _CODE_FIELDS.items():
            code = r.get(fld) or extra.get(fld)
            if not code:
                continue
            # EXACT lookup only — no prefix fallback. A sub-classification code (e.g.
            # 5-digit ISCO "25120") must NOT inherit its 4-digit parent's role: the
            # parent groups several of our roles, so a prefix hit is a guess, not a match.
            hit = _code_maps.get(sysn, {}).get(str(code).strip())
            if hit:
                return hit
        # US SOC via the H-1B SOC map (onet_soc keys carry the .00 suffix, so map directly)
        soc = r.get("soc_code") or r.get("soc")
        if soc:
            try:
                from backend.ingest.h1b import _soc_to_role
                return _soc_to_role(str(soc).split(".")[0])
            except Exception:  # noqa: BLE001
                return None
        return None

    obs_rows: list[dict] = []
    for src in ESTIMATE_SOURCES:
        for r in (_staging_json(f"{src}/estimates.json") or []):
            if not r.get("role_id"):
                rid = _recover_role(r)
                if rid:
                    r = {**r, "role_id": rid}
                    _recovered += 1
            if not r.get("role_id") or r.get("median") in (None, 0):
                continue                     # still-unknown rows: outside the 16-role charter
            yoe = r.get("yoe")
            yoe = int(yoe) if isinstance(yoe, (int, float)) and 0 <= yoe <= 60 else None
            n = int(r.get("n") or 0)
            rec = {"role_id": r["role_id"], "country_code": r.get("country", ""),
                   "yoe_min": yoe, "yoe_max": yoe, "level_label": r.get("level_label"),
                   "year": 2026, "median": r.get("median"), "p25": r.get("p25"),
                   "p75": r.get("p75"), "currency_code": r.get("currency", ""),
                   "basis": r.get("basis", "base-annual"), "sample_size": n,
                   "kind": r.get("kind", "estimate"),
                   "confidence": "high" if n >= 100 else ("med" if n else "low"),
                   "source_id": r.get("source", src), "retrieved_at": r.get("retrieved", "")}
            obs_rows.append(rec)
            con.execute(
                "INSERT INTO fact_salary_yoe_obs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rec["role_id"], rec["country_code"], rec["yoe_min"], rec["yoe_max"],
                 rec["level_label"], rec["year"], rec["median"], rec["p25"], rec["p75"],
                 rec["currency_code"], rec["basis"], rec["sample_size"], rec["kind"],
                 rec["confidence"], rec["source_id"], rec["retrieved_at"]))
    counts["yoe_obs"] = len(obs_rows)
    counts["recovered"] = _recovered
    if _recovered:
        log.info("ladder_build: recovered %d role_id=null estimate rows via occupation code", _recovered)

    # ---- 3) fit pay-vs-experience curves from the observations -----------------
    if obs_rows:
        try:
            from backend.ml.salary_curve import fit_curves
            fitted = fit_curves(obs_rows)   # grouped by (role,country,kind,CURRENCY)
            # One (role,country,kind) can yield curves in >1 currency (two sources,
            # different units). The serving key has no currency axis, so keep ONLY the
            # best-supported currency's curve and log the drop — never interleave units.
            support: dict[tuple, dict[str, float]] = {}
            for c in fitted:
                key = (c["role_id"], c["country_code"], c["kind"])
                cur = c.get("currency_code") or ""
                support.setdefault(key, {})[cur] = (
                    support.get(key, {}).get(cur, 0.0) + float(c.get("n_effective") or 0))
            keep: dict[tuple, str] = {}
            for key, by_cur in support.items():
                best = max(by_cur, key=lambda cu: by_cur[cu])
                keep[key] = best
                if len(by_cur) > 1:
                    dropped = {cu: round(w, 1) for cu, w in by_cur.items() if cu != best}
                    log.warning("ladder_build: %s has curves in %d currencies — keeping "
                                "%s (support %.1f), dropping %s (units are never blended)",
                                key, len(by_cur), best or "?", by_cur[best], dropped)
            for c in fitted:
                key = (c["role_id"], c["country_code"], c["kind"])
                if (c.get("currency_code") or "") != keep[key]:
                    continue
                con.execute(
                    "INSERT INTO fact_salary_curve VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (role_id, country_code, kind, yoe) DO NOTHING",
                    (c["role_id"], c["country_code"], c["kind"], c["yoe"],
                     c["fit_median"], c["lo"], c["hi"], c["support"],
                     c["n_effective"], c["method"], c.get("currency_code") or None))
                counts["curves"] += 1
        except Exception as e:  # noqa: BLE001 — sklearn/scipy absent → skip curves
            log.warning("ladder_build: curve fit skipped (%s)", e)

    log.info("ladder_build v2: %s", counts)
    return counts
