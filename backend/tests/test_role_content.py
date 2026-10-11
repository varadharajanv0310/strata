"""Shared per-role content (spec §5): trait profiles + roadmap templates.

These guard the generated files every paid personalization is assembled from: the validators
reject malformed records, the tracked trait file covers the whole catalog on one calibrated
scale, and every tracked roadmap passes the same checks the generators had to pass.
"""
from __future__ import annotations

import json

from backend.warehouse import role_content as rc


def _good_scores():
    return dict(rc.TRAIT_ANCHORS["swe"])


def test_trait_record_validation():
    assert rc.validate_trait_record("swe", {"scores": _good_scores(), "summary": "Ships code."}) == []
    errs = rc.validate_trait_record("swe", {"scores": {"build": 120}, "summary": ""})
    assert any("missing dimensions" in e for e in errs)
    assert any("integer 0-100" in e for e in errs)
    assert any("summary" in e for e in errs)


def _roadmap_for(role):
    core = [s["name"] for s in role["skills"] if s["importance"] == "A"]
    step = lambda name, grade="A": {"skill": name, "what": "topics", "tools": [], "hours": 30, "grade": grade}
    steps = [step(n) for n in core]
    return {"summary": "path", "phases": [
        {"title": "Start", "steps": [step("Git", "prereq"), *steps[:1]], "build": "a tool"},
        {"title": "Core", "steps": steps[1:] or [step("Testing", "I"), step("Docker", "B")], "build": "a service"},
        {"title": "Ship", "steps": [step("CI/CD", "B"), step("Observability", "B")], "build": "deployed"},
    ]}


def test_roadmap_record_validation():
    role = rc._catalog_roles()["swe"]
    assert rc.validate_roadmap_record("swe", _roadmap_for(role), role) == []
    broken = _roadmap_for(role)
    broken["phases"][0]["steps"] = broken["phases"][0]["steps"][:1]      # 1-step phase
    broken["phases"][2]["build"] = ""                                    # phase without a build
    errs = rc.validate_roadmap_record("swe", broken, role)
    assert any("2-6 steps" in e for e in errs)
    assert any("'build'" in e for e in errs)
    assert any("core skills not covered" in e for e in errs)


def test_tracked_traits_cover_catalog_on_one_scale():
    data = rc.load("traits")
    catalog = rc._catalog_roles()
    assert set(data["roles"]) == set(catalog), "every catalog role needs a trait profile"
    assert [d["id"] for d in data["dimensions"]] == rc.DIMENSION_IDS
    for rid, rec in data["roles"].items():
        assert rc.validate_trait_record(rid, rec) == []
    for rid, anchor in rc.TRAIT_ANCHORS.items():                         # calibration held
        assert data["roles"][rid]["scores"] == anchor
    assert data["kind"] == "generated"


def test_tracked_roadmaps_all_valid():
    data = rc.load("roadmaps")
    catalog = rc._catalog_roles()
    assert data["roles"], "roadmaps file is empty"
    for rid, rec in data["roles"].items():
        assert rid in catalog
        assert rc.validate_roadmap_record(rid, rec, catalog[rid]) == []
    assert data["kind"] == "generated"


def test_cli_validate_reports(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"roles": {"swe": {"scores": _good_scores(), "summary": "x"}}}))
    assert rc._main(["validate", "traits", str(good)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"roles": {"not-a-role": {}}}))
    assert rc._main(["validate", "traits", str(bad)]) == 1


# ---- API: what InternshipOS consumes ---------------------------------------------------------
def test_api_dimensions_and_all_traits(client):
    dims = client.get("/api/role-content/dimensions").json()
    assert [d["id"] for d in dims] == rc.DIMENSION_IDS
    t = client.get("/api/role-content/traits").json()
    assert t["kind"] == "generated"
    assert set(t["roles"]) == set(rc._catalog_roles())
    assert t["roles"]["sre"] == rc.TRAIT_ANCHORS["sre"]


def test_api_single_role_content(client):
    body = client.get("/api/role-content/swe").json()
    assert body["kind"] == "generated"
    assert body["traits"]["scores"] == rc.TRAIT_ANCHORS["swe"]
    assert body["roadmap"] and len(body["roadmap"]["phases"]) >= 3
    assert client.get("/api/role-content/not-a-role").status_code == 404


def test_role_detail_keeps_progression_fields(client):
    body = client.get("/api/roles/swe").json()
    assert "experienceCurve" in body and "coverage" in body
