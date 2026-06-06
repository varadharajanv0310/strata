"""Accounts (brief §9): register / login / me. Optional — anonymous works everywhere.

Passwords are bcrypt-hashed; sessions are JWT. Kept simple and secure.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.deps import current_account
from backend.app.models import Account
from backend.core.db import get_db
from backend.core.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    token: str
    email: str


class MeOut(BaseModel):
    id: int
    email: str


@router.post("/register", response_model=TokenOut)
def register(body: Credentials, db: Session = Depends(get_db)):
    if db.scalar(select(Account).where(Account.email == body.email)):
        raise HTTPException(status_code=409, detail="email already registered")
    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="password must be at least 8 characters")
    acc = Account(email=body.email, password_hash=hash_password(body.password))
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return TokenOut(token=create_access_token(str(acc.id)), email=acc.email)


@router.post("/login", response_model=TokenOut)
def login(body: Credentials, db: Session = Depends(get_db)):
    acc = db.scalar(select(Account).where(Account.email == body.email))
    if not acc or not verify_password(body.password, acc.password_hash):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return TokenOut(token=create_access_token(str(acc.id)), email=acc.email)


@router.get("/me", response_model=MeOut)
def me(account: Account = Depends(current_account)):
    return MeOut(id=account.id, email=account.email)
