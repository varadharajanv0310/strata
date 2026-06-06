"""Favourites (brief §9): a small, quiet saved shelf for logged-in accounts —
roles / countries / comparisons. No digests, no notifications, no feed. Anonymous
users persist nothing (handled client-side in localStorage).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.deps import current_account
from backend.app.models import Account, Favourite
from backend.core.db import get_db

router = APIRouter(prefix="/api/favourites", tags=["favourites"])


class FavIn(BaseModel):
    type: str            # role | country | comparison
    ref_id: str
    label: str | None = None
    family_id: str | None = None


class FavOut(BaseModel):
    id: int
    type: str
    ref_id: str
    label: str | None = None
    family_id: str | None = None


@router.get("", response_model=list[FavOut])
def list_favs(account: Account = Depends(current_account), db: Session = Depends(get_db)):
    rows = db.scalars(select(Favourite).where(Favourite.account_id == account.id).order_by(Favourite.id))
    return [FavOut(id=f.id, type=f.type, ref_id=f.ref_id, label=f.label, family_id=f.family_id) for f in rows]


@router.post("", response_model=FavOut)
def add_fav(body: FavIn, account: Account = Depends(current_account), db: Session = Depends(get_db)):
    existing = db.scalar(select(Favourite).where(
        Favourite.account_id == account.id, Favourite.type == body.type, Favourite.ref_id == body.ref_id))
    if existing:
        return FavOut(id=existing.id, type=existing.type, ref_id=existing.ref_id,
                      label=existing.label, family_id=existing.family_id)
    fav = Favourite(account_id=account.id, type=body.type, ref_id=body.ref_id,
                    label=body.label, family_id=body.family_id)
    db.add(fav)
    db.commit()
    db.refresh(fav)
    return FavOut(id=fav.id, type=fav.type, ref_id=fav.ref_id, label=fav.label, family_id=fav.family_id)


@router.delete("/{fav_id}", status_code=204)
def remove_fav(fav_id: int, account: Account = Depends(current_account), db: Session = Depends(get_db)):
    fav = db.get(Favourite, fav_id)
    if not fav or fav.account_id != account.id:
        raise HTTPException(status_code=404, detail="favourite not found")
    db.delete(fav)
    db.commit()
