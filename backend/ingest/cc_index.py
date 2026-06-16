"""Common Crawl columnar index as the ATS *address book* — STUB (interface only).

The reframe from the brainstorm: stop fetching WARCs blind. The CC URL index is
Parquet on S3 (``s3://commoncrawl/cc-index/table/cc-main/warc/``); one
``DISTINCT url_host_name`` sweep over 90+ monthly crawls enumerates **every ATS board
the web has ever exposed** (~60–90k Greenhouse/Lever/Ashby/Workday tenants) — no
BuiltWith license, no guessing. CC's value here isn't the pages; it's the address
book that feeds ``ats.py``.

This defines the interface + the actual DuckDB-over-S3 query shape. The run itself is
deferred (it queries hundreds of GB of remote Parquet and is an ingestion job). The
build pass only ships the skeleton so it's "flip it on".
"""
from __future__ import annotations

from backend.core.logging import get_logger

log = get_logger("ingest.cc_index")

# host suffixes that identify an ATS board in the CC url_host_name column
ATS_HOST_SUFFIXES = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "myworkdayjobs.com",      # *.wdN.myworkdayjobs.com tenants
    "smartrecruiters.com",
)

# the cc-index columnar table (one partition per monthly crawl)
CC_INDEX_S3 = "s3://commoncrawl/cc-index/table/cc-main/warc/crawl={crawl}/subset=warc/"


def _slug_query(crawl: str) -> str:
    """The DuckDB SQL that turns one crawl's index into ATS host/slug rows.

    Sketch (data-dependent tuning deferred). Requires the httpfs + parquet extensions
    and AWS creds/region for the requester-pays bucket.
    """
    likes = " OR ".join(f"url_host_name LIKE '%{s}'" for s in ATS_HOST_SUFFIXES)
    return f"""
        SELECT DISTINCT url_host_name
        FROM read_parquet('{CC_INDEX_S3.format(crawl=crawl)}*.parquet')
        WHERE ({likes})
          AND fetch_status = 200
    """  # TODO(ingestion): slug extraction from url_host_name/url_path per vendor


def enumerate_ats_slugs(crawls: list[str], host_suffixes=ATS_HOST_SUFFIXES) -> dict[str, list[str]]:
    """STUB: enumerate {vendor: [slug, ...]} from the CC columnar index.

    TODO(ingestion): for each crawl, ``duckdb.sql(_slug_query(crawl))`` against S3
    (httpfs, region us-east-1), union DISTINCT hosts, parse host→(vendor, slug). This
    is a real remote-query run (hundreds of GB scanned) — deferred. The slug flywheel
    (CT logs via crt.sh, Crunchbase portfolio probing, ATS sibling-company leaks) then
    compounds this seed set; those are separate connectors.
    """
    raise NotImplementedError(
        "CC-index slug enumeration deferred (remote S3 Parquet scan = an ingestion run). "
        f"Query ready for {len(crawls)} crawls × {len(host_suffixes)} ATS hosts.")
