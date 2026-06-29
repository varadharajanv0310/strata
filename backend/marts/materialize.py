"""Materialize query-ready **serving marts** from the DuckDB warehouse.

Reads the star schema, assembles the exact `mock.js`-shaped rows (series as JSON
per Role×Country), computes Market Pulse, and writes everything into the app/
serving DB (SQLite locally, Postgres in prod). Idempotent: clears + rewrites the
mart tables each run. Market Pulse is computed here (a real aggregate), not carried
from seed.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from sqlalchemy import delete

from backend.core.db import Base, engine, session_scope
from backend.core.logging import get_logger, stage_timer
from backend.core.db import duckdb_connect
from backend.app import models as _app_models  # noqa: F401  (register metadata)
from backend.marts import models as M
from backend.warehouse.seed import RESUME_SAMPLE, RESUME_B

log = get_logger("marts.materialize")

YEAR_2020_INDEX = 3  # YEARS = [2017..2025]

# Min-sample suppression (honesty): a salary LENS below its floor is nulled so the UI
# reads "not enough data" rather than showing a 1-respondent number as if it were a
# survey. Official anchors are aggregate statistics (no per-respondent sample) — kept
# and flagged 'official', never suppressed for low n.
MIN_SAMPLE_REALIZED = 30
MIN_SAMPLE_ADVERTISED = 10


def _group(rows, key_len):
    out = defaultdict(list)
    for row in rows:
        out[tuple(row[:key_len])].append(row)
    return out


def materialize_from_warehouse() -> None:
    Base.metadata.create_all(engine)  # ensure serving tables exist
    duck = duckdb_connect(read_only=True)
    try:
        with stage_timer(log, "marts.materialize"):
            countries = duck.execute(
                "SELECT code,name,currency_symbol,currency_code,nat_factor,ppp_rate,transparency,flag_c1,flag_c2,ord "
                "FROM dim_country ORDER BY ord"
            ).fetchall()
            roles = duck.execute(
                "SELECT role_id,name,family_id,family_name,family_hue,blurb,ord,is_seed FROM dim_role ORDER BY ord"
            ).fetchall()
            skills = duck.execute(
                "SELECT role_id,skill_name,level,ord FROM bridge_role_skill ORDER BY role_id,ord"
            ).fetchall()
            ladder = duck.execute(
                "SELECT role_id,ord,title,mult FROM bridge_role_ladder ORDER BY role_id,ord"
            ).fetchall()
            sources = dict(duck.execute("SELECT source_id,source_name FROM dim_source").fetchall())

            salary = duck.execute(
                "SELECT role_id,country_code,year,median,sample_size,confidence,kind,freshness,transparency,source_id,is_seed "
                "FROM fact_salary_job WHERE experience_code='pooled' ORDER BY role_id,country_code,year"
            ).fetchall()
            demand = duck.execute(
                "SELECT role_id,country_code,year,demand_index FROM fact_demand ORDER BY role_id,country_code,year"
            ).fetchall()
            forecast = duck.execute(
                "SELECT role_id,country_code,year,value,lo,hi FROM fact_demand_forecast ORDER BY role_id,country_code,year"
            ).fetchall()
            interest = duck.execute(
                "SELECT role_id,country_code,interest_index FROM fact_interest"
            ).fetchall()
            score = duck.execute(
                "SELECT role_id,country_code,total,demand_score,pay_score,opp_score,rank,pctile FROM fact_job_score"
            ).fetchall()

            # optional new-signal sources — guarded so an un-refused warehouse (no
            # new tables/rows yet) still materializes the core mart cleanly.
            def _q(sql):
                try:
                    return duck.execute(sql).fetchall()
                except Exception as e:  # noqa: BLE001
                    log.info("marts: optional source unavailable (%s)", str(e).splitlines()[0][:80])
                    return []
            person = _q("SELECT role_id,country_code,year,median,sample_size,currency_code,source_id FROM fact_salary_person "
                        "WHERE experience_code='pooled' ORDER BY role_id,country_code,year")
            official = _q("SELECT role_id,country_code,year,median,sample_size,currency_code,source_id FROM fact_salary_official "
                          "ORDER BY role_id,country_code,year")
            adjacency = _q("SELECT from_role,to_role,similarity,edge_type,source_id FROM bridge_role_adjacency "
                           "ORDER BY from_role,similarity DESC")
            importance = _q("SELECT role_id,skill_id,skill_name,importance,level,essential,source_id "
                            "FROM bridge_role_skill_importance ORDER BY role_id,importance DESC")
            outlook = _q("SELECT role_id,country_code,horizon_years,growth_pct,openings_per_year,"
                         "outlook_rating,shortage_flag,source_id FROM fact_role_outlook "
                         "ORDER BY role_id,country_code,horizon_years DESC")
            adoption = _q("SELECT skill_id,year,period,metric,value,ecosystem FROM fact_skill_adoption "
                          "ORDER BY skill_id,metric,ecosystem,period")
            skill_names = dict(_q("SELECT skill_id,name FROM dim_skill"))
        meta_years = sorted({r[2] for r in salary})
        meta_fyears = sorted({r[2] for r in forecast})
        is_seed_overall = bool(roles and roles[0][7])

        salary_g = _group(salary, 2)
        demand_g = _group(demand, 2)
        forecast_g = _group(forecast, 2)
        interest_m = {(r[0], r[1]): int(round(r[2])) for r in interest}
        score_m = {(r[0], r[1]): r for r in score}

        # latest median per (role,country) for the REALIZED + OFFICIAL salary lenses
        # (rows arrive year-ascending, so the last write per key is the newest year).
        # Carries currency so the lens is never shown as a bare unit-less integer.
        country_cur = {c[0]: c[3] for c in countries}     # code -> ISO currency (advertised lens basis)

        def _latest(rows):
            m: dict[tuple, tuple] = {}
            for (rid, code, _yr, med, n, cur, src) in rows:
                if med is not None:
                    m[(rid, code)] = (float(med), int(n or 0), sources.get(src, src), cur or "")
            return m
        realized_m = _latest(person)
        official_m = _latest(official)

        # per-skill ADOPTION momentum (latest vs prior period) per metric × ecosystem
        adoption_marts: list = []
        _agrp: dict[tuple, list] = defaultdict(list)
        for (sid, _yr, period, metric, value, eco) in adoption:
            _agrp[(sid, metric, eco)].append((str(period), float(value)))
        for (sid, metric, eco), ser in _agrp.items():
            ser.sort()
            lp, lv = ser[-1]
            pv = ser[-2][1] if len(ser) > 1 else None
            mom = round((lv - pv) / pv * 100, 1) if pv else None
            adoption_marts.append(M.MartSkillAdoption(
                skill_id=sid, skill_name=skill_names.get(sid, sid), metric=metric,
                ecosystem=eco, latest_period=lp, latest_value=lv, momentum_pct=mom))

        # ---- assemble per role×country — from the UNION of all signals, NOT just the
        #      salary fact, so DERIVED / demand-only roles still surface (A4). Missing
        #      Job Score defaults to 0 (recomputed post-fusion) instead of KeyError (A7).
        all_keys = (set(salary_g) | set(demand_g) | set(forecast_g)
                    | set(realized_m) | set(official_m) | set(score_m) | set(interest_m))
        rc_rows: list[M.MartRoleCountry] = []
        rc_index: dict[tuple, dict] = {}
        for (rid, code) in sorted(all_keys):
            srows = salary_g.get((rid, code))
            series = ([{"year": int(y), "value": int(round(v))} for (_, _, y, v, *_rest) in srows]
                      if srows else [])
            dser = [{"year": int(y), "value": int(round(v))} for (_, _, y, v) in demand_g.get((rid, code), [])]
            fc = [{"year": int(y), "value": int(round(v)), "lo": int(round(lo)), "hi": int(round(hi))}
                  for (_, _, y, v, lo, hi) in forecast_g.get((rid, code), [])]
            rl = realized_m.get((rid, code))
            of = official_m.get((rid, code))
            # min-sample suppression (honesty): realized lens below the floor → null so
            # the UI reads "not enough data". Official anchors kept (aggregate, no sample).
            if rl and rl[1] < MIN_SAMPLE_REALIZED:
                rl = None
            # headline median = advertised (salary fact) → realized → official → None.
            if srows:
                last = srows[-1]
                median, sample, conf, kind, freshness, transparency, source_id, is_seed = (
                    float(series[-1]["value"]), last[4], last[5], last[6], last[7], last[8], last[9], bool(last[10]))
                cur_adv = country_cur.get(code)
            elif rl:
                median, sample, conf, kind, freshness, transparency, source_id, is_seed, cur_adv = (
                    rl[0], rl[1], "med", "person-level", "—", 0.0, "stack-overflow-survey", False, rl[3])
            elif of:
                median, sample, conf, kind, freshness, transparency, source_id, is_seed, cur_adv = (
                    of[0], of[1], "med", "official", "—", 0.0, "official-anchor", False, of[3])
            else:                                          # demand-only (derived) role — no salary anywhere
                median, sample, conf, kind, freshness, transparency, source_id, is_seed, cur_adv = (
                    None, 0, "low", "demand-only", "—", 0.0, "derived", False, None)
            sc = score_m.get((rid, code)) or (rid, code, 0.0, 0.0, 0.0, 0.0, 0, 0)
            row = M.MartRoleCountry(
                role_id=rid, country_code=code,
                median=float(median) if median is not None else None,
                demand=dser[-1]["value"] if dser else 0,
                interest=interest_m.get((rid, code), 0),
                currency_advertised=cur_adv,
                median_realized=rl[0] if rl else None,
                sample_realized=rl[1] if rl else None,
                source_realized=rl[2] if rl else None,
                currency_realized=rl[3] if rl else None,
                median_official=of[0] if of else None,
                sample_official=of[1] if of else None,
                source_official=of[2] if of else None,
                currency_official=of[3] if of else None,
                score_total=sc[2], score_demand=sc[3], score_pay=sc[4], score_opp=sc[5],
                score_rank=int(sc[6]), score_pctile=int(sc[7]),
                sample=int(sample), conf=conf, kind=kind, source=sources.get(source_id, source_id),
                freshness=freshness, transparency=float(transparency), is_seed=is_seed,
                series=series, demand_series=dser, forecast=fc,
            )
            rc_rows.append(row)
            rc_index[(rid, code)] = {"median": median or 0, "demand": row.demand,
                                     "score": row.score_total, "dser": dser}

        # ---- market pulse (computed aggregate, per country) ----
        pulse_rows: list[M.MartMarketPulse] = []
        role_ids = [r[0] for r in roles]
        for c in countries:
            code = c[0]
            present = [rid for rid in role_ids if (rid, code) in rc_index]

            def rank_by(metric):
                if metric == "rising":
                    def grow(rid):
                        d = rc_index[(rid, code)]
                        base = d["dser"][YEAR_2020_INDEX]["value"] if len(d["dser"]) > YEAR_2020_INDEX else 0
                        return d["demand"] - base
                    return sorted(present, key=grow, reverse=True)[:5]
                key = {"hottest": "demand", "topPay": "median", "topScore": "score"}[metric]
                return sorted(present, key=lambda rid: rc_index[(rid, code)][key], reverse=True)[:5]

            for kind in ("hottest", "topPay", "rising", "topScore"):
                for ordi, rid in enumerate(rank_by(kind)):
                    pulse_rows.append(M.MartMarketPulse(country_code=code, kind=kind, ord=ordi, role_id=rid))

        # ---- write ----
        with session_scope() as db:
            for model in (M.MartMarketPulse, M.MartRoleCountry, M.MartRoleLadder,
                          M.MartRoleSkill, M.MartRoleAdjacency, M.MartRoleSkillImportance,
                          M.MartRoleOutlook, M.MartSkillAdoption, M.MartRolePayLadder,
                          M.MartSkillPremium, M.MartRole, M.MartFamily, M.MartCountry, M.MartMeta):
                db.execute(delete(model))

            db.add_all([
                M.MartCountry(code=c[0], name=c[1], cur=c[2], cur_code=c[3], nat_factor=c[4],
                              ppp_rate=c[5], transparency=c[6], c1=c[7], c2=c[8], ord=c[9])
                for c in countries
            ])
            fam_seen: dict[str, M.MartFamily] = {}
            for r in roles:
                if r[2] not in fam_seen:
                    fam_seen[r[2]] = M.MartFamily(id=r[2], name=r[3], hue=r[4], ord=len(fam_seen))
            db.add_all(list(fam_seen.values()))
            db.add_all([M.MartRole(id=r[0], name=r[1], family_id=r[2], family_name=r[3],
                                   family_hue=r[4], blurb=r[5], ord=r[6]) for r in roles])
            db.add_all([M.MartRoleSkill(role_id=s[0], name=s[1], level=s[2],
                                        dura=0, trend="", ord=s[3]) for s in skills])  # dura/trend filled below
            db.add_all([M.MartRoleLadder(role_id=l[0], ord=l[1], title=l[2], mult=l[3]) for l in ladder])
            role_name = {r[0]: r[1] for r in roles}
            db.add_all([M.MartRoleAdjacency(
                from_role=a[0], to_role=a[1], to_role_name=role_name.get(a[1], a[1]),
                similarity=float(a[2]), edge_type=a[3], source=a[4]) for a in adjacency])
            db.add_all([M.MartRoleSkillImportance(
                role_id=i[0], skill_id=i[1], skill_name=i[2], importance=float(i[3]),
                level=(float(i[4]) if i[4] is not None else None),
                essential=bool(i[5]), source=i[6]) for i in importance])
            db.add_all([M.MartRoleOutlook(
                role_id=o[0], country_code=o[1], horizon_years=int(o[2]), growth_pct=float(o[3]),
                openings_per_year=(float(o[4]) if o[4] is not None else None),
                outlook_rating=o[5], shortage_flag=o[6], source=o[7]) for o in outlook])
            db.add_all(adoption_marts)
            # real H-1B pay ladders + hedonic skill premiums (from analytics staging)
            from backend.analytics.promotion_ladder import load_ladders
            from backend.ml.hedonic import load_premiums
            for L in load_ladders():
                steps = {s["to"]: s for s in L.get("steps", [])}
                for i, rung in enumerate(L.get("rungs", [])):
                    st = steps.get(rung["label"], {})
                    db.add(M.MartRolePayLadder(
                        role_id=L["role_id"], country_code=L.get("country", "US"), ord=i,
                        level_label=rung["label"], median=float(rung["median"]), n=int(rung["n"]),
                        step_abs=(float(st["abs"]) if st else None),
                        step_pct=(float(st["pct"]) if st else None)))
            db.add_all([M.MartSkillPremium(
                skill_id=p["skill_id"], skill_name=p["skill_name"],
                premium_pct=float(p["premium_pct"]), n=int(p["n"]), r2=float(p["r2"]))
                for p in load_premiums()])
            db.add_all(rc_rows)
            db.add_all(pulse_rows)
            db.add(M.MartMeta(key="dataset", value={
                "years": meta_years, "fyears": meta_fyears, "is_seed": is_seed_overall,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }))
            db.add(M.MartMeta(key="profiles", value={"sample": RESUME_SAMPLE, "b": RESUME_B}))

        # fill skill durability/trend from dim_skill (separate pass to keep insert simple)
        sk_meta = dict(duck.execute("SELECT name, durability FROM dim_skill").fetchall())
        sk_trend = dict(duck.execute("SELECT name, trend FROM dim_skill").fetchall())
        with session_scope() as db:
            for ms in db.query(M.MartRoleSkill).all():
                ms.dura = int(sk_meta.get(ms.name, 0))
                ms.trend = sk_trend.get(ms.name, "stable")

        log.info("marts: %d countries, %d roles, %d role×country, %d pulse rows, "
                 "%d adjacency edges, %d skill-importance rows",
                 len(countries), len(roles), len(rc_rows), len(pulse_rows),
                 len(adjacency), len(importance))
    finally:
        duck.close()

    # additive served slices — alias graph + provenance manifest — so a single
    # `marts-materialize` builds the complete served layer (non-fatal if absent).
    try:
        n_alias = materialize_aliases()
        n_prov = materialize_provenance()
        log.info("marts: +%d aliases, +%d provenance sources", n_alias, n_prov)
    except Exception as e:  # noqa: BLE001
        log.warning("marts: alias/provenance materialization skipped (%s)", e)


def materialize_aliases() -> int:
    """Materialize the warehouse alias graph (``dim_role_alias``) → ``mart_role_alias``.

    Additive to materialize_from_warehouse: backs the resolver with the full alias
    graph (the resolver also carries the curated seed in-process, so a missing table
    is non-fatal). Returns the row count.
    """
    Base.metadata.create_all(engine)
    duck = duckdb_connect(read_only=True)
    try:
        rows = duck.execute(
            "SELECT alias_id, surface, norm, role_id, source, lang, weight FROM dim_role_alias"
        ).fetchall()
    except Exception:  # noqa: BLE001 — table absent (taxonomy not built yet)
        log.info("dim_role_alias absent — run build_taxonomy first; skipping alias mart")
        return 0
    finally:
        duck.close()
    with session_scope() as db:
        db.execute(delete(M.MartRoleAlias))
        db.add_all([M.MartRoleAlias(alias_id=a, surface=s, norm=n, role_id=r,
                                    source=src, lang=lang, weight=w)
                    for (a, s, n, r, src, lang, w) in rows])
    log.info("mart_role_alias: %d aliases", len(rows))
    return len(rows)


def materialize_provenance() -> int:
    """Materialize the per-source provenance manifest → ``mart_provenance``.

    Threads (source_id, snapshot_hash, transform_version, row_count, as_of) into the
    served layer so a number's full lineage is answerable. Returns the row count.
    """
    from backend.warehouse.provenance import collect_provenance

    Base.metadata.create_all(engine)
    duck = duckdb_connect(read_only=True)
    try:
        manifest = collect_provenance(duck)
    finally:
        duck.close()
    with session_scope() as db:
        db.execute(delete(M.MartProvenance))
        db.add_all([M.MartProvenance(**m) for m in manifest])
    log.info("mart_provenance: %d sources", len(manifest))
    return len(manifest)
