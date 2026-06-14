"""strata FastAPI application.

Public, read-only data endpoints (every payload carries native currency +
confidence/provenance); account features are authenticated. Response shapes equal
the frontend's `mock.js` contract so mock → real is a drop-in.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from backend.app.routers import (
    auth,
    compare,
    countries,
    dataset,
    explore,
    favourites,
    provenance,
    resume,
    roles,
)
from backend.app.schemas import HealthOut
from backend.core.config import settings
from backend.core.db import SessionLocal
from backend.core.logging import get_logger, setup_logging

setup_logging()
log = get_logger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        from backend.marts.models import MartRoleCountry

        with SessionLocal() as db:
            n = db.scalar(select(func.count()).select_from(MartRoleCountry))
        if not n:
            log.warning("marts are empty — run `python -m backend.cli seed` to populate.")
        else:
            log.info("API ready — %d role×country marts loaded.", n)
    except Exception as e:  # pragma: no cover
        log.warning("startup mart check skipped: %s", e)
    yield


app = FastAPI(
    title="strata API",
    version="0.1.0",
    description=(
        "Tech job-market intelligence — salaries, demand, skills, rankings across 7 markets. "
        "Read-only public data; native currencies; PPP (no live FX); provenance + confidence on every figure."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (dataset, roles, countries, explore, compare, provenance, resume, auth, favourites):
    app.include_router(r.router)


@app.get("/health", response_model=HealthOut, tags=["meta"])
def health() -> HealthOut:
    seed_flag = None
    try:
        from backend.marts.models import MartMeta

        with SessionLocal() as db:
            meta = db.get(MartMeta, "dataset")
            seed_flag = bool(meta.value.get("is_seed")) if meta else None
    except Exception:
        seed_flag = None
    return HealthOut(status="ok", env=settings.env, dataset_is_seed=seed_flag)
