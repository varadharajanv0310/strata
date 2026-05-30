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
    "dim_company": """
        CREATE TABLE IF NOT EXISTS dim_company (
            company_id VARCHAR PRIMARY KEY,
            name       VARCHAR NOT NULL,
            size       VARCHAR,
            industry   VARCHAR,
            source     VARCHAR                     -- wikidata | github | seed
        )""",
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
