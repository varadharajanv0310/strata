"""Tests for the generated catalog spine (the model-authored catalog IS the product).

The catalog is generated from model knowledge — hundreds of roles × 7 countries, every
cell filled and stamped ``generated``. These tests use a small in-memory catalog (no disk,
no run) to prove the expansion produces the full pipeline ``ds`` shape, per-country salary
medians in local currency, and the ``generated`` provenance stamp — and that measured data
UPGRADES a generated cell (measured stamp wins) while uncovered cells stay generated.
"""
from __future__ import annotations

from backend.warehouse.generated_build import (
    GENERATED_KIND, GENERATED_SOURCE, build_generated_dataset,
)

_CATALOG = {
    "roles": [
        {"id": "swe", "name": "Software Engineer", "family": "eng",
         "blurb": "Builds software across the stack.",
         "skills": [{"name": "Python", "importance": "A"}, {"name": "SQL", "importance": "I"},
                    {"name": "Kafka", "importance": "B"}],
         "ladder": [{"title": "SWE I", "mult": 0.6}, {"title": "Senior SWE", "mult": 1.0},
                    {"title": "Staff SWE", "mult": 1.4}],
         "demand": 88, "demandDelta": 12, "interest": 90, "growth": 0.3,
         "salary": {"IN": 1800000, "US": 150000, "GB": 70000, "CA": 120000,
                    "AU": 135000, "SG": 110000, "DE": 75000}},
        {"id": "blockchain-eng", "name": "Blockchain Engineer", "family": "eng",
         "blurb": "Builds decentralized systems.",
         "skills": [{"name": "Solidity", "importance": "A"}, {"name": "Rust", "importance": "I"}],
         "ladder": [{"title": "Blockchain Eng", "mult": 1.0}],
         "demand": 70, "demandDelta": 20, "interest": 65, "growth": 0.4,
         "salary": {"IN": 2200000, "US": 175000, "GB": 85000, "CA": 140000,
                    "AU": 150000, "SG": 125000, "DE": 90000}},
    ],
    "skillVocab": {"Solidity": [58, "rising"], "Kafka": [67, "stable"]},
}


_STAGED = {
    "roles": [
        {"id": "swe", "name": "Software Engineer", "family": "eng",
         "blurb": "Builds software.", "skills": [{"name": "Python", "importance": "A"}],
         "demand": 88, "demandDelta": 12, "interest": 90, "growth": 0.3,
         "stages": [
             {"title": "Software Engineer I", "yoe_min": 0, "yoe_max": 2,
              "salary": {"IN": 800000, "US": 115000, "GB": 48000, "CA": 85000,
                         "AU": 85000, "SG": 65000, "DE": 55000}},
             {"title": "Senior Software Engineer", "yoe_min": 5, "yoe_max": 9,
              "salary": {"IN": 3200000, "US": 200000, "GB": 95000, "CA": 155000,
                         "AU": 165000, "SG": 150000, "DE": 95000}},
             {"title": "Staff Engineer", "yoe_min": 9, "yoe_max": 40,
              "salary": {"IN": 5500000, "US": 270000, "GB": 130000, "CA": 210000,
                         "AU": 220000, "SG": 210000, "DE": 120000}},
         ],
         "leads_to": ["Staff Engineer", "Engineering Manager", "Cloud Architect"]},
    ],
}


def test_headline_is_the_early_career_stage():
    ds = build_generated_dataset(_STAGED)
    swe = ds["roles"][0]
    # headline median == the FIRST (entry) stage salary, NOT a mid/senior figure
    assert swe["countries"]["IN"]["median"] == 800000
    assert swe["countries"]["US"]["median"] == 115000
    # the entry headline is well below the senior stage (no mid-career blend in the headline)
    assert swe["countries"]["IN"]["median"] < 3_000_000
    # ladder derived from stages, Senior anchored to mult 1.0
    titles = [t for t, _m in swe["ladder"]]
    assert "Software Engineer I" in titles and "Senior Software Engineer" in titles


def test_progression_fills_experience_substrate():
    from backend.core.db import duckdb_connect
    from backend.warehouse.generated_build import fuse_generated_progression

    con = duckdb_connect()
    try:
        counts = fuse_generated_progression(con, _STAGED)
        assert counts["rungs"] >= 3 and counts["yoe_obs"] >= 3 * 7 and counts["curve"] >= 3 * 7
        # a generated pay-vs-experience curve exists per country, monotonic in salary
        curve = con.execute(
            "SELECT yoe, fit_median FROM fact_salary_curve "
            "WHERE kind='generated' AND role_id='swe' AND country_code='IN' ORDER BY yoe"
        ).fetchall()
        assert len(curve) >= 3
        meds = [c[1] for c in curve]
        assert meds == sorted(meds)                       # early < senior < staff
        # trajectory edges (where it leads) were written
        assert counts["edges"] >= 1
    finally:
        # clean up generated rows so later tests see the seed substrate unchanged
        for t, w in (("fact_salary_yoe_obs", "source_id='generated-model-knowledge'"),
                     ("fact_salary_curve", "kind='generated'"),
                     ("dim_ladder_rung", "source_id='generated-model-knowledge'"),
                     ("bridge_role_adjacency", "source_id='generated-model-knowledge'")):
            try:
                con.execute(f"DELETE FROM {t} WHERE {w}")
            except Exception:
                pass
        con.close()


def test_expands_to_full_pipeline_shape():
    ds = build_generated_dataset(_CATALOG)
    assert {"countries", "families", "roles", "years", "fyears", "marketPulse", "sources"} <= ds.keys()
    assert ds["is_seed"] is False and ds.get("generated") is True
    assert len(ds["roles"]) == 2
    assert len(ds["countries"]) == 7


def test_per_country_salary_is_local_and_generated_stamped():
    ds = build_generated_dataset(_CATALOG)
    swe = next(r for r in ds["roles"] if r["id"] == "swe")
    # India median is the generated LOCAL (INR) figure (rounded), not a US×factor value
    assert swe["countries"]["IN"]["median"] >= 1_000_000
    assert swe["countries"]["US"]["median"] < swe["countries"]["IN"]["median"]  # INR >> USD absolute
    for code in ("IN", "US", "GB", "CA", "AU", "SG", "DE"):
        cell = swe["countries"][code]
        assert cell["kind"] == GENERATED_KIND
        assert cell["source"] == GENERATED_SOURCE
        assert cell["median"] > 0                       # every cell filled, never blank


def test_every_role_every_country_filled():
    ds = build_generated_dataset(_CATALOG)
    for role in ds["roles"]:
        assert len(role["countries"]) == 7
        for cell in role["countries"].values():
            assert cell["median"] > 0 and cell["series"] and cell["demandSeries"]


def test_generated_source_registered():
    ds = build_generated_dataset(_CATALOG)
    assert GENERATED_SOURCE in ds["sources"]


def test_skill_durability_from_vocab_with_graceful_default():
    ds = build_generated_dataset(_CATALOG)
    swe = next(r for r in ds["roles"] if r["id"] == "swe")
    skills = {s["name"]: s for s in swe["skills"]}
    assert skills["Kafka"]["dura"] == 67              # from generated vocab
    assert skills["Python"]["dura"] > 0               # from curated SK
    # a skill absent from both vocabs still gets a graceful non-zero default, never crashes
    bc = next(r for r in ds["roles"] if r["id"] == "blockchain-eng")
    assert all(s["dura"] > 0 for s in bc["skills"])


def test_headline_stays_early_career_measured_upgrades_demand(monkeypatch):
    """build_real_dataset bases on the generated spine. Adzuna's pooled mean is a mid-career
    BLEND, so it must NOT overwrite the early-career headline — the headline stays generated;
    Adzuna instead upgrades DEMAND. Cells with no Adzuna stay fully generated."""
    from backend.warehouse import real_build

    monkeypatch.setattr(real_build, "has_generated_catalog", lambda: True)
    monkeypatch.setattr(real_build, "build_generated_dataset", lambda: build_generated_dataset(_CATALOG))
    monkeypatch.setattr(real_build, "load_cache",
                        lambda: {("US", "swe"): {"mean": 160000, "count": 2000,
                                                 "n_results": 100, "n_disclosed": 60}})
    monkeypatch.setattr(real_build, "latest_ppp", lambda: {})
    gen = build_generated_dataset(_CATALOG)
    gen_swe_us_median = next(r for r in gen["roles"] if r["id"] == "swe")["countries"]["US"]["median"]
    ds = real_build.build_real_dataset()
    swe = next(r for r in ds["roles"] if r["id"] == "swe")
    bc = next(r for r in ds["roles"] if r["id"] == "blockchain-eng")
    # headline stays the EARLY-CAREER generated figure — NOT Adzuna's 160000 blend
    assert swe["countries"]["US"]["source"] == GENERATED_SOURCE
    assert swe["countries"]["US"]["kind"] == GENERATED_KIND
    assert swe["countries"]["US"]["median"] == gen_swe_us_median
    assert swe["countries"]["US"]["median"] != 160000
    # Adzuna still upgraded the real demand signal on that cell
    assert swe["countries"]["US"]["transparency"] > 0
    # cells with no Adzuna stay fully generated
    assert bc["countries"]["US"]["source"] == GENERATED_SOURCE
