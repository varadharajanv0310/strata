"""Résumé feature (brief §8) — the inviolable user-data rule."""
from __future__ import annotations


def test_parse_returns_only_this_resume(client):
    cv = b"Data Scientist. 5 years experience. Python, Statistics, Machine Learning, SQL, PyTorch, A/B Testing."
    r = client.post("/api/resume/parse", files={"file": ("cv.txt", cv, "text/plain")})
    assert r.status_code == 200
    body = r.json()
    # never fabricates a second résumé
    assert "profile" in body
    assert "b" not in body and "resume_b" not in body and "profile_b" not in body
    # anonymous stores nothing
    assert body["stored"] is False
    p = body["profile"]
    assert "Python" in p["skills"]
    assert len(p["matchRoles"]) >= 1
    assert len(p["axes"]) == 6


def test_build_profile_extracts_skills():
    from backend.app.resume_service import build_profile
    from backend.core.db import session_scope

    with session_scope() as db:
        prof = build_profile(db, "Frontend Engineer. React, TypeScript, JavaScript. 3 years.")
    assert "React" in prof["skills"] and "TypeScript" in prof["skills"]
    assert prof["years"] == 3
