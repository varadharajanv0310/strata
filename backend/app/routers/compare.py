"""Compare surface: fully-unrestricted role × country × year matrix (brief §7/§12).

Any role × country can be compared against any other. The frontend assembles most
Compare views client-side from the dataset bundle; this endpoint backs API users
and the contract tests, and is intentionally unrestricted.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app import services
from backend.core.db import get_db

router = APIRouter(prefix="/api", tags=["compare"])


@router.get("/compare", summary="Compare any roles across any countries")
def compare(
    roles: str = Query(..., description="comma-separated role ids"),
    countries: str = Query("IN", description="comma-separated country codes"),
    db: Session = Depends(get_db),
) -> dict:
    role_ids = [r.strip() for r in roles.split(",") if r.strip()]
    codes = [c.strip() for c in countries.split(",") if c.strip()]
    if not role_ids:
        raise HTTPException(status_code=400, detail="at least one role id required")

    cells = []
    for rid in role_ids:
        role = services.get_role(db, rid)
        if not role:
            continue
        for code in codes:
            cd = role["countries"].get(code)
            if not cd:
                continue
            cells.append({
                "role_id": rid, "role": role["name"], "country": code,
                "median": cd["median"], "demand": cd["demand"], "interest": cd["interest"],
                "score": cd["score"], "series": cd["series"], "demandSeries": cd["demandSeries"],
                "conf": cd["conf"], "kind": cd["kind"], "source": cd["source"],
            })
    return {"roles": role_ids, "countries": codes, "cells": cells}
