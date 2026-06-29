"""DELIBERATELY-THIN coverage / honesty tests (the partial-coverage paths the
full seed masks).

The seed dataset is dense and uniform: every role×country has a salary series, a
job-score, a sample size well above every floor, and no derived/demand-only roles.
That density hides the cases the product is *supposed* to handle honestly. This
module builds a tiny warehouse with the gaps deliberately punched in, materializes
it, and asserts the serving layer stays honest:

  * **A1 / A7** — a role×country with a salary fact but NO job-score must not crash
    ``materialize_from_warehouse``. The current contract KEEPS the cell with a
    zeroed score (recomputed post-fusion) rather than KeyError'ing or dropping it.
  * **UI/API contract** — a 1-point salary series must serialize cleanly through
    ``services`` (no ``series[-1]`` / forecast assumptions that need ≥2 points).
  * **B3** — a realized salary lens with ``sample_size`` below ``MIN_SAMPLE_REALIZED``
    must be SUPPRESSED → the lens is null ("not enough data"), never shown.
  * **Official anchor with sample_size=0** — an aggregate statistic has no
    per-respondent sample; it must be KEPT (and flagged official), never suppressed.
  * **A4** — a DERIVED / demand-only role with NO salary fact must still appear:
      - a role with a demand signal but no salary surfaces as a cell with a NULL
        headline median (``"not enough data"``), and
      - a role with literally no facts still appears in the catalogue (empty
        ``countries``) — a real cluster is never silently dropped for lacking pay.

ISOLATION: ``conftest`` redirects ``DUCKDB_PATH`` / ``DATABASE_URL`` to a throwaway
temp DuckDB + SQLite *before* any backend import, so this builds into the temp
warehouse and NEVER touches the persistent warehouse or the live ``app.db``. A
module-scoped fixture rebuilds the seed warehouse + marts afterwards so the rest of
the suite sees the dense seed it asserts against (same pattern as ``test_publish``).

Heavy deps (vLLM / torch / the 7B model) are never imported here — this exercises
the pure warehouse → mart → services path only.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.core.db import duckdb_connect, session_scope
from backend.marts import models as M

# ---------------------------------------------------------------------------
# A tiny, hand-built dataset (NOT the seed). Two countries, a handful of roles,
# each crafted to land on a specific honesty path once we punch holes below.
# ---------------------------------------------------------------------------
YEARS = [2024, 2025]
FYEARS = [2026]

# country codes used: US (dense control), GB (thin cases)
COUNTRIES = [
    {"code": "US", "name": "United States", "cur": "$", "curCode": "USD",
     "natFactor": 1.0, "pppRate": 1.0, "transparency": 0.57, "c1": "#3C3B6E", "c2": "#B22234"},
    {"code": "GB", "name": "United Kingdom", "cur": "£", "curCode": "GBP",
     "natFactor": 0.5, "pppRate": 0.69, "transparency": 0.49, "c1": "#012169", "c2": "#C8102E"},
]
FAMILIES = [{"id": "eng", "name": "Engineering", "hue": 230}]
SOURCES = ["Aggregated public postings"]


def _country_cell(median: int) -> dict:
    """A minimally-complete role×country cell (single-point series → exercises the
    1-point UI/API contract; everything else present so the cell materializes)."""
    return {
        "median": median,
        "series": [{"year": YEARS[-1], "value": median}],          # 1-POINT series (contract)
        "demandSeries": [{"year": YEARS[-1], "value": 50}],
        "forecast": [{"year": FYEARS[0], "value": 51, "lo": 45, "hi": 57}],
        "demand": 50, "interest": 40,
        "score": {"total": 5.0, "demand": 5.0, "pay": 5.0, "opp": 5.0, "rank": 1, "pctile": 50},
        "sample": 800, "conf": "med", "kind": "job-level",
        "source": SOURCES[0], "freshness": "1 week", "transparency": 0.5,
    }


def _build_thin_dataset() -> dict:
    """Roles:
      * ``control``  — full cell in both countries (the dense baseline).
      * ``no-score`` — salary in GB but its job-score row is removed below (A1/A7).
      * ``thin-real``— gets a below-floor realized lens + an official sample=0 anchor.
    The derived roles (A4) are inserted straight into dim_role afterwards (no dataset
    path produces a role with zero salary facts — which is exactly the point).
    """
    def role(rid, name):
        return {
            "id": rid, "name": name, "family": FAMILIES[0],
            "blurb": f"{name} — thin-coverage fixture role.",
            "skills": [{"name": "Python", "level": "A", "dura": 89, "trend": "rising"}],
            "ladder": [["Junior", 0.7], ["Senior", 1.0]],
            "countries": {c["code"]: _country_cell(120000 if c["code"] == "US" else 90000)
                          for c in COUNTRIES},
        }

    roles = [role("control", "Control Role"),
             role("no-score", "No Score Role"),
             role("thin-real", "Thin Realized Role")]
    return {
        "countries": COUNTRIES, "families": FAMILIES, "roles": roles,
        "years": YEARS, "fyears": FYEARS, "marketPulse": {},
        "resume_sample": {}, "resume_b": {}, "sources": SOURCES, "is_seed": False,
    }


def _punch_holes_and_materialize() -> None:
    """Load the thin dataset, then surgically punch the honesty holes directly into
    the temp DuckDB warehouse and re-materialize."""
    from backend.warehouse.build import build_warehouse_from_dataset
    from backend.marts.materialize import materialize_from_warehouse

    build_warehouse_from_dataset(_build_thin_dataset(), is_seed=False)

    con = duckdb_connect()
    try:
        con.execute("BEGIN TRANSACTION")

        # A1/A7 — remove the job-score for no-score/GB so it has salary but NO score.
        con.execute(
            "DELETE FROM fact_job_score WHERE role_id='no-score' AND country_code='GB'")

        # B3 — a REALIZED (person-level) lens for thin-real/GB with sample below the
        # floor. Must be suppressed → null lens.
        con.execute(
            "INSERT INTO fact_salary_person VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("thin-real", "GB", "pooled", YEARS[-1], 88000.0, "GBP",
             3, "low", "survey", "person-level", "aggregated-public-postings", False))

        # OFFICIAL anchor with sample_size=0 for thin-real/GB. Aggregate statistic →
        # must be KEPT (and flagged official), never suppressed for low n.
        con.execute(
            "INSERT INTO fact_salary_official VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("thin-real", "GB", YEARS[-1], 95000.0, "GBP", 0, "high", "official",
             "aggregated-public-postings", False))

        # A4 (cell) — a DERIVED role with a DEMAND signal but NO salary fact at all.
        # It must surface as a priced-None cell ("not enough data" headline median).
        con.execute(
            "INSERT INTO dim_role VALUES (?,?,?,?,?,?,?,?,?)",
            ("derived-demand", "Derived Demand Cluster", "derived", "Derived", 230,
             "derived from postings; demand only, no salary", None, False, 900))
        con.execute(
            "INSERT INTO fact_demand VALUES (?,?,?,?,?,?,?,?,?)",
            ("derived-demand", "GB", YEARS[-1], 42.0, 137, 137, "low",
             "aggregated-public-postings", False))

        # A4 (catalogue) — a DERIVED role with ZERO facts of any kind. It must still
        # appear in the role catalogue with an empty countries map, never vanish.
        con.execute(
            "INSERT INTO dim_role VALUES (?,?,?,?,?,?,?,?,?)",
            ("derived-bare", "Derived Bare Cluster", "derived", "Derived", 230,
             "derived from postings; no facts yet", None, False, 901))

        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    materialize_from_warehouse()


@pytest.fixture(scope="module", autouse=True)
def thin_warehouse():
    """Build the thin warehouse for this module, then restore the seed afterwards so
    the rest of the suite sees the dense seed it asserts against."""
    _punch_holes_and_materialize()
    yield
    # restore the seed warehouse + marts (other modules depend on it)
    from backend.warehouse.build import build_warehouse_from_dataset
    from backend.warehouse.seed import build_seed_dataset
    from backend.marts.materialize import materialize_from_warehouse
    build_warehouse_from_dataset(build_seed_dataset(), is_seed=True)
    materialize_from_warehouse()


# ---------------------------------------------------------------------------
# A1 / A7 — salary but no job-score must not crash materialize; cell is KEPT with a
# zeroed (recompute-pending) score rather than dropped or KeyError'd.
# ---------------------------------------------------------------------------
def test_a1_salary_without_jobscore_does_not_crash_materialize():
    """Reaching this body proves ``materialize_from_warehouse`` (run in the fixture)
    did not raise on the score-less cell. Honesty contract: the cell is KEPT with a
    score that defaults to 0 (recomputed post-fusion), never KeyError'd away."""
    from backend.app import services

    with session_scope() as db:
        role = services.get_role(db, "no-score")
        assert role is not None, "no-score role dropped entirely"
        assert "GB" in role["countries"], "score-less cell must be kept (A7), not dropped"
        cell = role["countries"]["GB"]
        # salary survives; the missing score is zeroed honestly, not fabricated
        assert cell["median"] == 90000
        assert cell["score"]["total"] == 0
        assert set(cell["score"]) >= {"total", "demand", "pay", "opp", "rank", "pctile"}
        # the complete US cell is unaffected
        assert role["countries"]["US"]["score"]["total"] != 0


# ---------------------------------------------------------------------------
# 1-point series must serialize cleanly through the serving layer (UI contract).
# ---------------------------------------------------------------------------
def test_one_point_series_serializes():
    from backend.app import services

    with session_scope() as db:
        role = services.get_role(db, "control")
        assert role is not None
        cell = role["countries"]["US"]
        assert isinstance(cell["series"], list) and len(cell["series"]) == 1
        assert cell["series"][0] == {"year": YEARS[-1], "value": 120000}
        assert cell["median"] == 120000
        assert set(cell["score"]) >= {"total", "demand", "pay", "opp", "rank", "pctile"}


def test_one_point_series_in_assemble_dataset():
    """The full bundle (what the frontend hydrates from) must also assemble without a
    KeyError when cells carry only one series point / null medians / derived roles."""
    from backend.app import services

    with session_scope() as db:
        ds = services.assemble_dataset(db)
    ids = {r["id"] for r in ds["roles"]}
    assert {"control", "no-score", "thin-real", "derived-demand", "derived-bare"} <= ids


# ---------------------------------------------------------------------------
# B3 — below-floor realized lens suppressed; official sample=0 anchor kept.
# ---------------------------------------------------------------------------
def test_b3_realized_lens_suppressed_below_floor():
    from backend.app import services
    from backend.marts.materialize import MIN_SAMPLE_REALIZED

    assert MIN_SAMPLE_REALIZED > 3, "fixture assumes the realized floor exceeds n=3"
    with session_scope() as db:
        role = services.get_role(db, "thin-real")
        lenses = role["countries"]["GB"]["salaryLenses"]
    # realized lens had n=3 (< floor) → suppressed to null ("not enough data")
    assert lenses["realized"] is None, "below-floor realized lens must be suppressed"


def test_official_anchor_sample_zero_kept():
    from backend.app import services

    with session_scope() as db:
        role = services.get_role(db, "thin-real")
        lenses = role["countries"]["GB"]["salaryLenses"]
    # official anchor (aggregate, sample_size=0) must be KEPT, not suppressed
    assert lenses["official"] is not None, "official anchor wrongly suppressed for n=0"
    assert lenses["official"]["median"] == 95000
    assert lenses["official"]["sample"] == 0          # honestly zero, not hidden


# ---------------------------------------------------------------------------
# A4 — a derived role with no salary fact must still appear.
# ---------------------------------------------------------------------------
def test_a4_demand_only_role_has_null_median_cell():
    """A derived role with a demand signal but no salary anywhere must surface as a
    cell whose headline median is null ("not enough data") — present, not priced."""
    from backend.app import services

    with session_scope() as db:
        role = services.get_role(db, "derived-demand")
        assert role is not None, "demand-only derived role dropped (A4 regression)"
        assert "GB" in role["countries"], "demand-only cell should surface"
        cell = role["countries"]["GB"]
        assert cell["median"] is None, "no-salary cell must report a null headline median"
        assert cell["demand"] == 42                    # the real demand signal is present
        assert cell["postings"] == 137                 # honest unique-posting count behind it (A9)
        # all three salary lenses are honestly empty
        assert all(cell["salaryLenses"][k] is None for k in ("advertised", "realized", "official"))
        # lineage absent here (cluster_lineage=None in the fixture) → null, never fabricated (A10)
        assert role.get("lineage") is None


def test_a4_bare_derived_role_appears_with_empty_countries():
    from backend.app import services

    with session_scope() as db:
        role = services.get_role(db, "derived-bare")
        assert role is not None, "fact-less derived role dropped for lacking salary (A4)"
        assert role["name"] == "Derived Bare Cluster"
        assert role["countries"] == {}                 # honest: present but no priced cells

    # present in the role catalogue (mart_role), not merely fetchable by id
    with session_scope() as db:
        all_ids = {r.id for r in db.scalars(select(M.MartRole))}
    assert {"derived-demand", "derived-bare"} <= all_ids


def test_a4_derived_roles_in_assemble_dataset_no_keyerror():
    """assemble_dataset joins roles → rc cells; roles with null-median / zero cells
    must not KeyError and must surface with the right ``countries`` shape."""
    from backend.app import services

    with session_scope() as db:
        ds = services.assemble_dataset(db)
    by_id = {r["id"]: r for r in ds["roles"]}
    assert by_id["derived-bare"]["countries"] == {}
    assert by_id["derived-demand"]["countries"]["GB"]["median"] is None


# ---------------------------------------------------------------------------
# Provenance stays honest on the thin cells (kept cell → lineage; absent → 404).
# ---------------------------------------------------------------------------
def test_provenance_present_for_kept_scoreless_cell():
    """no-score/GB is KEPT (A7), so it has a mart row and answers a provenance
    lookup with its real lineage — not a fabricated record, and not a 404."""
    from backend.app import services

    with session_scope() as db:
        prov = services.provenance(db, "no-score", "GB")
    assert prov is not None
    assert prov["role_id"] == "no-score" and prov["country"] == "GB"
    assert "transform_version" in prov


def test_provenance_absent_for_unknown_cell():
    """A truly absent cell (bare derived role, no country fact) has no mart row → the
    provenance lookup is honestly None (the API returns 404), never fabricated."""
    from backend.app import services

    with session_scope() as db:
        assert services.provenance(db, "derived-bare", "US") is None
