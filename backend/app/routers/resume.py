"""Résumé surface (brief §8).

- `GET  /api/resume/sample` — the sample profiles the "use a sample" path uses.
- `POST /api/resume/parse`  — upload (PDF/DOCX/TXT) → parse → profile (this résumé
  only). Anonymous = parsed in memory, nothing stored. Logged-in + `store=true` =
  the structured profile is saved (never raw PII). **Never invents a Resume B.**
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from backend.app.deps import current_account_optional
from backend.app.models import Account, Resume
from backend.app.resume_service import build_profile, extract_text
from backend.core.db import get_db
from backend.marts import models as M

router = APIRouter(prefix="/api/resume", tags=["resume"])


@router.get("/sample", summary="Sample profiles for the résumé demo")
def sample(db: Session = Depends(get_db)) -> dict:
    meta = db.get(M.MartMeta, "profiles")
    return meta.value if meta else {"sample": {}, "b": {}}


@router.post("/parse", summary="Parse an uploaded résumé into a profile (this résumé only)")
async def parse(
    file: UploadFile = File(...),
    store: bool = Query(False, description="store the parsed profile (logged-in only)"),
    account: Account | None = Depends(current_account_optional),
    db: Session = Depends(get_db),
) -> dict:
    data = await file.read()
    text = extract_text(file.filename, data)
    profile = build_profile(db, text)  # ONLY this résumé — no fabricated Resume B
    stored = False
    if store and account is not None:
        db.add(Resume(account_id=account.id, filename=file.filename, parsed=profile))
        db.commit()
        stored = True
    return {
        "profile": profile,
        "stored": stored,
        "note": "Analysis is for this résumé only; a second profile is never invented.",
    }
