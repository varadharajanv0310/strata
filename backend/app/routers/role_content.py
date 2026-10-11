"""Shared per-role content: trait profiles and roadmap templates (ecosystem spec §5).

Read-only and identical for every user. InternshipOS consumes these: all trait profiles to rate
every role for a student without a model call (§4.2), and a role's roadmap template to build their
personal roadmap (§4.3). The quiz uses the dimension definitions so its answers land on the same
scale as the roles.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from backend.warehouse import role_content as rc

router = APIRouter(prefix="/api/role-content", tags=["role-content"])


@lru_cache(maxsize=1)
def _traits() -> dict:
    return rc.load("traits")


@lru_cache(maxsize=1)
def _roadmaps() -> dict:
    return rc.load("roadmaps")


@router.get("/dimensions", summary="Trait dimensions shared by roles and the quiz")
def dimensions() -> list[dict]:
    return [{"id": i, "group": g, "meaning": m} for i, g, m in rc.TRAIT_DIMENSIONS]


@router.get("/traits", summary="Trait profiles for every role (scores only)")
def traits() -> dict:
    t = _traits()
    return {"kind": t.get("kind"), "source": t.get("source"),
            "roles": {rid: rec["scores"] for rid, rec in t["roles"].items()}}


@router.get("/{role_id}", summary="One role's trait profile and roadmap template")
def role_content(role_id: str) -> dict:
    trait = _traits()["roles"].get(role_id)
    roadmap = _roadmaps()["roles"].get(role_id)
    if trait is None and roadmap is None:
        raise HTTPException(status_code=404, detail=f"no content for role '{role_id}'")
    return {"role_id": role_id, "kind": "generated", "source": rc.GENERATED_SOURCE,
            "traits": trait, "roadmap": roadmap}
