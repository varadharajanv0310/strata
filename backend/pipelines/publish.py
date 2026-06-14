"""Warehouse → served-artifact publish path (brief: "make the path real and tested").

The fusion + materialize halves both already existed, but no single command ran
``staging → warehouse → marts/app.db`` atomically with the taxonomy + provenance
layers wired in. This is that command:

    staging  ──build_warehouse_from_staging──▶  warehouse.duckdb (facts)
             ──build_taxonomy───────────────▶  dim_role_alias / crosswalk / births
             ──compute_job_scores+forecasts─▶  fact_job_score / fact_demand_forecast
             ──materialize_from_warehouse───▶  app.db serving marts (the spine)
             ──materialize_aliases──────────▶  mart_role_alias (resolver enrichment)
             ──materialize_provenance───────▶  mart_provenance (lineage tuple)

It runs against whatever DB the env points at — so the test harness (temp DuckDB +
SQLite, isolated by conftest) exercises the whole path end-to-end WITHOUT touching
the persistent warehouse or the live site. Publishing to the live marts is a
deliberate, separate act (run this against the real env), never done implicitly.
"""
from __future__ import annotations

import time

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("pipelines.publish")


def publish_served(*, with_taxonomy: bool = True, compute: bool = True) -> dict:
    """Run the full warehouse→served path. Returns a summary of row counts.

    Visible heartbeat per stage. Idempotent (every stage clears + rewrites its
    target). Reads staging read-only; writes the configured warehouse + app.db.
    """
    t0 = time.time()
    summary: dict = {"warehouse": settings.duckdb_path, "db": settings.resolved_database_url}

    def _beat(stage: str):
        log.info("▶ %s  (+%.0fs)", stage, time.time() - t0)

    # 1) fuse staging → warehouse facts
    _beat("build_warehouse_from_staging")
    from backend.warehouse.build import build_warehouse_from_staging
    build_warehouse_from_staging()

    # 2) taxonomy (alias graph / crosswalk / births) on the warehouse
    if with_taxonomy:
        _beat("build_taxonomy")
        from backend.core.db import duckdb_connect
        from backend.warehouse.taxonomy import build_taxonomy
        con = duckdb_connect()
        try:
            summary["taxonomy"] = build_taxonomy(con)
        finally:
            con.close()

    # 3) compute Job Score + back-tested forecast (materialize reads these)
    if compute:
        _beat("compute_job_scores + forecasts")
        from backend.ml.job_score import compute_job_scores
        from backend.ml.forecasting import compute_forecasts
        compute_job_scores()
        compute_forecasts()

    # 4) materialize serving marts (the spine)
    _beat("materialize_from_warehouse")
    from backend.marts.materialize import (
        materialize_aliases,
        materialize_from_warehouse,
        materialize_provenance,
    )
    materialize_from_warehouse()

    # 5) + alias graph and provenance manifest into the served layer
    if with_taxonomy:
        _beat("materialize_aliases")
        summary["alias_rows"] = materialize_aliases()
    _beat("materialize_provenance")
    summary["provenance_rows"] = materialize_provenance()

    # refresh the in-process resolver so it picks up the new alias mart
    try:
        from backend.app.resolver import invalidate_resolver
        invalidate_resolver()
    except Exception:  # noqa: BLE001
        pass

    summary["elapsed_s"] = round(time.time() - t0, 1)
    log.info("✅ served artifact published in %.0fs: %s", summary["elapsed_s"], summary)
    return summary


if __name__ == "__main__":
    # NOTE: against the real env this publishes to the live marts. The build pass
    # only ever runs this under the isolated test harness.
    publish_served()
