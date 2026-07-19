"""Generated catalog dataset — the model-authored catalog IS the product spine.

strata's catalog is **generated from model knowledge** of the tech job market (hundreds of
roles × 7 countries: skills, per-country salary medians, seniority ladder, description) —
not sourced from a dataset, taxonomy, or corpus. That is the honest day-one product: it is
exactly the data a person would find googling the role, surfaced in one comparable place.

Every generated cell is stamped ``source='Generated (model knowledge)'`` / ``kind='generated'``
so it is always distinguishable from measured data — the stamp is the honesty mechanism, it
NEVER withholds a cell. Measured pipeline data (Adzuna, Stack Overflow, H-1B, official
baselines) UPGRADES a generated cell where it exists (the measured stamp wins); where it
does not, the cell stays generated (filled, never blank).

This produces the SAME nested ``ds`` shape as ``build_seed_dataset`` — so the entire
warehouse→mart→services pipeline is unchanged — reading the generated catalog JSON the
generation pass wrote to ``staging/generated/catalog.json``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.seed import (
    COUNTRIES, FAMILIES, FYEARS, SK, YEARS,
    hash_str, jfix1, jround, ppp_usd, rng, round_nice,
)

log = get_logger("warehouse.generated_build")

CATALOG_REL = "generated/catalog.json"                    # optional staging OVERRIDE (regeneration)
# The generated catalog is durable PRODUCT DATA (the spine), so it lives tracked in the
# package — not under gitignored staging/. A staging copy, if present, overrides it (so a
# future regeneration run can supersede the committed catalog without editing source).
TRACKED_CATALOG = Path(__file__).resolve().parent / "generated_catalog.json"
GENERATED_SOURCE = "Generated (model knowledge)"   # dim_source name → mart 'source' badge
GENERATED_KIND = "generated"                        # mart 'kind' → the provenance stamp
_DEFAULT_SK = (60, "stable")                        # graceful durability/trend for any unlisted skill


def catalog_path():
    """Staging override if present (regeneration), else the tracked package catalog."""
    override = settings.staging_dir / CATALOG_REL
    return override if override.exists() else TRACKED_CATALOG


def has_generated_catalog() -> bool:
    p = catalog_path()
    if not p.exists():
        return False
    try:
        cat = json.loads(p.read_text(encoding="utf-8"))
        return bool(cat.get("roles"))
    except Exception:  # noqa: BLE001
        return False


def load_generated_catalog() -> dict | None:
    p = catalog_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("generated catalog unreadable (%s)", e)
        return None


def _skill_meta(skill_vocab: dict, name: str) -> tuple[int, str]:
    v = skill_vocab.get(name)
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return int(v[0]), str(v[1])
    return _DEFAULT_SK


def _early_median(d: dict, code: str) -> float | None:
    """The HEADLINE salary is the EARLY-CAREER figure — what a new entrant actually starts
    on — i.e. the FIRST progression stage. Falls back to the legacy single median, then
    None (caller scales US×natFactor). A mid-career blend must never sit in the headline."""
    stages = d.get("stages")
    if isinstance(stages, list) and stages:
        v = (stages[0].get("salary") or {}).get(code)
        if v:
            return float(v)
    v = (d.get("salary") or {}).get(code)
    return float(v) if v else None


def _senior_stage_index(stages: list) -> int:
    """The rung the ladder multiplier is anchored to (Senior = 1.0)."""
    for i, s in enumerate(stages):
        if "senior" in str(s.get("title", "")).lower():
            return i
    for i, s in enumerate(stages):
        if 5 <= int(s.get("yoe_min", 0) or 0) <= 9:
            return i
    return min(len(stages) - 1, len(stages) // 2)


def _ladder_from_stages(d: dict) -> list | None:
    """Build the curated-style [[title, mult]] ladder from the generated progression stages
    (mult vs the Senior rung's US salary), so the existing ladder mart renders the path."""
    stages = d.get("stages")
    if not (isinstance(stages, list) and stages):
        return None
    us = [float((s.get("salary") or {}).get("US") or 0) for s in stages]
    ai = _senior_stage_index(stages)
    base = us[ai] or next((x for x in reversed(us) if x), 1.0) or 1.0
    return [[str(s.get("title") or d.get("name") or "role"),
             round((us[i] / base) if base else 1.0, 3)] for i, s in enumerate(stages)]


def build_generated_dataset(catalog: dict | None = None) -> dict:
    """Expand the generated catalog JSON into the full nested ``ds`` (generated-stamped).

    Mirrors ``build_seed_dataset``'s per-country expansion (series / demand / forecast /
    score) but anchors each country's salary to the GENERATED per-country median (real
    local-currency reality) rather than usMedian×natFactor, and stamps every cell generated.
    Pass ``catalog`` to expand an in-memory catalog (tests); default reads it from staging.
    """
    cat = catalog if catalog is not None else load_generated_catalog()
    if not cat or not cat.get("roles"):
        raise RuntimeError("no generated catalog at %s — run the generation pass first"
                           % catalog_path())
    role_defs = cat["roles"]
    # merged skill vocab: curated SK durability/trend, extended with the generated vocab
    skill_vocab = {k: list(v) for k, v in SK.items()}
    for k, v in (cat.get("skillVocab") or {}).items():
        skill_vocab[k] = v

    roles: list[dict] = []
    for d in role_defs:
        fam = next((f for f in FAMILIES if f["id"] == d.get("family")), FAMILIES[0])
        countries: dict[str, dict] = {}
        for co in COUNTRIES:
            code = co["code"]
            r = rng(hash_str(d["id"] + code))
            # HEADLINE = early-career (first progression stage). The full progression fills
            # the experience curve (see fuse_generated_progression); here the headline series
            # is anchored to the entry figure so it never reads as a mid-career blend.
            early = _early_median(d, code)
            median = round_nice(early, code) if early else round_nice(
                float(d.get("salary", {}).get("US", 100000)) * co["natFactor"], code)

            growth = float(d.get("growth", 0.3))
            tot_growth = growth * (0.7 + r() * 0.5)
            start = median / (1 + tot_growth)
            series = []
            for i, y in enumerate(YEARS):
                t = i / (len(YEARS) - 1)
                base = start * ((1 + tot_growth) ** t)
                noise = 1 + (r() - 0.5) * 0.05
                dip = 0.985 if (y == 2020 or y == 2023) else 1
                series.append({"year": y, "value": round_nice(base * noise * dip, code)})
            series[-1]["value"] = median

            dem = int(d.get("demand", 60))
            ddelta = int(d.get("demandDelta", 10))
            d_cur = max(20, min(99, dem + (r() - 0.5) * 8))
            d_start = max(15, d_cur - ddelta - (r() - 0.5) * 6)
            demand_series = []
            for i, y in enumerate(YEARS):
                t = i / (len(YEARS) - 1)
                v = d_start + (d_cur - d_start) * (t ** 0.9) + (r() - 0.5) * 4
                demand_series.append({"year": y, "value": jround(max(10, min(100, v)))})
            demand_series[-1]["value"] = jround(d_cur)

            slope = (d_cur - d_start) / 8
            forecast = []
            for i, y in enumerate(FYEARS):
                step = i + 1
                v = max(10, min(100, d_cur + slope * step * (0.7 + r() * 0.3)))
                band = 4 + step * (4 + r() * 3)
                forecast.append({"year": y, "value": jround(v),
                                 "lo": jround(max(5, v - band)), "hi": jround(min(100, v + band))})

            interest = max(20, min(99, int(d.get("interest", 55)) + (r() - 0.5) * 8))

            pay_norm = min(10, (ppp_usd(median, code) / 130000) * 7.2)
            demand_score = d_cur / 10
            opportunity = max(0, min(10, ((d_cur - interest) + 50) / 10))
            total = jfix1(0.42 * demand_score + 0.33 * pay_norm + 0.25 * opportunity)

            countries[code] = {
                "median": median, "series": series, "demandSeries": demand_series, "forecast": forecast,
                "demand": jround(d_cur), "interest": jround(interest),
                "score": {"total": total, "demand": jfix1(demand_score), "pay": jfix1(pay_norm),
                          "opp": jfix1(opportunity)},
                # generated provenance: modest confidence, no measured sample, generated stamp
                "sample": 0, "conf": "low", "kind": GENERATED_KIND,
                "source": GENERATED_SOURCE, "freshness": "—", "transparency": 0.0,
            }

        skills = []
        for s in d.get("skills", []):
            nm, lvl = s.get("name"), s.get("importance", "I")
            if not nm:
                continue
            dura, trend = _skill_meta(skill_vocab, nm)
            skills.append({"name": nm, "level": lvl, "dura": dura, "trend": trend})
        ladder = _ladder_from_stages(d) or [
            [l.get("title", d["name"]), float(l.get("mult", 1.0))] for l in d.get("ladder", [])]
        if not ladder:
            ladder = [[d["name"], 1.0]]

        roles.append({
            "id": d["id"], "name": d["name"], "family": fam, "blurb": d.get("blurb", ""),
            "skills": skills, "ladder": ladder, "countries": countries,
        })

    # per-country rankings + percentiles (same as seed)
    for co in COUNTRIES:
        code = co["code"]
        sorted_roles = sorted(roles, key=lambda rr: rr["countries"][code]["score"]["total"], reverse=True)
        n = len(sorted_roles)
        for i, role in enumerate(sorted_roles):
            sc = role["countries"][code]["score"]
            sc["rank"] = i + 1
            sc["pctile"] = max(1, jround(i / max(1, n) * 100))

    # market pulse
    market_pulse: dict[str, dict] = {}
    for co in COUNTRIES:
        code = co["code"]
        hottest = sorted(roles, key=lambda rr: rr["countries"][code]["demand"], reverse=True)
        top_pay = sorted(roles, key=lambda rr: rr["countries"][code]["median"], reverse=True)
        top_score = sorted(roles, key=lambda rr: rr["countries"][code]["score"]["total"], reverse=True)
        rising = sorted(roles, key=lambda rr: rr["countries"][code]["demand"]
                        - rr["countries"][code]["demandSeries"][3]["value"], reverse=True)
        market_pulse[code] = {
            "hottest": [r["id"] for r in hottest[:5]], "topPay": [r["id"] for r in top_pay[:5]],
            "rising": [r["id"] for r in rising[:5]], "topScore": [r["id"] for r in top_score[:5]],
        }

    from backend.warehouse.seed import RESUME_B, RESUME_SAMPLE, SOURCES
    sources = list(SOURCES)
    if GENERATED_SOURCE not in sources:
        sources.append(GENERATED_SOURCE)
    log.info("generated dataset: %d roles × %d countries (generated spine)", len(roles), len(COUNTRIES))
    return {
        "countries": [dict(c) for c in COUNTRIES], "families": FAMILIES, "roles": roles,
        "years": YEARS, "fyears": FYEARS, "marketPulse": market_pulse,
        "resume_sample": RESUME_SAMPLE, "resume_b": RESUME_B,
        "sources": sources, "is_seed": False, "generated": True,
    }


def fuse_generated_progression(con, catalog: dict | None = None) -> dict:
    """Fuse the GENERATED salary progression into the experience substrate (the orphaned v2
    tables, now load-bearing). Per role×country×stage it writes a ``fact_salary_yoe_obs``
    observation + a ``fact_salary_curve`` point (kind='generated'); per role×stage a
    ``dim_ladder_rung`` spine row; and per role its ``leads_to`` career-trajectory edges into
    ``bridge_role_adjacency``. This is the GENERATED SPINE of the experience axis — every
    role×country×stage filled. Measured per-YoE data (``build_ladder_v2``) coexists as its
    own lens (kind='estimate'/'realized'/…) and UPGRADES it; generated stays where no measured
    stage-data exists. Call AFTER build_ladder_v2 (which resets those two fact tables). Clears
    its own prior generated rows first, so a re-fuse never duplicates. Returns counts.
    """
    from backend.warehouse.build import slug

    cat = catalog if catalog is not None else load_generated_catalog()
    counts = {"yoe_obs": 0, "curve": 0, "rungs": 0, "edges": 0}
    if not cat or not cat.get("roles"):
        return counts
    gsrc = slug(GENERATED_SOURCE)                       # 'generated-model-knowledge'
    cur_by_code = {co["code"]: co["curCode"] for co in COUNTRIES}
    # role-name → id (for resolving leads_to labels to real catalog roles)
    name_to_id = {}
    for d in cat["roles"]:
        name_to_id[str(d.get("name", "")).strip().lower()] = d["id"]

    # clear prior generated rows (idempotent re-fuse)
    for sql in (
        f"DELETE FROM fact_salary_yoe_obs WHERE source_id = '{gsrc}'",
        "DELETE FROM fact_salary_curve WHERE kind = 'generated'",
        f"DELETE FROM dim_ladder_rung WHERE source_id = '{gsrc}'",
        f"DELETE FROM bridge_role_adjacency WHERE source_id = '{gsrc}'",
    ):
        try:
            con.execute(sql)
        except Exception:  # noqa: BLE001 — table may not exist yet on a partial warehouse
            pass

    year = max(YEARS)
    rung_rows, obs_rows, curve_rows, edge_rows = [], [], [], []
    curve_seen: set = set()          # (role,country,kind,yoe) — respect the curve PK
    for d in cat["roles"]:
        rid = d["id"]
        stages = d.get("stages")
        if not (isinstance(stages, list) and stages):
            continue
        prev_yoe = -1
        for i, s in enumerate(stages):
            title = str(s.get("title") or d.get("name") or "role")[:80]
            ymin = int(s.get("yoe_min") or 0)
            ymax = int(s.get("yoe_max") or (ymin + 3))
            # strictly-increasing curve x so the points stay monotone in yoe
            xyoe = ymin if ymin > prev_yoe else prev_yoe + 1
            prev_yoe = xyoe
            rung_rows.append((rid, f"L{i}", i, title, "ic", float(ymin), float(ymax),
                              "generated", None, gsrc))
            sal = s.get("salary") or {}
            for code, cur in cur_by_code.items():
                med = sal.get(code)
                if not med:
                    continue
                med = float(med)
                obs_rows.append((rid, code, float(ymin), float(ymax), title, year, med,
                                 round(med * 0.88), round(med * 1.15), cur, "generated-annual",
                                 0, "generated", "low", gsrc, ""))
                ckey = (rid, code, "generated", int(xyoe))
                if ckey not in curve_seen:
                    curve_seen.add(ckey)
                    curve_rows.append((rid, code, "generated", int(xyoe), med,
                                       round(med * 0.85), round(med * 1.18), "generated",
                                       0.0, "generated", cur))
        seen_to: set[str] = set()
        for j, label in enumerate(d.get("leads_to") or []):
            lab = str(label).strip()
            if not lab:
                continue
            # link to a real catalog role when the label names one; else keep the human
            # label as the destination (the mart shows to_role as the name when unresolved).
            to_id = name_to_id.get(lab.lower()) or lab
            if to_id == rid or to_id in seen_to:
                continue
            seen_to.add(to_id)
            edge_rows.append((rid, to_id, round(1.0 - j * 0.12, 3), "progression", gsrc))

    # Bulk-insert via a registered DataFrame + INSERT…SELECT. Rows are already de-duplicated
    # in Python against each table's PK (rung_code unique per role; curve keyed by curve_seen;
    # edges by seen_to; from_role scopes adjacency) and the DELETEs cleared prior generated
    # rows, so no conflict handling is needed. This is ~50x faster than executemany, which is
    # row-by-row under the hood (~50s for these ~24k rows inside the fuse transaction).
    import pandas as pd

    def _bulk(table: str, rows: list, cols: list):
        if not rows:
            return
        df = pd.DataFrame(rows, columns=cols)  # noqa: F841 — referenced by the SQL view below
        con.execute(f"INSERT INTO {table} SELECT * FROM df")

    _bulk("dim_ladder_rung", rung_rows,
          ["role_id", "rung_code", "ord", "title", "track", "yoe_min", "yoe_max",
           "yoe_confidence", "fork_from", "source_id"])
    _bulk("fact_salary_yoe_obs", obs_rows,
          ["role_id", "country_code", "yoe_min", "yoe_max", "level_label", "year", "median",
           "p25", "p75", "currency_code", "basis", "sample_size", "kind", "confidence",
           "source_id", "retrieved_at"])
    _bulk("fact_salary_curve", curve_rows,
          ["role_id", "country_code", "kind", "yoe", "fit_median", "lo", "hi", "support",
           "n_effective", "method", "currency_code"])
    _bulk("bridge_role_adjacency", edge_rows,
          ["from_role", "to_role", "similarity", "edge_type", "source_id"])
    counts.update(rungs=len(rung_rows), yoe_obs=len(obs_rows),
                  curve=len(curve_rows), edges=len(edge_rows))
    log.info("generated progression fused: %s", counts)
    return counts
