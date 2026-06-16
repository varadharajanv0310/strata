"""Compile the serving marts to range-partitioned Parquet for the browser.

The "how did a student build this" engineering flex: the browser *is* the warehouse.
We compile the SQLite serving marts to compact Parquet (the big role×country table
range-partitioned by country), ship them over a CDN/static host, and DuckDB-WASM
queries them in the tab via HTTP range requests — the /data SQL console runs real
OLAP client-side, no server, and the honesty ethos becomes un-fakeable (a skeptic
re-derives any number themselves).

This exporter reads the marts READ-ONLY and writes Parquet to a target dir. It does
NOT publish to the live site; populating ``public/data/marts`` for the console is a
deliberate, separate act.
"""
from __future__ import annotations

from pathlib import Path

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("marts.export_parquet")

# served tables to export (skipped silently if a table doesn't exist yet)
_TABLES = [
    "mart_country", "mart_family", "mart_role", "mart_role_skill", "mart_role_ladder",
    "mart_role_country", "mart_market_pulse", "mart_meta",
    "mart_role_alias", "mart_provenance",
]


def export_marts(out_dir: str | Path | None = None, *, partition: bool = True) -> dict:
    """Export marts → Parquet. Returns {table: row_count}. ``mart_role_country`` is
    also written hive-partitioned by ``country_code`` (range-partition for the
    per-country queries the Role Dashboard makes)."""
    import pandas as pd
    from sqlalchemy import create_engine, inspect

    # default: the static path the /data console fetches from (public/data/marts)
    default = settings.data_path.parent.parent / "public" / "data" / "marts"
    out = Path(out_dir) if out_dir else default
    out.mkdir(parents=True, exist_ok=True)
    engine = create_engine(settings.resolved_database_url)
    insp = inspect(engine)
    present = set(insp.get_table_names())

    counts: dict[str, int] = {}
    with engine.connect() as con:
        for t in _TABLES:
            if t not in present:
                continue
            df = pd.read_sql(f"SELECT * FROM {t}", con)
            df.to_parquet(out / f"{t}.parquet", index=False)
            counts[t] = len(df)
            if partition and t == "mart_role_country" and "country_code" in df.columns and len(df):
                pdir = out / "mart_role_country_by_country"
                pdir.mkdir(exist_ok=True)
                df.to_parquet(pdir, partition_cols=["country_code"], index=False)
    log.info("exported %d mart tables to %s: %s", len(counts), out, counts)
    return counts


if __name__ == "__main__":  # pragma: no cover
    import json
    print(json.dumps(export_marts(), indent=2))
