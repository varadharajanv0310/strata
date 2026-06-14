"""Cross-cutting provenance lookup — where any figure came from (brief §7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.app import services
from backend.app.schemas import ProvenanceOut, SourceProvenanceOut
from backend.core.db import get_db

router = APIRouter(prefix="/api", tags=["provenance"])


@router.get("/provenance/sources", response_model=list[SourceProvenanceOut],
            summary="Per-source provenance manifest (snapshot hash + transform version + row count)")
def provenance_sources(db: Session = Depends(get_db)):
    return services.list_provenance(db)


@router.get("/provenance", response_model=ProvenanceOut, summary="Source/sample/freshness + lineage for a figure")
def provenance(
    role: str = Query(..., description="role id"),
    country: str = Query(..., description="country code"),
    db: Session = Depends(get_db),
):
    p = services.provenance(db, role, country)
    if not p:
        raise HTTPException(status_code=404, detail="no figure for that role/country")
    return p
