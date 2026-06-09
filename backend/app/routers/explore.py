"""Explore surface: market-pulse feeds for the open canvas (brief §7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app import services
from backend.core.db import get_db

router = APIRouter(prefix="/api/explore", tags=["explore"])


@router.get("/pulse", summary="Market pulse (hottest / top-pay / rising / top-score)")
def pulse(country: str = Query("IN"), db: Session = Depends(get_db)) -> dict:
    return {"country": country, "pulse": services.market_pulse(db, country)}
