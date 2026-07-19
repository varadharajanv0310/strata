"""Tests for the corpus-free role universe assembler (catalog decoupling, Move B).

These read the O*NET DB zip already in staging (a static reference file, pipeline input) —
no run, no network, no corpus. They assert the assembler mints real, deduped, clean
occupation NODES that widen the catalog independent of posting volume.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.warehouse.role_universe import (
    ONET_PREFIX,
    assemble_universe_nodes,
    curated_soc_anchors,
    load_onet_occupations,
    _norm_skill,
    _tech_soc,
)


@pytest.fixture(scope="module")
def nodes():
    return assemble_universe_nodes()


def test_zip_present_or_skip():
    from backend.core.config import settings
    if not (settings.staging_dir / "onet" / "onet_db.zip").exists():
        pytest.skip("O*NET zip absent in this checkout")


def test_assembler_mints_many_new_nodes(nodes):
    # decoupling delivers a real breadth jump over the curated 16 — corpus-free
    assert len(nodes) >= 20, f"expected a meaningful occupation-node set, got {len(nodes)}"


def test_every_node_has_disjoint_onet_namespace(nodes):
    from backend.warehouse.taxonomy import CURATED_ALIASES
    curated_ids = set(CURATED_ALIASES.keys())
    ids = [n["role_id"] for n in nodes]
    assert len(ids) == len(set(ids)), "duplicate role_ids in the universe"
    for rid in ids:
        assert rid.startswith(ONET_PREFIX), f"{rid} not namespaced"
        assert rid not in curated_ids, f"{rid} collides with a curated node"
        assert not rid.startswith("derived:") and not rid.startswith("emerging:")


def test_curated_soc_anchors_are_skipped(nodes):
    # a SOC a curated role already owns must NOT be re-minted as an occupation node
    anchors = curated_soc_anchors()
    assert anchors, "expected curated O*NET-SOC anchors"
    for n in nodes:
        assert n["soc"] not in anchors, f"{n['role_id']} duplicates curated SOC {n['soc']}"
    # e.g. Software Developers (15-1252.00) is owned by swe → must be absent
    assert not any(n["soc"] == "15-1252.00" for n in nodes)


def test_nodes_carry_name_blurb_and_soc(nodes):
    for n in nodes[:5]:
        assert n["name"].strip()
        assert n["family_id"] == "onet"
        assert n["soc"] and "-" in n["soc"]
        assert len(n["blurb"]) <= 260


def test_new_searchable_roles_present(nodes):
    # exactly the "simple Google search" roles the corpus never surfaced as nodes
    names = {n["name"].lower() for n in nodes}
    for expected in ("database administrators", "penetration testers",
                     "blockchain engineers", "statisticians"):
        assert expected in names, f"missing decoupled role: {expected}"


def test_nodes_ship_real_onet_skills(nodes):
    blockchain = next((n for n in nodes if n["role_id"] == "onet:blockchain-engineers"), None)
    assert blockchain is not None
    skills = {s.lower() for s in blockchain["skills"]}
    assert "solidity" in skills, "expected the real O*NET In-Demand tool set"
    # skills are cleaned, not raw O*NET category labels
    for s in blockchain["skills"]:
        assert len(s) <= 40 and not s.lower().endswith(" software")


def test_all_other_buckets_dropped(nodes):
    assert not any("all other" in n["name"].lower() for n in nodes)


def test_skill_normalizer_cleans_and_drops():
    assert _norm_skill("Structured query language SQL") == "SQL"
    assert _norm_skill("Ansible software") == "Ansible"
    assert _norm_skill("Operating system software") is None   # generic category → dropped
    assert _norm_skill("") is None


def test_tech_soc_filter():
    assert _tech_soc("15-1252.00") and _tech_soc("11-3021.00") and _tech_soc("17-2061.00")
    assert not _tech_soc("29-1141.00") and not _tech_soc("")


def test_graceful_when_zip_absent():
    # a missing reference file must never break the build — empty, not raising
    assert load_onet_occupations(Path("nonexistent/onet.zip")) == []
    assert assemble_universe_nodes(Path("nonexistent/onet.zip")) == []
