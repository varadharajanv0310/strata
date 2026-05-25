"""Database sessions — two engines, by design (brief §2).

* **SQLAlchemy** → the serving + application DB (Postgres in prod, SQLite locally;
  set via ``DATABASE_URL``). Holds accounts, favourites, stored resumes, and the
  materialized serving marts the API reads.
* **DuckDB** → the analytical warehouse over Parquet (the star schema, all heavy
  aggregation). Embedded; no server.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings
from .logging import get_logger

log = get_logger("core.db")


class Base(DeclarativeBase):
    """Declarative base for all app/marts ORM models."""


def _make_engine():
    url = settings.resolved_database_url
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    return engine


# ensure the data dir exists before sqlite tries to open/create its file
settings.ensure_dirs()

engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts/pipelines."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def duckdb_connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the warehouse DuckDB connection (creates the data dir if needed)."""
    settings.ensure_dirs()
    return duckdb.connect(str(settings.duckdb_file), read_only=read_only)


def init_app_db() -> None:
    """Create all app/marts tables (used in local dev; prod uses Alembic)."""
    settings.ensure_dirs()
    # import models so they register on Base.metadata
    from backend.app import models  # noqa: F401  (side-effect import)
    from backend.marts import models as mart_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    log.info("app/marts schema ensured at %s", settings.resolved_database_url)
