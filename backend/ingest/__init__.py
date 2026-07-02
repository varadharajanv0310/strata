"""Ingestion package + connector registry (brief §6).

Each source has its own connector; all are idempotent, resumable, and
credential-graceful. `run_connector(name)` runs one (or "all"); missing creds /
pending extractors **skip+flag** instead of halting.

Two connector shapes coexist:
  * class-based (`BaseConnector` subclasses, run via ``cls().run(limit)``) — the
    Common Crawl / Adzuna full extractors and the still-pending `ScaffoldConnector`
    catalog entries.
  * function-based **real** connectors — standalone modules that expose a
    module-level ``run(**kw)`` entrypoint (so_survey exposes ``fetch_and_aggregate``).
    These are the council-fleet sources; their dispatch takes **precedence** over any
    same-named scaffold so ``run_connector('so_survey')`` / ``('gov_projections')``
    reaches the real module, not a 'scaffold' stub.
"""
from __future__ import annotations

from typing import Callable

from backend.core.logging import get_logger
from backend.ingest.base import BaseConnector, IngestResult  # noqa: F401

log = get_logger("ingest")

_CONNECTORS: dict[str, type[BaseConnector]] | None = None

# Real, function-based connectors: name -> "module:function" of the module-level
# entrypoint. These shadow any scaffold of the same name (precedence in dispatch).
# Kept lazy (string spec, imported on demand) to avoid import cycles / heavy deps
# at package-import time.
_REAL_SPECS: dict[str, str] = {
    "so_survey": "backend.ingest.so_survey:fetch_and_aggregate",
    "gh_archive": "backend.ingest.gh_archive:run",
    "google_trends": "backend.ingest.google_trends:run",
    "stack_exchange": "backend.ingest.stack_exchange:run",
    "package_registries": "backend.ingest.package_registries:run",
    "gov_projections": "backend.ingest.gov_projections:run",
    "eures": "backend.ingest.eures:run",
    "bundesagentur": "backend.ingest.bundesagentur:run",
    "usajobs": "backend.ingest.usajobs:run",
    "ilostat": "backend.ingest.ilostat:run",
    "mycareersfuture": "backend.ingest.mycareersfuture:run",
    "cedefop_ovate": "backend.ingest.cedefop_ovate:run",
    "hn_hiring": "backend.ingest.hn_hiring:run",
    "remoteok": "backend.ingest.remoteok:run",
    "wikidata_occupations": "backend.ingest.wikidata_occupations:run",
    "arxiv": "backend.ingest.arxiv:run",
    "huggingface": "backend.ingest.huggingface:run",
    "wikipedia_pageviews": "backend.ingest.wikipedia_pageviews:run",
    "ambitionbox": "backend.ingest.ambitionbox:run",
}


def registry() -> dict[str, type[BaseConnector]]:
    """Lazy class-based registry (avoids import cycles with checkpoint/base)."""
    global _CONNECTORS
    if _CONNECTORS is None:
        from backend.ingest.adzuna import AdzunaConnector
        from backend.ingest.common_crawl import CommonCrawlConnector
        from backend.ingest.scaffold_sources import SCAFFOLD_CONNECTORS

        classes = [CommonCrawlConnector, AdzunaConnector, *SCAFFOLD_CONNECTORS]
        _CONNECTORS = {c.name: c for c in classes}
    return _CONNECTORS


def _resolve_real(name: str) -> Callable | None:
    """Import and return the module-level entrypoint for a real connector."""
    spec = _REAL_SPECS.get(name)
    if spec is None:
        return None
    import importlib

    mod_path, _, fn = spec.partition(":")
    module = importlib.import_module(mod_path)
    return getattr(module, fn)


def list_connectors() -> list[str]:
    """The full real catalog — class-based registry ∪ function-based real connectors."""
    return sorted(set(registry()) | set(_REAL_SPECS))


def _run_one(name: str, limit: int | None):
    """Dispatch one connector. Real (function-based) connectors take precedence
    over same-named scaffolds; class-based connectors run via ``cls().run(limit)``.
    """
    fn = _resolve_real(name)
    if fn is not None:
        res = fn()
        log.info("✓ %s — %s", name, res)
        return res
    return registry()[name]().run(limit)


def run_connector(name: str, limit: int | None = None):
    names = list_connectors()
    if name == "all":
        results = [_run_one(n, limit) for n in names]
        log.info("ingest summary: %s", "; ".join(str(r) for r in results))
        return results
    if name not in names:
        raise ValueError(f"unknown connector '{name}'. available: {', '.join(names)}")
    res = _run_one(name, limit)
    log.info("%s", res)
    return res
