"""Posting-title classification onto catalog roles (ecosystem spec §7 Phase 1a).

InternshipOS sends raw posting titles ("Machine Learning Intern", "Data Analyst Intern -
Summer 2027") and needs the strata role each one belongs to. These pin the three fixes that
made that work: posting noise is stripped, a role's own name and generated titles outrank the
legacy 16-role seed, and short skills ('r') can no longer match inside longer words.
"""
from __future__ import annotations

import pytest

from backend.app import resolver as R
from backend.warehouse import role_content as rc


@pytest.fixture(scope="module")
def catalog_resolver():
    res = R.RoleResolver.from_catalog()
    res._embed_failed = True          # exact + fuzzy only: deterministic, no model download
    return res


def test_posting_noise_is_stripped():
    assert R._query_variants("Data Analyst Intern - Summer 2027")[0] == "data analyst"
    assert R._query_variants("Werkstudent Data Science (m/w/d)")[0] == "data science"
    assert "software engineer" in R._query_variants("Software Engineer Intern - Bangalore")
    assert R._query_variants("Intern")  # never empty, even when everything is noise


def test_word_matching_rules():
    assert R._tok_match("engineering", "engineer") > 0.8      # inflection
    assert R._tok_match("scientsit", "scientist") > 0.6       # typo
    assert R._tok_match("project", "product") == 0            # different words that share letters
    assert R._tok_match("design", "designops") == 0           # not an inflection
    assert R._tok_match("r", "cybersecurity") == 0
    assert R._toks(R._query_variants("Werkstudent für Daten")[0]) == ("daten",)   # no stray 'r' from 'für'


@pytest.mark.parametrize("title", ["Chemical Engineer Intern", "Mechanical Engineer Intern",
                                   "Electrical Engineering Intern", "Summer Analyst Intern"])
def test_non_tech_and_generic_titles_stay_unclassified(catalog_resolver, title):
    assert catalog_resolver.classify(title)["confidence"] == "low"


@pytest.mark.parametrize("title, role", [
    ("Software Engineering Intern", "swe"),
    ("SDE Intern", "swe"),
    ("Machine Learning Intern", "ml-eng"),
    ("Data Analyst Intern - Summer 2027", "data-analyst"),
    ("Data Science Intern", "data-sci"),
    ("Cybersecurity Intern", "security"),
    ("Product Management Intern", "pm"),
    ("Android Developer Intern", "android-eng"),
    ("iOS Developer Intern", "ios-eng"),
    ("Penetration Testing Intern", "pentester"),
    ("Business Analyst Intern", "business-analyst"),
    ("Quant Research Intern", "quant-researcher"),
    ("Frontend Developer Intern", "frontend"),
    ("Full Stack Developer Intern", "fullstack"),
    ("DevOps Intern", "devops"),
    ("Data Engineering Intern", "data-eng"),
    ("Backend Engineer Intern", "backend"),
    ("Backend Engineer Intern (Go)", "backend"),                # bracketed asides don't drown the role
    ("Front End Developer Intern", "frontend"),                 # spaced = joined form
    ("Werkstudent für den Bereich Software-Entwicklung", "swe"),
    ("Co-op/Intern Developer, DevOps", "devops"),               # the specific segment beats 'developer'
    ("Software Engineer Intern (Nashville, TN)", "swe"),
    ("Game Developer Intern", "game-client-eng"),
    ("AI Research Intern", "research-scientist-ml"),
    ("Software Engineer Intern - Bangalore", "swe"),
    ("Werkstudent Data Science (m/w/d)", "data-sci"),
    ("Site Reliability Engineering Intern", "sre"),
    ("Technical Writer Intern", "technical-writer"),
    ("Product Design Intern", "product-designer"),
    ("Senior Software Engineer II", "swe"),
])
def test_posting_titles_classify_onto_catalog_roles(catalog_resolver, title, role):
    got = catalog_resolver.classify(title)
    assert got["role_id"] == role, got
    assert got["confidence"] in ("high", "med")


def test_skill_only_match_is_never_high(catalog_resolver):
    # a skill is weak evidence: its full weight sits below the high-confidence line
    assert R._W_SKILL < R._HIGH
    got = catalog_resolver.resolve("Data Handling (Pandas)", use_embedding=False)
    assert got["matched"]["via"] == "data handling pandas"
    assert got["confidence"] != "high"


def test_tracked_aliases_cover_catalog_and_are_valid():
    data = rc.load("aliases")
    assert set(data["roles"]) == set(rc._catalog_roles()), "every catalog role needs aliases"
    assert rc.validate_batch("aliases", data) == []
    assert data["kind"] == "generated"


# ---- API: what InternshipOS calls -------------------------------------------------------------
def test_api_classify_batch(client):
    body = client.post("/api/roles/classify",
                       json={"titles": ["SDE Intern", "Data Analyst Intern - Summer 2027", " "],
                             "alternatives": 1}).json()
    rows = body["results"]
    assert [r["role_id"] for r in rows] == ["swe", "data-analyst", None]
    assert rows[0]["confidence"] == "high" and len(rows[0]["alternatives"]) <= 1
    assert rows[2]["confidence"] == "none"
    assert client.post("/api/roles/classify", json={"titles": []}).status_code == 422
