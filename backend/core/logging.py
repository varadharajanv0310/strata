"""Structured-ish logging setup (stdlib, no extra deps).

Every pipeline stage logs a run summary (rows in/out, time, flags) through these
helpers (brief §10).
"""
from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager

from .config import settings

_CONFIGURED = False


def setup_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or settings.log_level).upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(lvl)
    # quiet noisy libs
    for noisy in ("httpx", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel("WARNING")
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


@contextmanager
def stage_timer(logger: logging.Logger, stage: str):
    """Context manager that logs stage start/finish + elapsed seconds."""
    logger.info("▶ %s — start", stage)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        logger.info("✓ %s — done in %.2fs", stage, dt)
