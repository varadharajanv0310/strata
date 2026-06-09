"""Dataset bundle + meta — the frontend hydrates from `/api/dataset`."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app import services
from backend.core.db import get_db

router = APIRouter(prefix="/api", tags=["dataset"])


@router.get("/dataset", summary="Full dataset bundle (mock.js-shaped)")
def get_dataset(db: Session = Depends(get_db)) -> dict:
    return services.assemble_dataset(db)


@router.get("/meta", summary="Dataset metadata (years, seed flag, freshness)")
def get_meta(db: Session = Depends(get_db)) -> dict:
    return services.get_meta(db)
