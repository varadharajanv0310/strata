"""Build the DuckDB warehouse (facts/dims/bridges) from a dataset dict.

`build_warehouse_from_dataset` loads the seed (or, later, normalized real data of
the same shape). `build_warehouse_from_staging` is the Phase-5 entry that builds
from ingested STAGING parquet — stubbed here and filled when connectors land.
Everything is loaded transactionally and the schema is rebuilt fresh each run so
the load is idempotent (brief §10).
"""
from __future__ import annotations

import json
import re

from backend.core.config import settings
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


# ============================================================================
# Phase 5: fuse the ingested STAGING aggregates into the warehouse facts.
# ============================================================================
#
# Fusion precedence (council decision — logged at run time):
#   * fact_salary_job  (HEADLINE, job-level): Adzuna is AUTHORITATIVE. We reuse
#       build_real_dataset() (Adzuna salary+demand+transparency overlaid on the
#       curated catalogue) for these rows unchanged. Official aggregates (BLS/ONS)
#       would be calibration anchors, not a replacement.
#   * fact_salary_person (SEPARATE population, never blended with job-level):
#       Stack Overflow survey is AUTHORITATIVE (real, multi-year, multi-country,
#       130k respondents → ~1.7k cells). H-1B/OFLC CORROBORATES and fills only the
#       US cells SO does not cover — SO wins any (role,country,exp,year) collision.
#   * fact_demand: Common Crawl unique-posting VOLUME is primary per role×country;
#       GH Archive skill-adoption is a global corroborating trend (lower priority,
#       used only where CC volume is absent). Adzuna demand already lives in
#       fact_salary_job's sibling demand rows via the dataset path.
#   * fact_interest: Google Trends is the only real source → authoritative.
#   * dim_role / bridge_role_skill: role_derivation + skill_norm outputs are
#       layered in WHERE PRESENT (is_seed=False); otherwise the curated ROLE_DEFS
#       catalogue is kept (it is the product taxonomy).
#   * dim_ppp: already real (World Bank) via build_real_dataset. Kept.
#
# Every staging file is optional: we fuse what exists and skip what's absent, so a
# partial overnight run still produces a coherent warehouse.

def _staging_json(rel: str):
    p = settings.staging_dir / rel
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("staging %s unreadable: %s", rel, e)
        return None


def _read_parquet(rel: str):
    p = settings.staging_dir / rel
    if not p.exists():
        return None
    try:
        import pandas as pd
        return pd.read_parquet(p)
    except Exception as e:  # noqa: BLE001
        log.warning("staging %s unreadable: %s", rel, e)
        return None


def _person_rows_from_so(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_salary_person rows from the SO survey aggregate (authoritative)."""
    recs = _staging_json("so_survey/salary_agg.json")
    if not recs:
        return []
    rows = []
    sid = slug("Stack Overflow Survey")
    for r in recs:
        rid, code = r.get("role_id"), r.get("country_code")
        if rid not in known_roles or code not in known_countries:
            continue
        rows.append((rid, code, r.get("experience_code", "pooled"), int(r["year"]),
                     float(r["median"]), r.get("currency_code", "USD"),
                     int(r.get("sample_size") or 0), r.get("confidence", "low"),
                     "survey", "person-level", sid, False))
    return rows


def _person_rows_from_h1b(known_roles: set[str], known_countries: set[str],
                          taken: set[tuple]) -> list[tuple]:
    """fact_salary_person rows from H-1B/OFLC — US corroboration; SO wins overlaps."""
    recs = _staging_json("h1b/salary_agg.json") or _staging_json("dol_oflc/salary_agg.json")
    if not recs:
        return []
    rows = []
    sid = slug("US DOL OFLC H-1B")
    for r in recs:
        rid = r.get("role_id")
        code = r.get("country_code", "US")
        exp = r.get("experience_code", "pooled")
        year = int(r["year"])
        if rid not in known_roles or code not in known_countries:
            continue
        if (rid, code, exp, year) in taken:  # SO is authoritative on collision
            continue
        rows.append((rid, code, exp, year, float(r["median"]),
                     r.get("currency_code", "USD"), int(r.get("sample_size") or 0),
                     r.get("confidence", "med"), "disclosure", "person-level", sid, False))
    return rows


def _demand_overlay(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_demand rows from Common Crawl unique-posting volume (+ GH corroboration)."""
    rows: list[tuple] = []
    seen: set[tuple] = set()

    # 1) Common Crawl posting volume per (role, country) — primary.
    dedup = _read_parquet("normalized/posting_dedup.parquet")
    skills = _read_parquet("normalized/posting_skills.parquet")
    if dedup is not None and not dedup.empty and skills is not None and not skills.empty:
        try:
            import pandas as pd
            uniq = dedup[dedup["is_unique"]][["posting_id", "country"]]
            sk = skills[["posting_id", "skill_id"]]
            # map skills→roles via curated bridge so postings count toward roles
            from backend.warehouse.seed import ROLE_DEFS
            skill_to_roles: dict[str, list[str]] = {}
            for d in ROLE_DEFS:
                for (n, _lvl) in d["sk"]:
                    skill_to_roles.setdefault(slug(n), []).append(d["id"])
            sk = sk.merge(uniq, on="posting_id", how="inner")
            counts: dict[tuple, int] = {}
            for _, row in sk.iterrows():
                for rid in skill_to_roles.get(row["skill_id"], []):
                    counts[(rid, row["country"])] = counts.get((rid, row["country"]), 0) + 1
            if counts:
                mx = max(counts.values())
                year = max(_YEARS)
                sid = slug("Common Crawl JobPosting")
                for (rid, code), c in counts.items():
                    if rid not in known_roles or code not in known_countries:
                        continue
                    idx = round(100 * (c / mx) ** 0.5)
                    rows.append((rid, code, year, float(idx), int(c), c, "med", sid, False))
                    seen.add((rid, code, year))
        except Exception as e:  # noqa: BLE001
            log.warning("CC demand overlay failed: %s", e)

    # 2) GH Archive skill-adoption demand — corroboration only (fill CC gaps).
    #    The connector emits skill-scoped event counts (scope/key/year/events). We
    #    map skills→roles via the curated bridge, aggregate events per role, and
    #    spread the global signal across all countries (GH has no geo) as a flagged
    #    corroboration. Role-scoped records (role_id/country_code) are also accepted.
    gh = _staging_json("gh_archive/demand.json")
    if gh:
        sid = slug("GH Archive")
        records = gh if isinstance(gh, list) else gh.get("records", [])
        from backend.warehouse.build import slug as _slug  # local alias
        # bridge skill_id -> roles
        from backend.warehouse.seed import ROLE_DEFS
        skill_to_roles: dict[str, list[str]] = {}
        for d in ROLE_DEFS:
            for (n, _lvl) in d["sk"]:
                skill_to_roles.setdefault(slug(n), []).append(d["id"])

        role_events: dict[tuple, int] = {}  # (role_id, year) -> events
        for r in records:
            year = int(r.get("year") or max(_YEARS))
            if r.get("role_id"):  # role-scoped record (future format)
                rid = r["role_id"]
                code = r.get("country_code", "US")
                if rid in known_roles and code in known_countries and (rid, code, year) not in seen:
                    rows.append((rid, code, year, float(r.get("demand_index", 0)),
                                 r.get("postings_count"), int(r.get("sample_size") or 0),
                                 r.get("confidence", "low"), sid, False))
                    seen.add((rid, code, year))
                continue
            if r.get("scope") == "skill":  # skill-scoped record (current format)
                sk_id = slug(str(r.get("key", "")))
                ev = int(r.get("events") or 0)
                for rid in skill_to_roles.get(sk_id, []):
                    role_events[(rid, year)] = role_events.get((rid, year), 0) + ev

        if role_events:
            mx = max(role_events.values())
            for (rid, year), ev in role_events.items():
                if rid not in known_roles:
                    continue
                idx = round(100 * (ev / mx) ** 0.5) if mx else 0
                for code in known_countries:  # global signal → all countries (flagged)
                    if (rid, code, year) in seen:  # CC primary wins
                        continue
                    rows.append((rid, code, year, float(idx), None,
                                 ev, "low", sid, False))
                    seen.add((rid, code, year))
    return rows


def _interest_overlay(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_interest rows from Google Trends (authoritative for the interest axis)."""
    gt = _staging_json("google_trends/interest.json")
    if not gt:
        return []
    rows = []
    sid = slug("Google Trends")
    for r in (gt if isinstance(gt, list) else gt.get("records", [])):
        rid, code, year = r.get("role_id"), r.get("country_code"), int(r.get("year"))
        if rid not in known_roles or code not in known_countries:
            continue
        rows.append((rid, code, year, float(r.get("interest_index", 0)), sid, False))
    return rows


# captured for the demand year fallback
_YEARS: list[int] = []


def build_warehouse_from_staging() -> None:
    """Phase 5: FUSE ingested STAGING aggregates into the warehouse (is_seed=False).

    Builds the dimensional spine + the Adzuna-authoritative job-level facts from
    build_real_dataset() (falls back to the seed shape if Adzuna staging is absent),
    then overlays the real person-level / demand / interest aggregates. Tolerant of
    missing staging files. Schema rebuilt fresh, loaded transactionally → idempotent.
    """
    global _YEARS
    with stage_timer(log, "warehouse.build_from_staging"):
        # ---- 1) dimensional spine + headline job-level facts (Adzuna real) ----
        try:
            from backend.warehouse.real_build import build_real_dataset
            ds = build_real_dataset()
            base = "adzuna-real"
        except Exception as e:  # noqa: BLE001 — Adzuna staging may be absent
            log.warning("real dataset unavailable (%s) — building dims from seed shape", e)
            from backend.warehouse.seed import build_seed_dataset
            ds = build_seed_dataset()
            ds["is_seed"] = False  # facts we add below are real; dims are the catalogue
            base = "seed-shape"

        _YEARS = list(ds["years"])
        # build the spine + job-level facts via the shared loader (is_seed=False)
        build_warehouse_from_dataset(ds, is_seed=False)

        known_roles = {r["id"] for r in ds["roles"]}
        known_countries = {c["code"] for c in ds["countries"]}

        # ---- 2) overlay the real staging aggregates ----
        con = duckdb_connect()
        try:
            con.execute("BEGIN TRANSACTION")

            # ensure the two real source rows exist in dim_source
            extra_sources = [
                ("Stack Overflow Survey", "person-level"),
                ("US DOL OFLC H-1B", "person-level"),
                ("Common Crawl JobPosting", "job-level"),
                ("GH Archive", "demand"),
                ("Google Trends", "interest"),
            ]
            for name, kind in extra_sources:
                con.execute(
                    "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (source_id) DO NOTHING",
                    (slug(name), name, kind, None, "real ingested source", False, "2026-06-24"),
                )

            # fact_salary_person ← SO (authoritative) + H-1B (US gap-fill)
            so_rows = _person_rows_from_so(known_roles, known_countries)
            taken = {(r[0], r[1], r[2], r[3]) for r in so_rows}
            h1b_rows = _person_rows_from_h1b(known_roles, known_countries, taken)
            person_rows = so_rows + h1b_rows
            if person_rows:
                con.executemany(
                    "INSERT INTO fact_salary_person VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (role_id, country_code, experience_code, year) DO NOTHING",
                    person_rows,
                )

            # fact_demand ← Common Crawl volume (primary) + GH Archive (corroborate)
            demand_rows = _demand_overlay(known_roles, known_countries)
            if demand_rows:
                con.executemany(
                    "INSERT INTO fact_demand VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (role_id, country_code, year) DO UPDATE SET "
                    "demand_index=excluded.demand_index, postings_count=excluded.postings_count, "
                    "source_id=excluded.source_id, is_seed=FALSE",
                    demand_rows,
                )

            # fact_interest ← Google Trends
            interest_rows = _interest_overlay(known_roles, known_countries)
            if interest_rows:
                con.executemany(
                    "INSERT INTO fact_interest VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (role_id, country_code, year) DO UPDATE SET "
                    "interest_index=excluded.interest_index, source_id=excluded.source_id, is_seed=FALSE",
                    interest_rows,
                )

            # dim_role lineage ← role_derivation (layer in derived clusters, keep curated)
            derived = _read_parquet("normalized/derived_roles.parquet")
            n_derived = 0
            if derived is not None and not derived.empty:
                for _, r in derived.iterrows():
                    con.execute(
                        "INSERT INTO dim_role VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (role_id) DO NOTHING",
                        (r["role_id"], r["label_title"], "derived", "Derived", 230,
                         f"derived from {int(r['posting_count'])} postings in {r['country']}",
                         r.get("member_titles"), False, 900 + n_derived),
                    )
                    n_derived += 1

            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

        # ---- 3) report real fused counts ----
        con = duckdb_connect(read_only=True)
        try:
            n_person = con.execute("SELECT COUNT(*) FROM fact_salary_person").fetchone()[0]
            n_so = con.execute(
                "SELECT COUNT(*) FROM fact_salary_person WHERE source_id = ?",
                (slug("Stack Overflow Survey"),)).fetchone()[0]
            n_demand = con.execute("SELECT COUNT(*) FROM fact_demand WHERE NOT is_seed").fetchone()[0]
            n_demand_cc = con.execute(
                "SELECT COUNT(*) FROM fact_demand WHERE source_id IN (?, ?)",
                (slug("Common Crawl JobPosting"), slug("GH Archive"))).fetchone()[0]
            n_interest = con.execute("SELECT COUNT(*) FROM fact_interest WHERE NOT is_seed").fetchone()[0]
            n_interest_gt = con.execute(
                "SELECT COUNT(*) FROM fact_interest WHERE source_id = ?",
                (slug("Google Trends"),)).fetchone()[0]
            n_job = con.execute("SELECT COUNT(*) FROM fact_salary_job").fetchone()[0]
        finally:
            con.close()
        log.info("FUSED (%s base): fact_salary_person=%d (SO=%d, H1B=%d), "
                 "fact_demand=%d (CC/GH overlay=%d, rest Adzuna), "
                 "fact_interest=%d (GTrends overlay=%d), fact_salary_job=%d (Adzuna)",
                 base, n_person, n_so, n_person - n_so, n_demand, n_demand_cc,
                 n_interest, n_interest_gt, n_job)
        log.info("council/fusion precedence: salary_job←Adzuna(auth); "
                 "salary_person←SO(auth)+H1B(US gap-fill); demand←CommonCrawl(primary)+GHArchive; "
                 "interest←GoogleTrends; dim_role←curated+derived; dim_ppp←WorldBank")
