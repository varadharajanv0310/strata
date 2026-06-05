"""Build the DuckDB warehouse (facts/dims/bridges) from a dataset dict.

`build_warehouse_from_dataset` loads the seed (or, later, normalized real data of
the same shape). `build_warehouse_from_staging` is the Phase-5 entry that builds
from ingested STAGING parquet — stubbed here and filled when connectors land.
Everything is loaded transactionally and the schema is rebuilt fresh each run so
the load is idempotent (brief §10).
"""
from __future__ import annotations

import re

from backend.core.db import duckdb_connect
from backend.core.logging import get_logger, stage_timer
from backend.warehouse.schema import create_warehouse_schema, drop_warehouse_schema

log = get_logger("warehouse.build")

EXPERIENCE_BANDS = [
    ("pooled", "All experience (pooled)", None, None),
    ("0-2", "0–2 years", 0, 2),
    ("3-5", "3–5 years", 3, 5),
    ("6-9", "6–9 years", 6, 9),
    ("10+", "10+ years", 10, None),
]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def build_warehouse_from_dataset(ds: dict, is_seed: bool = True) -> None:
    years = ds["years"]
    fyears = ds["fyears"]
    score_year = max(years)
    seed = bool(is_seed)

    con = duckdb_connect()
    try:
        with stage_timer(log, "warehouse.build_from_dataset"):
            drop_warehouse_schema(con)
            create_warehouse_schema(con)
            con.execute("BEGIN TRANSACTION")

            # ---- dim_time ----
            con.executemany(
                "INSERT INTO dim_time VALUES (?, ?)",
                [(y, False) for y in years] + [(y, True) for y in fyears],
            )

            # ---- dim_experience ----
            con.executemany("INSERT INTO dim_experience VALUES (?, ?, ?, ?)", EXPERIENCE_BANDS)

            # ---- dim_country + dim_ppp ----
            con.executemany(
                "INSERT INTO dim_country VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(c["code"], c["name"], c["cur"], c["curCode"], c["natFactor"], c["pppRate"],
                  c["transparency"], c["c1"], c["c2"], i)
                 for i, c in enumerate(ds["countries"])],
            )
            con.executemany(
                "INSERT INTO dim_ppp VALUES (?, ?, ?, ?, ?)",
                [(c["code"], y, c["pppRate"], None, "seed:ppp-flat" if seed else "oecd-ppp")
                 for c in ds["countries"] for y in years + fyears],
            )

            # ---- dim_source ----
            src_rows = []
            for name in ds["sources"]:
                src_rows.append((slug(name), name, "job-level", None, "representative seed source" if seed else None, seed, "2026-06-07"))
            con.executemany("INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?)", src_rows)

            # ---- dim_skill (from role skills) ----
            seen_skills: dict[str, tuple] = {}
            for role in ds["roles"]:
                for sk in role["skills"]:
                    sid = slug(sk["name"])
                    if sid not in seen_skills:
                        seen_skills[sid] = (sid, sk["name"], sk["dura"], sk["trend"], "seed" if seed else "lightcast")
            con.executemany("INSERT INTO dim_skill VALUES (?, ?, ?, ?, ?)", list(seen_skills.values()))

            # ---- dim_role + bridges ----
            role_rows, skill_rows, ladder_rows = [], [], []
            for ri, role in enumerate(ds["roles"]):
                fam = role["family"]
                role_rows.append((role["id"], role["name"], fam["id"], fam["name"], fam["hue"], role["blurb"], None, seed, ri))
                for i, sk in enumerate(role["skills"]):
                    skill_rows.append((role["id"], slug(sk["name"]), sk["name"], sk["level"], i))
                for i, (title, mult) in enumerate(role["ladder"]):
                    ladder_rows.append((role["id"], i, title, mult))
            con.executemany("INSERT INTO dim_role VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", role_rows)
            con.executemany("INSERT INTO bridge_role_skill VALUES (?, ?, ?, ?, ?)", skill_rows)
            con.executemany("INSERT INTO bridge_role_ladder VALUES (?, ?, ?, ?)", ladder_rows)

            # ---- facts ----
            f_salary, f_demand, f_interest, f_forecast, f_score = [], [], [], [], []
            for role in ds["roles"]:
                rid = role["id"]
                for code, cd in role["countries"].items():
                    cur = next(c["curCode"] for c in ds["countries"] if c["code"] == code)
                    src_id = slug(cd["source"])
                    # salary series (job-level) — provenance carried on each row
                    for pt in cd["series"]:
                        f_salary.append((rid, code, "pooled", pt["year"], float(pt["value"]), cur,
                                         cd["sample"], cd["conf"], cd["freshness"], cd["kind"],
                                         cd["transparency"], src_id, seed))
                    # demand series
                    for pt in cd["demandSeries"]:
                        f_demand.append((rid, code, pt["year"], float(pt["value"]), None,
                                         cd["sample"], cd["conf"], src_id, seed))
                    # interest (scalar → latest year)
                    f_interest.append((rid, code, score_year, float(cd["interest"]),
                                       slug("Platform learner-interest signals"), seed))
                    # forecast
                    for pt in cd["forecast"]:
                        f_forecast.append((rid, code, pt["year"], float(pt["value"]),
                                           float(pt["lo"]), float(pt["hi"]), src_id, seed))
                    # job score components
                    sc = cd["score"]
                    f_score.append((rid, code, score_year, sc["total"], sc["demand"], sc["pay"],
                                    sc["opp"], sc["rank"], sc["pctile"], src_id, seed))

            con.executemany("INSERT INTO fact_salary_job VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", f_salary)
            con.executemany("INSERT INTO fact_demand VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", f_demand)
            con.executemany("INSERT INTO fact_interest VALUES (?, ?, ?, ?, ?, ?)", f_interest)
            con.executemany("INSERT INTO fact_demand_forecast VALUES (?, ?, ?, ?, ?, ?, ?, ?)", f_forecast)
            con.executemany("INSERT INTO fact_job_score VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", f_score)

            con.execute("COMMIT")
            log.info("loaded %d salary, %d demand, %d forecast, %d score rows (%d roles × %d countries)",
                     len(f_salary), len(f_demand), len(f_forecast), len(f_score),
                     len(ds["roles"]), len(ds["countries"]))
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def build_warehouse_from_staging() -> None:
    """Phase 5: build the warehouse from ingested STAGING parquet (real data).

    Lands when the GPU-normalized staging tables exist (skill ids, derived roles,
    deduped postings). Until then this raises so the CLI reports it clearly.
    """
    raise NotImplementedError(
        "warehouse-from-staging builds from real ingested data (Phase 5). "
        "Run `seed` for the seed-backed warehouse, or ingest sources first."
    )
