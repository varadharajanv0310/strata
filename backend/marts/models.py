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

    median: Mapped[float] = mapped_column(Float)          # ADVERTISED lens (fact_salary_job)
    demand: Mapped[int] = mapped_column(Integer)
    interest: Mapped[int] = mapped_column(Integer)

    # THREE-LENS salary — advertised (median, above) + realized + official, shown
    # side-by-side, never blended. Nullable: a lens is null when that source has no
    # data for this role×country (the UI then honestly shows "not enough data").
    median_realized: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_realized: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_realized: Mapped[str | None] = mapped_column(String(160), nullable=True)
    median_official: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_official: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_official: Mapped[str | None] = mapped_column(String(160), nullable=True)

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


class MartRoleAlias(Base):
    """Served slice of the role alias graph — backs the never-dead-end resolver.

    Materialized from warehouse ``dim_role_alias``. The resolver also carries the
    curated seed in-process, so this table is an enrichment (the full alias graph),
    not a hard dependency.
    """

    __tablename__ = "mart_role_alias"

    alias_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    surface: Mapped[str] = mapped_column(String(200))
    norm: Mapped[str] = mapped_column(String(200), index=True)
    role_id: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[str] = mapped_column(String(20))
    lang: Mapped[str] = mapped_column(String(6), default="en")
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class MartRoleAdjacency(Base):
    """Served role→role TRAJECTORY edges ("where does this role lead?").

    Materialized from warehouse ``bridge_role_adjacency`` (O*NET/ESCO/Wikidata).
    ROLES-ONLY: every edge is occupation→occupation, never an employer career graph.
    """

    __tablename__ = "mart_role_adjacency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_role: Mapped[str] = mapped_column(String(120), index=True)
    to_role: Mapped[str] = mapped_column(String(120))
    to_role_name: Mapped[str] = mapped_column(String(160))
    similarity: Mapped[float] = mapped_column(Float)
    edge_type: Mapped[str] = mapped_column(String(20))     # similar | career_change | sibling | ...
    source: Mapped[str] = mapped_column(String(20))         # onet | esco | wikidata


class MartRoleSkillImportance(Base):
    """Served per-role skill IMPORTANCE weighting (O*NET/ESCO) — core vs peripheral,
    so a role's skill bag is graded, not flat. Distinct from the curated skill list."""

    __tablename__ = "mart_role_skill_importance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(120), index=True)
    skill_id: Mapped[str] = mapped_column(String(120))
    skill_name: Mapped[str] = mapped_column(String(120))
    importance: Mapped[float] = mapped_column(Float)        # 0-100
    level: Mapped[float | None] = mapped_column(Float, nullable=True)
    essential: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20))         # onet | esco


class MartRoleOutlook(Base):
    """Served forward demand-OUTLOOK per role×country (official projections).

    Materialized from warehouse ``fact_role_outlook`` (BLS-EP / Canada-COPS / JSA).
    Roles-only: occupation growth, not company hiring."""

    __tablename__ = "mart_role_outlook"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[str] = mapped_column(String(120), index=True)
    country_code: Mapped[str] = mapped_column(String(2), index=True)
    horizon_years: Mapped[int] = mapped_column(Integer)
    growth_pct: Mapped[float] = mapped_column(Float)
    openings_per_year: Mapped[float | None] = mapped_column(Float, nullable=True)
    outlook_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)
    shortage_flag: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str] = mapped_column(String(40))


class MartSkillAdoption(Base):
    """Served per-skill ADOPTION / durability summary (registries / SE / arXiv / HF /
    Wikipedia). One row per (skill, metric, ecosystem) with the latest value + a
    momentum % (recent vs prior period) — the "rising or fading" signal. Global
    (country-invariant) skill attribute that modulates the role-scoped signals."""

    __tablename__ = "mart_skill_adoption"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str] = mapped_column(String(120), index=True)
    skill_name: Mapped[str] = mapped_column(String(120))
    metric: Mapped[str] = mapped_column(String(20))         # downloads | questions | submissions | ...
    ecosystem: Mapped[str] = mapped_column(String(20))
    latest_period: Mapped[str] = mapped_column(String(7))
    latest_value: Mapped[float] = mapped_column(Float)
    momentum_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class MartProvenance(Base):
    """Per-source provenance manifest — the (source_id, snapshot_hash,
    transform_version, row_count, as_of) tuple threaded into the served layer so a
    number's full lineage is answerable, not just its source name."""

    __tablename__ = "mart_provenance"

    source_id: Mapped[str] = mapped_column(String(60), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(20))            # person-level | job-level | demand | ...
    snapshot_hash: Mapped[str] = mapped_column(String(40))   # sha1 of the staging snapshot (or '')
    transform_version: Mapped[str] = mapped_column(String(40))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    as_of: Mapped[str] = mapped_column(String(40))           # ISO timestamp of the snapshot
