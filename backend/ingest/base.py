"""Connector base — every source connector is idempotent, resumable, and
**credential-graceful** (brief §6). Land to RAW immutably, clean to STAGING.

Inclusion rule (enforced per source): a source enters only if it joins on
country / skill / role / time AND adds a signal not already present. (strata is
ROLES-only — employer is never a join/product axis, only internal dedup plumbing.)

A connector that lacks its credentials (or isn't fully implemented yet) **skips
and flags** rather than halting the build. Status is one of:
  ok | skipped (missing creds) | scaffold (impl pending) | error
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest")


@dataclass
class IngestResult:
    source: str
    status: str                      # ok | skipped | scaffold | error
    rows_raw: int = 0
    rows_staging: int = 0
    note: str = ""
    extras: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.status}] {self.source}: raw={self.rows_raw} staging={self.rows_staging} {self.note}".strip()


class BaseConnector:
    name: str = "base"
    description: str = ""
    requires: tuple[str, ...] = ()   # settings attrs that must be truthy (e.g. api keys)
    joins_on: tuple[str, ...] = ()   # which keys it contributes (country/skill/role/time)
    adds_signal: str = ""            # the new signal it brings (inclusion rule)

    # ---- filesystem helpers (RAW immutable, STAGING cleaned) ----
    def raw_dir(self) -> Path:
        d = settings.raw_dir / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def staging_dir(self) -> Path:
        d = settings.staging_dir / self.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_raw_json(self, fname: str, obj) -> int:
        path = self.raw_dir() / fname
        path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        return len(obj) if hasattr(obj, "__len__") else 1

    # ---- credential gate ----
    def available(self) -> tuple[bool, str]:
        missing = [a for a in self.requires if not getattr(settings, a, None)]
        if missing:
            return False, "missing credentials: " + ", ".join(missing)
        return True, "ok"

    # ---- to implement per source ----
    def land_raw(self, limit: int | None = None) -> int:
        """Fetch from the source into RAW (immutable). Return rows landed."""
        raise NotImplementedError(f"{self.name}.land_raw not implemented yet")

    def build_staging(self) -> int:
        """Clean/type/dedupe RAW into STAGING parquet. Return staging rows."""
        raise NotImplementedError(f"{self.name}.build_staging not implemented yet")

    # ---- orchestration (graceful) ----
    def run(self, limit: int | None = None) -> IngestResult:
        ok, reason = self.available()
        if not ok:
            log.warning("⤼ skip %s — %s", self.name, reason)
            return IngestResult(self.name, "skipped", note=reason)
        try:
            raw = self.land_raw(limit)
            stg = self.build_staging()
            log.info("✓ %s — raw=%d staging=%d", self.name, raw, stg)
            return IngestResult(self.name, "ok", rows_raw=raw, rows_staging=stg)
        except NotImplementedError as e:
            log.warning("◻ scaffold %s — %s", self.name, e)
            return IngestResult(self.name, "scaffold", note=str(e))
        except Exception as e:  # never halt the whole build for one source
            log.error("✗ %s — %s", self.name, e)
            return IngestResult(self.name, "error", note=str(e))


class ScaffoldConnector(BaseConnector):
    """A registered source whose full extractor is pending. It documents the real
    plan and reports `scaffold` (or `skipped` if its creds are absent) so coverage
    is honest and the catalog is complete.
    """

    plan: str = ""

    def land_raw(self, limit: int | None = None) -> int:
        raise NotImplementedError(self.plan or "extractor pending")
