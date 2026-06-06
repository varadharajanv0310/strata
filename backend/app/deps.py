"""Shared FastAPI dependencies — optional/required account from a Bearer token.

Anonymous usage works everywhere; these only gate account features (brief §9).
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.app.models import Account
from backend.core.db import get_db
from backend.core.security import decode_token


def current_account_optional(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> Account | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    payload = decode_token(authorization.split(" ", 1)[1])
    if not payload or "sub" not in payload:
        return None
    try:
        return db.get(Account, int(payload["sub"]))
    except (ValueError, TypeError):
        return None


def current_account(account: Account | None = Depends(current_account_optional)) -> Account:
    if account is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return account
