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
        meta_years = sorted({r[2] for r in salary})
        meta_fyears = sorted({r[2] for r in forecast})
        is_seed_overall = bool(roles and roles[0][7])

        salary_g = _group(salary, 2)
        demand_g = _group(demand, 2)
        forecast_g = _group(forecast, 2)
        interest_m = {(r[0], r[1]): int(round(r[2])) for r in interest}
        score_m = {(r[0], r[1]): r for r in score}

        # ---- assemble per role×country ----
        rc_rows: list[M.MartRoleCountry] = []
        rc_index: dict[tuple, dict] = {}
        for (rid, code), srows in salary_g.items():
            series = [{"year": int(y), "value": int(round(v))} for (_, _, y, v, *_rest) in srows]
            last = srows[-1]
            sample, conf, kind, freshness, transparency, source_id = last[4], last[5], last[6], last[7], last[8], last[9]
            dser = [{"year": int(y), "value": int(round(v))} for (_, _, y, v) in demand_g.get((rid, code), [])]
            fc = [{"year": int(y), "value": int(round(v)), "lo": int(round(lo)), "hi": int(round(hi))}
                  for (_, _, y, v, lo, hi) in forecast_g.get((rid, code), [])]
            sc = score_m[(rid, code)]
            row = M.MartRoleCountry(
                role_id=rid, country_code=code,
                median=float(series[-1]["value"]),
                demand=dser[-1]["value"] if dser else 0,
                interest=interest_m.get((rid, code), 0),
                score_total=sc[2], score_demand=sc[3], score_pay=sc[4], score_opp=sc[5],
                score_rank=int(sc[6]), score_pctile=int(sc[7]),
                sample=int(sample), conf=conf, kind=kind, source=sources.get(source_id, source_id),
                freshness=freshness, transparency=float(transparency), is_seed=bool(last[10]),
                series=series, demand_series=dser, forecast=fc,
            )
            rc_rows.append(row)
            rc_index[(rid, code)] = {"median": row.median, "demand": row.demand,
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
                          M.MartRoleSkill, M.MartRole, M.MartFamily, M.MartCountry, M.MartMeta):
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

        log.info("marts: %d countries, %d roles, %d role×country, %d pulse rows",
                 len(countries), len(roles), len(rc_rows), len(pulse_rows))
    finally:
        duck.close()
