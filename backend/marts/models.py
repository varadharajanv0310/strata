"""Serving marts (brief §5/§7): query-ready aggregates the API reads.

Materialized from the DuckDB warehouse. Denormalized for fast serving in the exact
shapes the frontend's `data/mock.js` expects, so mock → real is a drop-in. Series
(salary/demand/forecast) are stored as JSON per Role × Country to assemble the Role
Dashboard in a single read. Every row carries provenance + confidence + `is_seed`.
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.db import Base


class MartCountry(Base):
    __tablename__ = "mart_country"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    cur: Mapped[str] = mapped_column(String(4))
    cur_code: Mapped[str] = mapped_column(String(3))
    nat_factor: Mapped[float] = mapped_column(Float)
    ppp_rate: Mapped[float] = mapped_column(Float)
    transparency: Mapped[float] = mapped_column(Float)
    c1: Mapped[str] = mapped_column(String(9))
    c2: Mapped[str] = mapped_column(String(9))
    ord: Mapped[int] = mapped_column(Integer, default=0)


class MartFamily(Base):
    __tablename__ = "mart_family"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    hue: Mapped[int] = mapped_column(Integer)
    ord: Mapped[int] = mapped_column(Integer, default=0)


class MartRole(Base):
    __tablename__ = "mart_role"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    family_id: Mapped[str] = mapped_column(String(40), index=True)
    family_name: Mapped[str] = mapped_column(String(80))
    family_hue: Mapped[int] = mapped_column(Integer)
    blurb: Mapped[str] = mapped_column(String(600))
    ord: Mapped[int] = mapped_column(Integer, default=0)


class MartRoleSkill(Base):
    __tablename__ = "mart_role_skill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(120), index=True)
    name: Mapped[str] = mapped_column(String(120))
    level: Mapped[str] = mapped_column(String(1))          # A | I | B
    dura: Mapped[int] = mapped_column(Integer)
    trend: Mapped[str] = mapped_column(String(10))
    ord: Mapped[int] = mapped_column(Integer, default=0)


class MartRoleLadder(Base):
    __tablename__ = "mart_role_ladder"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(120), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(120))
    mult: Mapped[float] = mapped_column(Float)


class MartRoleCountry(Base):
    """The per-role-per-country serving row — the spine of the Role Dashboard."""

    __tablename__ = "mart_role_country"

    role_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)

    median: Mapped[float] = mapped_column(Float)
    demand: Mapped[int] = mapped_column(Integer)
    interest: Mapped[int] = mapped_column(Integer)

    score_total: Mapped[float] = mapped_column(Float)
    score_demand: Mapped[float] = mapped_column(Float)
    score_pay: Mapped[float] = mapped_column(Float)
    score_opp: Mapped[float] = mapped_column(Float)
    score_rank: Mapped[int] = mapped_column(Integer)
    score_pctile: Mapped[int] = mapped_column(Integer)

    # provenance / confidence (drives the badge + lookup)
    sample: Mapped[int] = mapped_column(Integer)
    conf: Mapped[str] = mapped_column(String(4))           # high | med | low
    kind: Mapped[str] = mapped_column(String(16))          # job-level | person-level
    source: Mapped[str] = mapped_column(String(160))
    freshness: Mapped[str] = mapped_column(String(40))
    transparency: Mapped[float] = mapped_column(Float)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)

    # series (JSON) — assembled once at materialization for fast serving
    series: Mapped[list] = mapped_column(JSON)             # [{year, value}] salary over time
    demand_series: Mapped[list] = mapped_column(JSON)      # [{year, value}]
    forecast: Mapped[list] = mapped_column(JSON)           # [{year, value, lo, hi}]


class MartMarketPulse(Base):
    __tablename__ = "mart_market_pulse"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    kind: Mapped[str] = mapped_column(String(16))          # hottest | topPay | rising | topScore
    ord: Mapped[int] = mapped_column(Integer)
    role_id: Mapped[str] = mapped_column(String(120))


class MartMeta(Base):
    """Dataset-level metadata: years, forecast years, seed flag, generated timestamp."""

    __tablename__ = "mart_meta"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
