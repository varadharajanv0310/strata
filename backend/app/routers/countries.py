"""Countries surface: per-country dashboards + Pay Transparency (brief §7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app import services
from backend.app.schemas import CountryOut
from backend.core.db import get_db

router = APIRouter(prefix="/api", tags=["countries"])


@router.get("/countries", response_model=list[CountryOut], summary="All markets")
def countries(db: Session = Depends(get_db)):
    return services.list_countries(db)


@router.get("/countries/{code}", summary="Per-country dashboard payload")
def country(code: str, db: Session = Depends(get_db)) -> dict:
    items = {c["code"]: c for c in services.list_countries(db)}
    if code not in items:
        raise HTTPException(status_code=404, detail=f"country '{code}' not found")
    return {
        "country": items[code],
        "pulse": services.market_pulse(db, code),
        "board": services.jobscore_board(db, code),
    }
