"""Pipeline orchestration (brief §10) — idempotent, resumable stages:

  ingest_<source> → build_staging → gpu_normalize → build_warehouse → compute → materialize_marts

Prefect drives this when installed (`requirements.txt`); otherwise a plain
sequential runner executes the same stages so the pipeline is always runnable.
Stages are idempotent and skip+flag gracefully (the Common Crawl scan checkpoints
and never restarts from zero).
"""
from __future__ import annotations

from backend.core.logging import get_logger

log = get_logger("pipelines")


# ---- stage callables (pure functions; safe to re-run) ----
def stage_seed() -> None:
    from backend.marts.materialize import materialize_from_warehouse
    from backend.warehouse.build import build_warehouse_from_dataset
    from backend.warehouse.seed import build_seed_dataset
    build_warehouse_from_dataset(build_seed_dataset(), is_seed=True)
    materialize_from_warehouse()


def stage_ingest_all() -> None:
    from backend.ingest import run_connector
    run_connector("all")


def stage_gpu_normalize() -> None:
    from backend.ml import run_stage
    for s in ("skill_norm", "entity_resolution", "role_derivation"):
        try:
            run_stage(s)
        except NotImplementedError as e:
            log.warning("skip %s — %s", s, e)


def stage_build_warehouse() -> None:
    from backend.warehouse.build import build_warehouse_from_staging
    build_warehouse_from_staging()


def stage_compute() -> None:
    from backend.ml.forecasting import compute_forecasts
    from backend.ml.job_score import compute_job_scores
    compute_job_scores()
    compute_forecasts()


def stage_materialize() -> None:
    from backend.marts.materialize import materialize_from_warehouse
    materialize_from_warehouse()


FLOWS = {
    "seed": [stage_seed],
    "ingest": [stage_ingest_all],
    "compute": [stage_compute, stage_materialize],
    "marts": [stage_materialize],
    # full real pipeline (stages skip+flag until creds/data/GPU are present)
    "full": [stage_ingest_all, stage_gpu_normalize, stage_build_warehouse, stage_compute, stage_materialize],
}


def _run_sequential(name: str, stages: list) -> None:
    log.info("flow '%s' (sequential): %d stage(s)", name, len(stages))
    for fn in stages:
        try:
            fn()
        except Exception as e:
            log.error("stage %s failed: %s", getattr(fn, "__name__", fn), e)
            raise


def run_flow(name: str) -> None:
    if name not in FLOWS:
        raise ValueError(f"unknown flow '{name}'. one of: {', '.join(FLOWS)}")
    stages = FLOWS[name]
    try:
        from prefect import flow, task  # type: ignore

        tasks = [task(fn, name=fn.__name__) for fn in stages]

        @flow(name=f"strata-{name}")
        def _flow():
            for t in tasks:
                t()

        log.info("flow '%s' (prefect): %d stage(s)", name, len(stages))
        _flow()
    except ModuleNotFoundError:
        _run_sequential(name, stages)
