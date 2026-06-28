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


def _official_salary_rows(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_salary_official rows — the THIRD salary lens, from the official national
    statistical anchors already ingested by the baselines connector (BLS OEWS / ONS
    ASHE / Eurostat / MOM / StatCan). These are real, role-crosswalked, per-country
    medians that previously landed in NO salary fact. Kept SEPARATE from advertised
    (fact_salary_job) and realized (fact_salary_person) — three lenses, never blended.
    """
    try:
        from backend.ingest.baselines import load_all
        recs = load_all()
    except Exception as e:  # noqa: BLE001 — baselines staging may be absent
        log.info("official salary: baselines unavailable (%s)", e)
        return []
    rows = []
    for r in recs or []:
        rid, code = r.get("role_id"), r.get("country_code")
        median = r.get("median")
        if rid not in known_roles or code not in known_countries or not median:
            continue
        rows.append((rid, code, int(r.get("year") or max(_YEARS)), float(median),
                     r.get("currency_code", "USD"), int(r.get("sample_size") or 0),
                     r.get("confidence", "high"), "official",
                     slug(r.get("source", "official baseline")), False))

    # ILOSTAT cross-country wage spine (when landed) — the harmonized 'ilostat' source.
    # Monthly earnings → annualized; ISCO-08 → role via the curated crosswalk. Graceful
    # when the connector has not been run (the bulk of council sources land later).
    try:
        from backend.ingest.ilostat import load_earnings
        from backend.warehouse.taxonomy import GOV_CROSSWALK
        isco_to_role = {str(code): rid for rid, sysd in GOV_CROSSWALK.items()
                        for sysn, (code, _l) in sysd.items() if "isco" in sysn.lower()}
        for r in load_earnings():
            rid = isco_to_role.get(str(r.get("isco08")))
            code, earn = r.get("country"), r.get("earnings")
            if rid in known_roles and code in known_countries and earn:
                rows.append((rid, code, int(r.get("year") or max(_YEARS)), float(earn) * 12.0,
                             r.get("currency") or "", 0, "med", "official", "ilostat", False))
    except Exception as e:  # noqa: BLE001
        log.info("official salary: ILOSTAT unavailable (%s)", e)
    return rows


def _demand_from_skill_feeds(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_demand CORROBORATION from the skill-bearing vacancy feeds (EURES / HN
    Who-is-Hiring / RemoteOK / MyCareersFuture). Maps each posting's skills → roles via
    the curated skill bridge (the same path GH Archive uses), counts role×country, and
    lands a low-confidence demand index per feed-source. Graceful/empty until those
    connectors run; never overrides the Common-Crawl/Adzuna primary (DO NOTHING at the
    call site). Postings without a real country are skipped (no faked geography)."""
    from collections import defaultdict
    from backend.warehouse.seed import ROLE_DEFS
    skill_to_roles: dict[str, list[str]] = {}
    for d in ROLE_DEFS:
        for (n, _lvl) in d["sk"]:
            skill_to_roles.setdefault(slug(n), []).append(d["id"])

    feeds = ["eures/vacancies.json", "hn_hiring/postings.json",
             "remoteok/postings.json", "mycareersfuture/postings.json"]
    per_feed: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
    for rel in feeds:
        recs = _staging_json(rel)
        if not recs:
            continue
        feed = rel.split("/")[0]
        for r in recs:
            country = r.get("country")
            if country not in known_countries:
                continue
            roles: set[str] = set()
            for sk in (r.get("skills") or []):
                roles.update(skill_to_roles.get(slug(str(sk)), []))
            for rid in roles:
                if rid in known_roles:
                    per_feed[feed][(rid, country)] += 1

    rows: list[tuple] = []
    year = max(_YEARS) if _YEARS else 2025
    for feed, cc in per_feed.items():
        mx = max(cc.values()) if cc else 1
        for (rid, country), c in cc.items():
            idx = round(100 * (c / mx) ** 0.5)
            rows.append((rid, country, year, float(idx), c, c, "low", slug(feed), False))
    return rows


def _official_from_entgeltatlas(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_salary_official ← Germany Entgeltatlas (bundesagentur wages.json). KldB
    occupation → role via the curated KldB map; monthly gross → annualized. DE only."""
    recs = _staging_json("bundesagentur/wages.json")
    if not recs or "DE" not in known_countries:
        return []
    from backend.warehouse.taxonomy import KLDB_TO_ROLE
    rows = []
    for r in recs:
        rid = KLDB_TO_ROLE.get(str(r.get("kldb")))
        med = r.get("median_gross_monthly")
        if rid in known_roles and med:
            rows.append((rid, "DE", int(r.get("year") or (max(_YEARS) if _YEARS else 2025)),
                         float(med) * 12.0, "EUR", 0, "high", "official", "entgeltatlas", False))
    return rows


def _official_from_usajobs(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_salary_official ← US federal pay (usajobs). OPM series → role; the median
    of (min+max)/2 across postings is the public-sector pay floor/ceiling per role."""
    import statistics
    from collections import defaultdict
    recs = _staging_json("usajobs/postings.json")
    if not recs or "US" not in known_countries:
        return []
    from backend.warehouse.taxonomy import OPM_TO_ROLE
    by_role: dict[str, list[float]] = defaultdict(list)
    for r in recs:
        rid = OPM_TO_ROLE.get(str(r.get("series")))
        smin, smax = r.get("salary_min"), r.get("salary_max")
        mid = (smin + smax) / 2 if smin and smax else (smin or smax)
        if rid in known_roles and mid:
            by_role[rid].append(float(mid))
    year = max(_YEARS) if _YEARS else 2025
    return [(rid, "US", year, float(statistics.median(v)), "USD", len(v), "med", "official",
             "usajobs", False) for rid, v in by_role.items()]


def _advertised_from_title_feeds(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_salary_job (advertised lens) corroboration from gov-board POSTED salary
    (MyCareersFuture, SG). Title → role via the curated matcher; median per role×country.
    Conflict-skipped where Adzuna already has the cell, so it only gap-fills."""
    import statistics
    from collections import defaultdict
    from backend.warehouse.taxonomy import match_title_to_role
    _CUR = {"SG": "SGD", "US": "USD", "GB": "GBP", "IN": "INR", "CA": "CAD", "AU": "AUD", "DE": "EUR"}
    by: dict[tuple, list[float]] = defaultdict(list)
    for rel in ("mycareersfuture/postings.json",):
        recs = _staging_json(rel)
        if not recs:
            continue
        src = rel.split("/")[0]
        for r in recs:
            country = r.get("country")
            if country not in known_countries:
                continue
            rid = match_title_to_role(r.get("title"))
            smin, smax = r.get("salary_min"), r.get("salary_max")
            mid = (smin + smax) / 2 if smin and smax else (smin or smax)
            if rid in known_roles and mid:
                by[(rid, country, src)].append(float(mid))
    year = max(_YEARS) if _YEARS else 2025
    return [(rid, country, "pooled", year, float(statistics.median(vals)), _CUR.get(country, ""),
             len(vals), "med", "live", "job-level", 1.0, slug(src), False)
            for (rid, country, src), vals in by.items()]


_OUTLOOK_SRC = {"soc": "bls_ep", "noc": "ca_cops", "anzsco": "jsa", "isco": "ilostat"}


def _parse_horizon(h) -> int | None:
    """gov_projections horizon is a string ('2023-2033' | '3yr' | '10yr' | 'current').
    Return the horizon length in years, or None for a non-projection ('current')."""
    s = str(h or "").lower().strip()
    if s in ("", "current"):
        return None
    if "-" in s:                                          # '2023-2033' → span
        try:
            a, b = s.split("-")[:2]
            return max(1, int(b) - int(a))
        except (ValueError, IndexError):
            return 10
    digits = "".join(c for c in s if c.isdigit())
    return int(digits) if digits else 10


def _role_outlook_rows(known_roles: set[str], known_countries: set[str]) -> list[tuple]:
    """fact_role_outlook rows from the gov_projections staging (BLS-EP / Canada-COPS /
    JSA). Maps each national occupation code → our role via the per-system crosswalk
    (SOC via the H-1B map, NOC/ANZSCO/ISCO via the curated GOV_CROSSWALK). Graceful/
    empty until the connector has run."""
    recs = _staging_json("gov_projections/outlook.json")
    if not recs:
        return []
    from backend.ingest.h1b import _soc_to_role
    from backend.warehouse.taxonomy import GOV_CROSSWALK
    sys_maps: dict[str, dict[str, str]] = {}
    for rid, sysd in GOV_CROSSWALK.items():
        for sysn, (code, _l) in sysd.items():
            sys_maps.setdefault(sysn.lower(), {})[str(code)] = rid

    def to_role(system: str, code: str) -> str | None:
        s, c = (system or "").lower(), str(code or "")
        if "soc" in s:
            return _soc_to_role(c.split(".")[0])
        for m in sys_maps.values():                       # try any crosswalk by code/prefix
            if c in m:
                return m[c]
            if c[:4] in m:
                return m[c[:4]]
        return None

    rows = []
    for r in recs:
        rid, code = to_role(r.get("system"), r.get("occ_code")), r.get("country")
        hz = _parse_horizon(r.get("horizon"))
        if rid not in known_roles or code not in known_countries or hz is None:
            continue
        system = (r.get("system") or "").lower()
        src = next((v for k, v in _OUTLOOK_SRC.items() if k in system), slug(system or "projection"))
        opy = r.get("openings_per_year")
        shortage = r.get("shortage_flag")
        shortage = str(shortage) if shortage is not None else None
        rows.append((rid, code, hz, float(r.get("growth_pct") or 0.0),
                     float(opy) if opy else None, r.get("outlook_rating"), shortage,
                     "med", src, False))
    return rows


def _skill_adoption_rows() -> list[tuple]:
    """fact_skill_adoption rows from the adoption connectors (registries / Stack Exchange
    / arXiv / Hugging Face / Wikipedia). Each lands [{skill, period, <metric>, country}];
    we slug skill→skill_id and tag ecosystem + metric. Mostly global (country=''). This
    is reference data for ANY skill (not just dim_skill), so it isn't role-filtered.
    Graceful/empty until the connectors run."""
    feeds = [
        ("package_registries/adoption.json", "downloads"),
        ("stack_exchange/tag_volume.json", "questions"),
        ("arxiv/velocity.json", "submissions"),
        ("huggingface/velocity.json", "models"),
        ("wikipedia/pageviews.json", "pageviews"),
        ("cedefop/skill_demand.json", "vacancy_share"),
    ]
    rows: list[tuple] = []
    seen: set[tuple] = set()
    for rel, metric in feeds:
        recs = _staging_json(rel)
        if not recs:
            continue
        eco = rel.split("/")[0]
        for r in recs:
            name = r.get("skill") or r.get("esco_occ")
            if not name:
                continue
            sid = slug(str(name))
            period = str(r.get("period") or r.get("year") or "")
            if not period:
                continue
            year = int(period[:4]) if period[:4].isdigit() else max(_YEARS)
            val = (r.get("n") or r.get("downloads") or r.get("views")
                   or r.get("share_or_count") or r.get("value") or 0)
            ecosystem = r.get("ecosystem") or eco
            country = r.get("country") or ""
            key = (sid, country, period, metric, ecosystem)
            if key in seen:
                continue
            seen.add(key)
            rows.append((sid, country, year, period, metric, float(val), ecosystem, slug(eco), False))
    return rows


def _fuse_onet_trajectory(con, known_roles: set[str]) -> tuple[int, int]:
    """Fuse the O*NET role-adjacency + skill-importance staging (parsed from the
    cached zip) into bridge_role_adjacency + bridge_role_skill_importance. Both ends
    of every edge must be a known role (roles-only; no employer anything)."""
    from backend.warehouse import onet_trajectory as ot
    n_adj = n_imp = 0
    for e in ot.load_adjacency():
        if e["from_role"] in known_roles and e["to_role"] in known_roles:
            con.execute(
                "INSERT INTO bridge_role_adjacency VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (from_role, to_role, source_id, edge_type) DO NOTHING",
                (e["from_role"], e["to_role"], e["similarity"], e["edge_type"], e["source_id"]))
            n_adj += 1
    for s in ot.load_skill_importance():
        if s["role_id"] in known_roles:
            con.execute(
                "INSERT INTO bridge_role_skill_importance VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (role_id, skill_id, source_id) DO NOTHING",
                (s["role_id"], s["skill_id"], s["skill_name"], s["importance"],
                 s["level"], bool(s["essential"]), s["source_id"]))
            n_imp += 1
    return n_adj, n_imp


def _fuse_wikidata_adjacency(con, known_roles: set[str]) -> int:
    """Fuse Wikidata occupation→occupation edges → bridge_role_adjacency. Maps each
    occupation's LABEL → role via the curated matcher (no QID hardcoding) and builds a
    qid→role index from the same file, so related-occupation QIDs resolve too. Both ends
    must be known roles. Roles-only: occupation edges only (the connector already strips
    every employer/org property)."""
    recs = _staging_json("wikidata/occupations.json")
    if not recs:
        return 0
    from backend.warehouse.taxonomy import match_title_to_role
    qid_role = {}
    for r in recs:
        rid = match_title_to_role(r.get("label"))
        if rid:
            qid_role[r.get("occ_qid")] = rid
    best: dict[tuple, float] = {}
    for r in recs:
        src = qid_role.get(r.get("occ_qid"))
        if not src or src not in known_roles:
            continue
        for rel_qid in (r.get("related_occ") or []):
            dst = qid_role.get(rel_qid)
            if dst and dst != src and dst in known_roles:
                best[(src, dst)] = 0.6              # Wikidata "related" = moderate similarity
    n = 0
    for (a, b), sim in best.items():
        con.execute(
            "INSERT INTO bridge_role_adjacency VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (from_role, to_role, source_id, edge_type) DO NOTHING",
            (a, b, sim, "related", "wikidata"))
        n += 1
    return n


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

            # fact_salary_official ← official national anchors (the THIRD salary lens):
            # baselines + ILOSTAT + Germany Entgeltatlas (KldB) + US federal (OPM series)
            official_rows = (_official_salary_rows(known_roles, known_countries)
                             + _official_from_entgeltatlas(known_roles, known_countries)
                             + _official_from_usajobs(known_roles, known_countries))
            if official_rows:
                for sid in {r[8] for r in official_rows}:
                    con.execute(
                        "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (source_id) DO NOTHING",
                        (sid, sid.replace("-", " ").title(), "official", None,
                         "official statistical wage anchor", False, "2026-06-24"))
                con.executemany(
                    "INSERT INTO fact_salary_official VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (role_id, country_code, year, source_id) DO NOTHING",
                    official_rows,
                )

            # fact_salary_job (advertised) gap-fill ← gov-board POSTED salary (title→role);
            # conflict-skipped so the Adzuna headline always wins, this only fills holes.
            adv_rows = _advertised_from_title_feeds(known_roles, known_countries)
            if adv_rows:
                con.execute(
                    "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (source_id) DO NOTHING",
                    (slug("mycareersfuture"), "MyCareersFuture", "job-level", None,
                     "SG government board posted salary", False, "2026-06-25"))
                con.executemany(
                    "INSERT INTO fact_salary_job VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (role_id, country_code, experience_code, year) DO NOTHING", adv_rows)

            # bridge_role_adjacency + bridge_role_skill_importance ← O*NET (trajectory)
            con.execute(
                "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (source_id) DO NOTHING",
                (slug("O*NET"), "O*NET", "taxonomy", None,
                 "O*NET role adjacency + skill importance (cached zip)", False, "2026-06-24"))
            n_adj, n_imp = _fuse_onet_trajectory(con, known_roles)
            n_adj += _fuse_wikidata_adjacency(con, known_roles)   # roles-only occupation edges

            # fact_role_outlook ← gov_projections (the demand-OUTLOOK axis)
            outlook_rows = _role_outlook_rows(known_roles, known_countries)
            if outlook_rows:
                for sid in {r[8] for r in outlook_rows}:
                    con.execute(
                        "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (source_id) DO NOTHING",
                        (sid, sid.replace("_", " ").replace("-", " ").title(), "outlook", None,
                         "official occupation projection", False, "2026-06-25"))
                con.executemany(
                    "INSERT INTO fact_role_outlook VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (role_id, country_code, horizon_years, source_id) DO NOTHING", outlook_rows)

            # fact_skill_adoption ← registries / SE / arXiv / HF / Wikipedia (DURABILITY/emergence)
            adoption_rows = _skill_adoption_rows()
            if adoption_rows:
                for sid in {r[7] for r in adoption_rows}:
                    con.execute(
                        "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (source_id) DO NOTHING",
                        (sid, sid.replace("_", " ").title(), "adoption", None,
                         "technology adoption signal", False, "2026-06-25"))
                con.executemany(
                    "INSERT INTO fact_skill_adoption VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (skill_id, country_code, period, metric, ecosystem) DO NOTHING", adoption_rows)

            # fact_demand CORROBORATION ← skill-bearing vacancy feeds (skills→role bridge)
            feed_demand = _demand_from_skill_feeds(known_roles, known_countries)
            if feed_demand:
                for sid in {r[7] for r in feed_demand}:
                    con.execute(
                        "INSERT INTO dim_source VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (source_id) DO NOTHING",
                        (sid, sid.replace("_", " ").title(), "demand", None,
                         "skill-tagged vacancy feed", False, "2026-06-25"))
                con.executemany(
                    "INSERT INTO fact_demand VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (role_id, country_code, year) DO NOTHING", feed_demand)

            # dim_role + REAL facts ← role_derivation. A derived cluster is NOT just a
            # name: it carries demand from its own unique-posting volume + a skill bag
            # from its member titles. Salary is left ABSENT so the UI honestly shows
            # "not enough data" rather than a fabricated or borrowed number.
            derived = _read_parquet("normalized/derived_roles.parquet")
            n_derived = 0
            if derived is not None and not derived.empty:
                from backend.ml.fingerprint import extract_skills
                cc_sid = slug("Common Crawl JobPosting")
                pc_max = max(1, int(derived["posting_count"].max()))
                dyear = max(_YEARS)
                for _, r in derived.iterrows():
                    rid, country, pc = r["role_id"], r.get("country"), int(r["posting_count"])
                    con.execute(
                        "INSERT INTO dim_role VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (role_id) DO NOTHING",
                        (rid, r["label_title"], "derived", "Derived", 230,
                         f"derived from {pc} postings in {country}",
                         r.get("member_titles"), False, 900 + n_derived),
                    )
                    if country in known_countries:
                        idx = round(100 * (pc / pc_max) ** 0.5)
                        con.execute(
                            "INSERT INTO fact_demand VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT (role_id, country_code, year) DO NOTHING",
                            (rid, country, dyear, float(idx), pc, pc, "low", cc_sid, False))
                    for i, sk in enumerate(extract_skills(str(r.get("member_titles") or ""))[:12]):
                        con.execute(
                            "INSERT INTO bridge_role_skill VALUES (?, ?, ?, ?, ?) "
                            "ON CONFLICT (role_id, skill_id) DO NOTHING",
                            (rid, slug(sk), sk, "I", i))
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
            n_official = con.execute("SELECT COUNT(*) FROM fact_salary_official").fetchone()[0]
            n_adjacency = con.execute("SELECT COUNT(*) FROM bridge_role_adjacency").fetchone()[0]
            n_importance = con.execute("SELECT COUNT(*) FROM bridge_role_skill_importance").fetchone()[0]
            n_derived_roles = con.execute(
                "SELECT COUNT(*) FROM dim_role WHERE family_id = 'derived'").fetchone()[0]
            n_outlook = con.execute("SELECT COUNT(*) FROM fact_role_outlook").fetchone()[0]
            n_adoption = con.execute("SELECT COUNT(*) FROM fact_skill_adoption").fetchone()[0]
        finally:
            con.close()
        log.info("new-axis fuse: fact_role_outlook=%d, fact_skill_adoption=%d", n_outlook, n_adoption)
        log.info("FUSED (%s base): fact_salary_person=%d (SO=%d, H1B=%d), "
                 "fact_demand=%d (CC/GH overlay=%d, rest Adzuna), "
                 "fact_interest=%d (GTrends overlay=%d), fact_salary_job=%d (Adzuna), "
                 "fact_salary_official=%d (3rd lens), adjacency=%d, skill_importance=%d, derived_roles=%d",
                 base, n_person, n_so, n_person - n_so, n_demand, n_demand_cc,
                 n_interest, n_interest_gt, n_job, n_official, n_adjacency, n_importance, n_derived_roles)
        log.info("council/fusion precedence: salary_job←Adzuna(auth); "
                 "salary_person←SO(auth)+H1B(US gap-fill); salary_official←baselines(3rd lens, separate); "
                 "demand←CommonCrawl(primary)+GHArchive; interest←GoogleTrends; "
                 "trajectory/skill-importance←O*NET; dim_role←curated+derived(now with real facts); dim_ppp←WorldBank")
