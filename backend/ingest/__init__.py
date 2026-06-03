"""Ingestion package + connector registry (brief §6).

Each source has its own connector; all are idempotent, resumable, and
credential-graceful. `run_connector(name)` runs one (or "all"); missing creds /
pending extractors **skip+flag** instead of halting.
"""
from __future__ import annotations

from backend.core.logging import get_logger
from backend.ingest.base import BaseConnector, IngestResult  # noqa: F401

log = get_logger("ingest")

_CONNECTORS: dict[str, type[BaseConnector]] | None = None


def registry() -> dict[str, type[BaseConnector]]:
    """Lazy registry (avoids import cycles with checkpoint/base)."""
    global _CONNECTORS
    if _CONNECTORS is None:
        from backend.ingest.adzuna import AdzunaConnector
        from backend.ingest.common_crawl import CommonCrawlConnector
        from backend.ingest.scaffold_sources import SCAFFOLD_CONNECTORS

        classes = [CommonCrawlConnector, AdzunaConnector, *SCAFFOLD_CONNECTORS]
        _CONNECTORS = {c.name: c for c in classes}
    return _CONNECTORS


def list_connectors() -> list[str]:
    return sorted(registry().keys())


def run_connector(name: str, limit: int | None = None):
    reg = registry()
    if name == "all":
        results = [reg[n]().run(limit) for n in sorted(reg)]
        log.info("ingest summary: %s", "; ".join(str(r) for r in results))
        return results
    if name not in reg:
        raise ValueError(f"unknown connector '{name}'. available: {', '.join(sorted(reg))}")
    res = reg[name]().run(limit)
    log.info("%s", res)
    return res
