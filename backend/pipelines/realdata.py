"""End-to-end **real data** run: Adzuna ingest → overlay → real Job Score +
back-tested forecast → materialize serving marts (atomic seed→real cutover).

Designed for the "Start / Stop" protocol: every stage is idempotent and the
Adzuna fetch resumes from its on-disk cache, so the run survives a kill or a
reboot mid-way and simply continues on the next invocation. Run detached with::

    python -m backend.pipelines.realdata          # full run (fetch + publish)
    python -m backend.pipelines.realdata --no-fetch  # rebuild/publish from cache
"""
from __future__ import annotations

import json
import sys

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("pipelines.realdata")


def _write_state(**kw) -> None:
    try:
        p = settings.data_path / "run_state.json"
        prev = {}
        if p.exists():
            prev = json.loads(p.read_text(encoding="utf-8"))
        prev.update(kw)
        p.write_text(json.dumps(prev, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — state file is best-effort
        log.warning("could not write run_state.json: %s", e)


def run_realdata(fetch: bool = True, throttle: float = 2.5) -> dict:
    from backend.ingest.adzuna_salaries import fetch_all
    from backend.warehouse.real_build import build_real_dataset
    from backend.warehouse.build import build_warehouse_from_dataset
    from backend.ml.job_score import compute_job_scores
    from backend.ml.forecasting import compute_forecasts
    from backend.marts.materialize import materialize_from_warehouse

    _write_state(status="running", stage="ingest")
    if fetch:
        log.info("STAGE 1/5 — Adzuna ingest (resumable)")
        summary = fetch_all(throttle=throttle)
        _write_state(stage="ingest", adzuna=summary)
        log.info("ingest done: %s", summary)

    log.info("STAGE 2/5 — overlay real numbers onto curated taxonomy")
    _write_state(stage="overlay")
    ds = build_real_dataset()
    real, tot, modeled = ds["_real_count"], ds["_total_count"], ds["_modeled_count"]

    log.info("STAGE 3/5 — load warehouse (is_seed=False)")
    _write_state(stage="warehouse", real=real, total=tot, modeled=modeled)
    build_warehouse_from_dataset(ds, is_seed=False)

    log.info("STAGE 4/5 — recompute real Job Score + back-tested forecast")
    _write_state(stage="compute")
    compute_job_scores()
    compute_forecasts()

    log.info("STAGE 5/5 — materialize serving marts (atomic cutover)")
    _write_state(stage="materialize")
    materialize_from_warehouse()

    result = {"real": real, "total": tot, "modeled": modeled, "is_seed": False}
    _write_state(status="done", stage="published", result=result)
    log.info("✅ REAL DATA PUBLISHED — site now serves Adzuna-backed dataset "
             "(%d/%d real, %d modeled). /health dataset_is_seed → false.", real, tot, modeled)
    return result


if __name__ == "__main__":
    fetch = "--no-fetch" not in sys.argv
    try:
        run_realdata(fetch=fetch)
    except Exception as e:  # noqa: BLE001
        _write_state(status="error", error=str(e))
        log.exception("real data run failed: %s", e)
        raise
