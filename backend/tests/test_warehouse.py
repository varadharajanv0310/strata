"""Warehouse aggregation correctness + conventions (brief §5/§12)."""
from __future__ import annotations

from backend.core.db import duckdb_connect


def test_seed_warehouse_grain_and_provenance():
    con = duckdb_connect(read_only=True)
    # job-level salary grain: 16 roles × 7 countries × 9 years
    n_job = con.execute("SELECT count(*) FROM fact_salary_job").fetchone()[0]
    assert n_job == 16 * 7 * 9

    # headline median == the latest year's job-level value, native currency
    m = con.execute(
        "SELECT median FROM fact_salary_job WHERE role_id='ml-eng' AND country_code='IN' AND year=2025"
    ).fetchone()[0]
    assert int(m) == 1900000

    # provenance present on every fact row (drives the confidence badge)
    missing = con.execute(
        "SELECT count(*) FROM fact_salary_job WHERE source_id IS NULL OR confidence IS NULL OR sample_size IS NULL"
    ).fetchone()[0]
    assert missing == 0

    # job-level and person-level live in separate facts (never blended)
    assert "fact_salary_person" in [
        r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
    ]

    # PPP present per country/year (no live FX); 7 countries
    n_ppp_countries = con.execute("SELECT count(DISTINCT country_code) FROM dim_ppp").fetchone()[0]
    assert n_ppp_countries == 7
    con.close()
