"""Alembic environment — wires migrations to the app/serving DB.

URL comes from `settings.resolved_database_url` so SQLite (local) and Postgres
(prod) share one migration history. `target_metadata` includes both the app and
marts models.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# make `backend` importable when alembic runs from anywhere
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.config import settings  # noqa: E402
from backend.core.db import Base  # noqa: E402
from backend.app import models as _app_models  # noqa: E402,F401  (register tables)
from backend.marts import models as _mart_models  # noqa: E402,F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.resolved_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.resolved_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,  # safe ALTERs on SQLite
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = settings.resolved_database_url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
