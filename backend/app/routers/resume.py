"""Résumé surface (brief §8).

Phase 2 exposes the sample profiles the frontend's "use a sample" path uses.
The real upload→parse→price pipeline (with the inviolable user-data rule —
never fabricate a user's inputs, never invent a Resume B) lands in Phase 5 at
`POST /api/resume/parse`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.db import get_db
from backend.marts import models as M

router = APIRouter(prefix="/api/resume", tags=["resume"])


@router.get("/sample", summary="Sample profiles for the résumé demo")
def sample(db: Session = Depends(get_db)) -> dict:
    meta = db.get(M.MartMeta, "profiles")
    return meta.value if meta else {"sample": {}, "b": {}}
