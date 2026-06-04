"""Application DB models (brief §9): accounts, favourites, stored resumes.

Anonymous usage persists nothing; these only back logged-in features. Resumes
are stored **only** for accounts (PII rule, brief §8).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.core.db import Base


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    favourites: Mapped[list["Favourite"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    resumes: Mapped[list["Resume"]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Favourite(Base):
    __tablename__ = "favourite"
    __table_args__ = (UniqueConstraint("account_id", "type", "ref_id", name="uq_fav"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20))          # role | country | comparison
    ref_id: Mapped[str] = mapped_column(String(120))       # role_id, country code, or comparison key
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    family_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["Account"] = relationship(back_populates="favourites")


class Resume(Base):
    __tablename__ = "resume"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parsed: Mapped[dict] = mapped_column(JSON)            # parsed profile (skills/title/years), never raw PII text
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["Account"] = relationship(back_populates="resumes")
