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


def test_measured_upgrades_generated_via_real_build(monkeypatch):
    """build_real_dataset bases on the generated spine; where Adzuna has a fact it upgrades
    the cell (measured source wins), and where it doesn't the cell stays generated."""
    from backend.warehouse import real_build

    monkeypatch.setattr(real_build, "has_generated_catalog", lambda: True)
    monkeypatch.setattr(real_build, "build_generated_dataset", lambda: build_generated_dataset(_CATALOG))
    # one real Adzuna cell for swe/US; nothing for blockchain-eng
    monkeypatch.setattr(real_build, "load_cache",
                        lambda: {("US", "swe"): {"mean": 160000, "count": 2000,
                                                 "n_results": 100, "n_disclosed": 60}})
    monkeypatch.setattr(real_build, "latest_ppp", lambda: {})
    ds = real_build.build_real_dataset()
    swe = next(r for r in ds["roles"] if r["id"] == "swe")
    bc = next(r for r in ds["roles"] if r["id"] == "blockchain-eng")
    # measured upgrade: swe/US now Adzuna-sourced (measured stamp wins)
    assert swe["countries"]["US"]["source"] == real_build.REAL_SOURCE
    assert swe["countries"]["US"]["kind"] == "job-level"
    # untouched generated cells stay generated (filled, never relabeled 'modeled')
    assert bc["countries"]["US"]["source"] == GENERATED_SOURCE
    assert bc["countries"]["US"]["kind"] == GENERATED_KIND
    assert swe["countries"]["IN"]["source"] == GENERATED_SOURCE
