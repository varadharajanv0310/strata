"""strata pipeline CLI — run any single stage or the whole flow.

    python -m backend.cli init-db
    python -m backend.cli seed
    python -m backend.cli serve --reload
    python -m backend.cli ingest adzuna --limit 500
    python -m backend.cli warehouse-build
    python -m backend.cli marts-materialize
    python -m backend.cli jobscore
    python -m backend.cli pipeline full

Stages import lazily so the CLI works even before later-phase modules exist.
Crawl scope and all params come from config/.env (brief §10).
"""
from __future__ import annotations

import argparse
import sys

from backend.core.config import settings
from backend.core.logging import get_logger, setup_logging

log = get_logger("cli")


def cmd_init_db(_args: argparse.Namespace) -> int:
    from backend.core.db import init_app_db

    init_app_db()
    return 0


def cmd_seed(_args: argparse.Namespace) -> int:
    from backend.warehouse.seed import build_seed_dataset
    from backend.warehouse.build import build_warehouse_from_dataset
    from backend.marts.materialize import materialize_from_warehouse

    ds = build_seed_dataset()
    build_warehouse_from_dataset(ds, is_seed=True)
    materialize_from_warehouse()
    log.info("seed → warehouse → marts complete")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
        reload=args.reload,
    )
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from backend.ingest import run_connector

    run_connector(args.source, limit=args.limit)
    return 0


def cmd_warehouse_build(_args: argparse.Namespace) -> int:
    from backend.warehouse.build import build_warehouse_from_staging

    build_warehouse_from_staging()
    return 0


def cmd_marts(_args: argparse.Namespace) -> int:
    from backend.marts.materialize import materialize_from_warehouse

    materialize_from_warehouse()
    return 0


def cmd_ml(args: argparse.Namespace) -> int:
    from backend.ml import run_stage

    run_stage(args.stage)
    return 0


def cmd_jobscore(_args: argparse.Namespace) -> int:
    from backend.ml.job_score import compute_job_scores

    compute_job_scores()
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    from backend.pipelines.flows import run_flow

    run_flow(args.flow)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="strata", description="strata data-platform CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create app/marts schema").set_defaults(fn=cmd_init_db)
    sub.add_parser("seed", help="generate seed dataset → warehouse → marts").set_defaults(fn=cmd_seed)

    sp = sub.add_parser("serve", help="run the FastAPI app")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("ingest", help="run a source connector")
    sp.add_argument("source", help="connector name, e.g. adzuna|common_crawl|so_survey|...")
    sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(fn=cmd_ingest)

    sub.add_parser("warehouse-build", help="build warehouse facts/dims from staging").set_defaults(fn=cmd_warehouse_build)
    sub.add_parser("marts-materialize", help="materialize marts to the serving DB").set_defaults(fn=cmd_marts)

    sp = sub.add_parser("ml", help="run a GPU pipeline stage")
    sp.add_argument("stage", help="skill_norm|entity_resolution|role_derivation|forecasting|job_score")
    sp.set_defaults(fn=cmd_ml)

    sub.add_parser("jobscore", help="recompute Job Score").set_defaults(fn=cmd_jobscore)

    sp = sub.add_parser("pipeline", help="run a Prefect flow")
    sp.add_argument("flow", help="full|ingest|warehouse|compute|marts")
    sp.set_defaults(fn=cmd_pipeline)

    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except NotImplementedError as e:
        log.warning("stage needs more setup before it can run: %s", e)
        return 0
    except ModuleNotFoundError as e:
        log.error("stage not available yet (%s). It lands in a later build phase.", e)
        return 2
    except KeyboardInterrupt:
        log.warning("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
