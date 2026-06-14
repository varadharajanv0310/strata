"""Provenance manifest — lineage threaded from raw staging through the warehouse.

For every source in ``dim_source`` we compute the tuple the served layer surfaces
per-number:

  * ``snapshot_hash``     — sha1 over the source's staging files (name+size+mtime),
                            a stable identity for "which bytes produced this number",
  * ``transform_version`` — the build-code version that fused them,
  * ``row_count``         — warehouse fact rows attributable to the source,
  * ``as_of``             — latest mtime of the staged snapshot.

A back-test or a salary number pinned to a snapshot_hash is reproducible — that's
the difference between "trust me" and "verify me". Hashes are best-effort: when a
source's staging path can't be located the hash is '' (honest, not faked).
"""
from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import duckdb

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("warehouse.provenance")

# bumped whenever the staging→warehouse fusion logic changes shape
TRANSFORM_VERSION = "2026.06.25"

# source_id / source_name keyword -> staging subdirectory
_SOURCE_DIR_HINTS = [
    ("so", "so_survey"), ("stack overflow", "so_survey"), ("survey", "so_survey"),
    ("h1b", "h1b"), ("h-1b", "h1b"), ("oflc", "h1b"), ("perm", "h1b"), ("dol", "h1b"),
    ("adzuna", "adzuna"),
    ("gh", "gh_archive"), ("github", "gh_archive"), ("archive", "gh_archive"),
    ("trend", "google_trends"), ("google", "google_trends"),
    ("crawl", "common_crawl"), ("commoncrawl", "common_crawl"),
    ("world bank", "worldbank"), ("worldbank", "worldbank"), ("ppp", "worldbank"),
    ("bls", "baselines"), ("ons", "baselines"), ("eurostat", "baselines"),
    ("mom", "baselines"), ("statcan", "baselines"), ("baseline", "baselines"),
    ("onet", "onet"), ("o*net", "onet"),
]

# fact tables carrying a source_id (for row-count attribution)
_FACT_TABLES = ["fact_salary_person", "fact_salary_job", "fact_demand", "fact_interest"]


def _staging_dir_for(source_id: str, source_name: str) -> Path | None:
    blob = f"{source_id} {source_name}".lower()
    for kw, sub in _SOURCE_DIR_HINTS:
        if kw in blob:
            p = settings.staging_dir / sub
            if p.exists():
                return p
    return None


def _hash_dir(path: Path) -> tuple[str, str]:
    """(sha1 over sorted (relpath, size, mtime), latest-mtime ISO). '' if empty."""
    h = hashlib.sha1()
    latest = 0.0
    files = sorted(p for p in path.rglob("*") if p.is_file())
    if not files:
        return "", ""
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(str(f.relative_to(path)).encode())
        h.update(str(st.st_size).encode())
        h.update(str(int(st.st_mtime)).encode())
        latest = max(latest, st.st_mtime)
    as_of = dt.datetime.fromtimestamp(latest, dt.timezone.utc).isoformat() if latest else ""
    return h.hexdigest(), as_of


def _row_counts_by_source(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tbl in _FACT_TABLES:
        try:
            for sid, n in con.execute(
                f"SELECT source_id, count(*) FROM {tbl} GROUP BY source_id"
            ).fetchall():
                if sid is not None:
                    counts[sid] = counts.get(sid, 0) + int(n)
        except duckdb.Error:
            continue
    return counts


def collect_provenance(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Build the provenance manifest from an open warehouse connection."""
    try:
        sources = con.execute(
            "SELECT source_id, source_name, default_kind FROM dim_source"
        ).fetchall()
    except duckdb.Error:
        log.warning("dim_source absent — empty provenance manifest")
        return []
    counts = _row_counts_by_source(con)
    manifest: list[dict] = []
    for source_id, source_name, kind in sources:
        sdir = _staging_dir_for(source_id, source_name or "")
        snap, as_of = _hash_dir(sdir) if sdir else ("", "")
        manifest.append({
            "source_id": source_id,
            "source_name": source_name or source_id,
            "kind": kind or "",
            "snapshot_hash": snap,
            "transform_version": TRANSFORM_VERSION,
            "row_count": counts.get(source_id, 0),
            "as_of": as_of,
        })
    log.info("provenance manifest: %d sources, %d with snapshot hashes",
             len(manifest), sum(1 for m in manifest if m["snapshot_hash"]))
    return manifest
