"""DuckDB / Parquet **star schema** — the analytical spine (brief §5).

Grain: the finest reliable grain is **Role × Country × Experience × Time**; the
default display collapses up to Role × Country. Salary is **job-level by default**
and **person-level lives in a separate fact** (different population, never blended).
Currency is stored **natively**; cross-currency comparison is via `dim_ppp` only —
there is **no live FX**. Provenance (source + sample size + freshness + confidence)
is first-class and carried at the fact-row grain so the confidence badge and the
"where did this come from" lookup are always answerable. `is_seed` distinguishes
representative seed rows from real ingested data (retired by Phase 6).
"""
from __future__ import annotations

import duckdb

# ---- dimensions ----
DIMENSIONS = {
    "dim_country": """
        CREATE TABLE IF NOT EXISTS dim_country (
            code            VARCHAR PRIMARY KEY,   -- IN, US, GB, ...
            name            VARCHAR NOT NULL,
            currency_symbol VARCHAR NOT NULL,      -- ₹ £ $ €
            currency_code   VARCHAR NOT NULL,      -- INR GBP USD EUR
            nat_factor      DOUBLE,                -- salary scale vs US (seed calibration)
            ppp_rate        DOUBLE,                -- base PPP conversion (also in dim_ppp by year)
            transparency    DOUBLE,                -- base salary-disclosure rate
            flag_c1         VARCHAR,
            flag_c2         VARCHAR,
            ord             INTEGER                -- canonical display order
        )""",
    "dim_role": """
        CREATE TABLE IF NOT EXISTS dim_role (
            role_id        VARCHAR PRIMARY KEY,    -- derived cluster id / seed slug
            name           VARCHAR NOT NULL,
            family_id      VARCHAR,
            family_name    VARCHAR,
            family_hue     INTEGER,
            blurb          VARCHAR,
            cluster_lineage VARCHAR,               -- JSON: titles/centroids feeding the cluster
            is_seed        BOOLEAN DEFAULT FALSE,
            ord            INTEGER                 -- catalog display order
        )""",
    "dim_experience": """
        CREATE TABLE IF NOT EXISTS dim_experience (
            code      VARCHAR PRIMARY KEY,         -- pooled, 0-2, 3-5, 6-9, 10+
            label     VARCHAR NOT NULL,
            min_years INTEGER,
            max_years INTEGER
        )""",
    "dim_skill": """
        CREATE TABLE IF NOT EXISTS dim_skill (
            skill_id        VARCHAR PRIMARY KEY,   -- canonical Lightcast/ESCO/O*NET id
            name            VARCHAR NOT NULL,
            durability      INTEGER,               -- 0-100 long-term durability signal
            trend           VARCHAR,               -- rising | stable | fading
            taxonomy_source VARCHAR                -- lightcast | esco | onet | seed
        )""",
    "dim_time": """
        CREATE TABLE IF NOT EXISTS dim_time (
            year        INTEGER PRIMARY KEY,
            is_forecast BOOLEAN DEFAULT FALSE
        )""",
    # NOTE: strata is ROLES-only. There is deliberately NO company/employer dimension
    # — companies are never a product axis. Employer survives only as an in-memory
    # dedup key inside the pipeline (entity_resolution / fingerprint), never an entity.
    "dim_source": """
        CREATE TABLE IF NOT EXISTS dim_source (
            source_id    VARCHAR PRIMARY KEY,
            source_name  VARCHAR NOT NULL,
            default_kind VARCHAR,                  -- job-level | person-level | demand | interest | taxonomy | ppp
            url          VARCHAR,
            notes        VARCHAR,
            is_seed      BOOLEAN DEFAULT FALSE,
            retrieved_at VARCHAR
        )""",
    "dim_ppp": """
        CREATE TABLE IF NOT EXISTS dim_ppp (
            country_code VARCHAR,
            year         INTEGER,
            ppp_factor   DOUBLE,                   -- local currency units per international $
            col_index    DOUBLE,                   -- cost-of-living index (Numbeo), optional
            source       VARCHAR,
            PRIMARY KEY (country_code, year)
        )""",
}

# ---- facts (provenance carried at row grain) ----
FACTS = {
    # job-level salary: single-point median over postings/positions, native currency
    "fact_salary_job": """
        CREATE TABLE IF NOT EXISTS fact_salary_job (
            role_id         VARCHAR,
            country_code    VARCHAR,
            experience_code VARCHAR DEFAULT 'pooled',
            year            INTEGER,
            median          DOUBLE,
            currency_code   VARCHAR,
            sample_size     INTEGER,
            confidence      VARCHAR,               -- high | med | low
            freshness       VARCHAR,
            kind            VARCHAR DEFAULT 'job-level',
            transparency    DOUBLE,                -- salary-disclosure rate (Pay Transparency Index)
            source_id       VARCHAR,
            is_seed         BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, experience_code, year)
        )""",
    # person-level salary: SO survey / H-1B-PERM — DIFFERENT population, never blended
    "fact_salary_person": """
        CREATE TABLE IF NOT EXISTS fact_salary_person (
            role_id         VARCHAR,
            country_code    VARCHAR,
            experience_code VARCHAR DEFAULT 'pooled',
            year            INTEGER,
            median          DOUBLE,
            currency_code   VARCHAR,
            sample_size     INTEGER,
            confidence      VARCHAR,
            freshness       VARCHAR,
            kind            VARCHAR DEFAULT 'person-level',
            source_id       VARCHAR,
            is_seed         BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, experience_code, year)
        )""",
    "fact_demand": """
        CREATE TABLE IF NOT EXISTS fact_demand (
            role_id      VARCHAR,
            country_code VARCHAR,
            year         INTEGER,
            demand_index DOUBLE,                   -- normalized 0-100
            postings_count BIGINT,
            sample_size  INTEGER,
            confidence   VARCHAR,
            source_id    VARCHAR,
            is_seed      BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, year)
        )""",
    "fact_interest": """
        CREATE TABLE IF NOT EXISTS fact_interest (
            role_id        VARCHAR,
            country_code   VARCHAR,
            year           INTEGER,
            interest_index DOUBLE,                 -- learner/search interest 0-100 (NOT "competition")
            source_id      VARCHAR,
            is_seed        BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, year)
        )""",
    # honest forecast: projected value + confidence band, back-test stored separately
    "fact_demand_forecast": """
        CREATE TABLE IF NOT EXISTS fact_demand_forecast (
            role_id      VARCHAR,
            country_code VARCHAR,
            year         INTEGER,
            value        DOUBLE,
            lo           DOUBLE,                   -- confidence band low
            hi           DOUBLE,                   -- confidence band high
            source_id    VARCHAR,
            is_seed      BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, year)
        )""",
    # Job Score: components persisted so the frontend can show them clickable
    "fact_job_score": """
        CREATE TABLE IF NOT EXISTS fact_job_score (
            role_id      VARCHAR,
            country_code VARCHAR,
            year         INTEGER,
            total        DOUBLE,
            demand_score DOUBLE,
            pay_score    DOUBLE,
            opp_score    DOUBLE,
            rank         INTEGER,
            pctile       INTEGER,
            source_id    VARCHAR,
            is_seed      BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, year)
        )""",
    # back-test record: what the model predicted for a held-out period vs actual
    "fact_forecast_backtest": """
        CREATE TABLE IF NOT EXISTS fact_forecast_backtest (
            role_id      VARCHAR,
            country_code VARCHAR,
            year         INTEGER,
            predicted    DOUBLE,
            actual       DOUBLE,
            abs_error    DOUBLE,
            source_id    VARCHAR,
            is_seed      BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, year)
        )""",
    # OFFICIAL salary lens — national statistical / ILO earnings by occupation. The
    # THIRD salary lens beside fact_salary_job (advertised) + fact_salary_person
    # (realized). Three lenses, never blended — shown side-by-side with provenance.
    "fact_salary_official": """
        CREATE TABLE IF NOT EXISTS fact_salary_official (
            role_id         VARCHAR,
            country_code    VARCHAR,
            year            INTEGER,
            median          DOUBLE,
            currency_code   VARCHAR,
            sample_size     INTEGER,
            confidence      VARCHAR,
            kind            VARCHAR DEFAULT 'official',
            source_id       VARCHAR,                 -- ilostat | entgeltatlas | bls_oews | ons | ...
            is_seed         BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, year, source_id)
        )""",
    # forward DEMAND-OUTLOOK — official occupation growth projections + shortage flags
    "fact_role_outlook": """
        CREATE TABLE IF NOT EXISTS fact_role_outlook (
            role_id           VARCHAR,
            country_code      VARCHAR,
            horizon_years     INTEGER,              -- 3 | 10 (projection horizon)
            growth_pct        DOUBLE,               -- projected % change over horizon
            openings_per_year DOUBLE,
            outlook_rating    VARCHAR,              -- e.g. Canada 1-3 star, or 'good'/'limited'
            shortage_flag     VARCHAR,              -- shortage | balance | surplus (AU/etc.)
            confidence        VARCHAR,
            source_id         VARCHAR,              -- bls_ep | ca_cops | jsa | ...
            is_seed           BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (role_id, country_code, horizon_years, source_id)
        )""",
    # skill ADOPTION / EMERGENCE / DURABILITY — registry downloads, SE tag volume,
    # arXiv velocity, pageviews. Mostly country-invariant (a global skill attribute);
    # country_code '' = global. Modulates, never overrides, country-specific signals.
    "fact_skill_adoption": """
        CREATE TABLE IF NOT EXISTS fact_skill_adoption (
            skill_id     VARCHAR,
            country_code VARCHAR DEFAULT '',        -- '' = global (most adoption signals)
            year         INTEGER,
            period       VARCHAR,                   -- 'YYYY' | 'YYYY-MM'
            metric       VARCHAR,                   -- downloads | questions | submissions | pageviews | models
            value        DOUBLE,
            ecosystem    VARCHAR,                   -- pypi | npm | crates | stackexchange | arxiv | hf | wikipedia
            source_id    VARCHAR,
            is_seed      BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (skill_id, country_code, period, metric, ecosystem)
        )""",
}

# ---- bridges (role attributes; skills/ladder are role-level, not per country) ----
BRIDGES = {
    "bridge_role_skill": """
        CREATE TABLE IF NOT EXISTS bridge_role_skill (
            role_id    VARCHAR,
            skill_id   VARCHAR,
            skill_name VARCHAR,
            level      VARCHAR,                    -- A | I | B (Advanced/Intermediate/Beginner)
            ord        INTEGER,
            PRIMARY KEY (role_id, skill_id)
        )""",
    "bridge_role_ladder": """
        CREATE TABLE IF NOT EXISTS bridge_role_ladder (
            role_id VARCHAR,
            ord     INTEGER,
            title   VARCHAR,
            mult    DOUBLE,                        -- pay multiple vs the role's own median
            PRIMARY KEY (role_id, ord)
        )""",
    # role TRAJECTORY — directed role→role adjacency ("where does this role lead?").
    # ROLES-ONLY: edges are occupation→occupation (O*NET related/career-changers,
    # ESCO siblings, Wikidata occupation edges) — never employer career graphs.
    "bridge_role_adjacency": """
        CREATE TABLE IF NOT EXISTS bridge_role_adjacency (
            from_role  VARCHAR,
            to_role    VARCHAR,
            similarity DOUBLE,                      -- 0-1 relatedness / move frequency
            edge_type  VARCHAR,                     -- similar | career_change | sibling | broader | narrower
            source_id  VARCHAR,                     -- onet | esco | wikidata
            PRIMARY KEY (from_role, to_role, source_id, edge_type)
        )""",
    # skill IMPORTANCE per role — core vs peripheral weighting (O*NET / ESCO), so a
    # role's skill bag is graded, not flat, and adjacency edges are explainable.
    "bridge_role_skill_importance": """
        CREATE TABLE IF NOT EXISTS bridge_role_skill_importance (
            role_id    VARCHAR,
            skill_id   VARCHAR,
            skill_name VARCHAR,
            importance DOUBLE,                      -- 0-100 importance
            level      DOUBLE,                      -- 0-100 required level
            essential  BOOLEAN,                     -- ESCO essential vs optional
            source_id  VARCHAR,                     -- onet | esco
            PRIMARY KEY (role_id, skill_id, source_id)
        )""",
}

ALL_TABLES = {**DIMENSIONS, **FACTS, **BRIDGES}
TABLE_NAMES = list(ALL_TABLES.keys())


def create_warehouse_schema(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in ALL_TABLES.values():
        con.execute(ddl)


def drop_warehouse_schema(con: duckdb.DuckDBPyConnection) -> None:
    for name in TABLE_NAMES:
        con.execute(f"DROP TABLE IF EXISTS {name}")


def existing_tables(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]
