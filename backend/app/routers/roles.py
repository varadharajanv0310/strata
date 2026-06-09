"""Roles surface: search/list, Role Dashboard, Job Score board (brief §7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app import services
from backend.app.schemas import JobScoreRow, RoleOut
from backend.core.db import get_db

router = APIRouter(prefix="/api", tags=["roles"])


@router.get("/roles", summary="Search / browse roles")
def roles(
    q: str | None = Query(None, description="search role name or skill"),
    family: str | None = Query(None, description="family id, or 'all'"),
    db: Session = Depends(get_db),
) -> list[dict]:
    return services.list_roles(db, q, family)


@router.get("/roles/{role_id}", response_model=RoleOut, summary="Role Dashboard (full)")
def role(role_id: str, db: Session = Depends(get_db)):
    r = services.get_role(db, role_id)
    if not r:
        raise HTTPException(status_code=404, detail=f"role '{role_id}' not found")
    return r


@router.get("/jobscore", response_model=list[JobScoreRow], summary="Job Score board for a country")
def jobscore(
    country: str = Query("IN"),
    limit: int | None = Query(None, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return services.jobscore_board(db, country, limit)
