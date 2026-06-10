"""Accounts + favourites (brief §9)."""
from __future__ import annotations

EMAIL = "tester@example.com"
PW = "testpassw0rd"


def _token(client) -> str:
    r = client.post("/api/auth/register", json={"email": EMAIL, "password": PW})
    if r.status_code == 409:  # already created by a prior run
        r = client.post("/api/auth/login", json={"email": EMAIL, "password": PW})
    assert r.status_code == 200
    return r.json()["token"]


def test_auth_flow(client):
    tok = _token(client)
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/auth/me", headers=h).json()["email"] == EMAIL
    assert client.post("/api/auth/login", json={"email": EMAIL, "password": "wrong"}).status_code == 401


def test_favourites_require_auth_and_are_idempotent(client):
    tok = _token(client)
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/favourites").status_code == 401          # anonymous persists nothing
    client.post("/api/favourites", json={"type": "role", "ref_id": "swe", "label": "Software Engineer"}, headers=h)
    client.post("/api/favourites", json={"type": "role", "ref_id": "swe"}, headers=h)  # idempotent
    favs = client.get("/api/favourites", headers=h).json()
    swe = [f for f in favs if f["ref_id"] == "swe"]
    assert len(swe) == 1
    assert client.delete(f"/api/favourites/{swe[0]['id']}", headers=h).status_code == 204
