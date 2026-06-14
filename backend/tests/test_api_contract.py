"""API contract conformance to the frontend `mock.js` shapes (brief §13)."""
from __future__ import annotations

DATASET_KEYS = {"countries", "families", "roles", "years", "fyears", "marketPulse", "resume_sample", "resume_b"}
ROLE_KEYS = {"id", "name", "family", "blurb", "skills", "ladder", "countries"}
RC_KEYS = {"median", "series", "demandSeries", "forecast", "demand", "interest", "score",
           "sample", "conf", "kind", "source", "freshness", "transparency"}
SCORE_KEYS = {"total", "demand", "pay", "opp", "rank", "pctile"}
COUNTRY_KEYS = {"code", "name", "cur", "curCode", "natFactor", "pppRate", "transparency", "c1", "c2"}


def test_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok"


def test_dataset_shape(client):
    ds = client.get("/api/dataset").json()
    assert DATASET_KEYS.issubset(ds)
    assert len(ds["countries"]) == 7
    assert len(ds["roles"]) == 16
    assert COUNTRY_KEYS.issubset(ds["countries"][0])
    ml = next(r for r in ds["roles"] if r["id"] == "ml-eng")
    assert ROLE_KEYS.issubset(ml)
    cd = ml["countries"]["IN"]
    assert RC_KEYS.issubset(cd)
    assert SCORE_KEYS.issubset(cd["score"])
    assert cd["median"] == 1900000           # native currency, single-point median
    assert len(cd["series"]) == 9 and len(cd["forecast"]) == 3
    assert {"year", "value"} <= set(cd["series"][0])
    assert ds["is_seed"] is True             # seed clearly flagged


def test_role_dashboard(client):
    r = client.get("/api/roles/ml-eng").json()
    assert r["name"] == "Machine Learning Engineer"
    assert isinstance(r["ladder"], list) and len(r["ladder"][0]) == 2
    assert client.get("/api/roles/does-not-exist").status_code == 404


def test_jobscore_board(client):
    board = client.get("/api/jobscore?country=IN").json()
    assert len(board) == 16
    totals = [r["score"]["total"] for r in board]
    assert totals == sorted(totals, reverse=True)         # ranked
    assert all(1 <= r["score"]["pctile"] <= 100 for r in board)


def test_countries(client):
    assert len(client.get("/api/countries").json()) == 7
    dash = client.get("/api/countries/IN").json()
    assert dash["country"]["code"] == "IN" and "pulse" in dash and "board" in dash


def test_provenance(client):
    p = client.get("/api/provenance?role=ml-eng&country=IN").json()
    assert {"source", "sample", "confidence", "kind", "freshness", "transparency", "is_seed"} <= set(p)


def test_compare_unrestricted(client):
    # any role × any country vs any other (brief §12)
    out = client.get("/api/compare?roles=data-analyst,swe&countries=GB,CA").json()
    assert len(out["cells"]) == 4
    assert {c["country"] for c in out["cells"]} == {"GB", "CA"}
