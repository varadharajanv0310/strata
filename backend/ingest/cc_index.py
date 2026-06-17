"""Common Crawl index as the ATS *address book* — enumerate board slugs.

The reframe from the brainstorm: stop fetching WARCs blind. The CC URL index already
knows every ATS board the web has exposed. One pass over the index for the ATS host
patterns yields the **slug universe** (the list of boards to poll) — not the postings
themselves (those come from the live board APIs in ``ats.py``).

We **reuse the proven index access in ``common_crawl.py``** (CDX API → columnar
``cluster.idx`` fallback, with bounded retries + backoff) rather than reinventing S3
access — that machinery already landed real captures and survived CC's rate-limit
blocks. We just point it at the ATS host patterns and extract the board slug from
each captured URL.

Bounded by design: one recent monthly crawl, a per-host slug cap, and a wall-clock
budget — enumeration must never hang or balloon. Returns ``{vendor: [slug, ...]}``.
Historical + US/English-skewed (that's the CC reality) — the *blind* arm of the proof.
"""
from __future__ import annotations

import time
from urllib.parse import parse_qs, urlsplit

from backend.core.logging import get_logger

log = get_logger("ingest.cc_index")

# host patterns we enumerate, by vendor. greenhouse's JSON-LD is dead (SPA) but its
# board *slugs* are still all over the index, and we poll the live JSON API (ats.py),
# so it stays. workday tenants are captured for completeness (no parser yet).
ENUM_HOST_PATTERNS: dict[str, str] = {
    # boards.greenhouse.io now 301s to job-boards.greenhouse.io (old host only has
    # robots.txt in the index); the new host carries the real /{slug}/jobs/... paths.
    "greenhouse": "job-boards.greenhouse.io",
    "lever": "jobs.lever.co",
    "ashby": "jobs.ashbyhq.com",
    "workday": "*.myworkdayjobs.com",
}

# the production-scale path (documented; not used for the bounded proof): a DuckDB
# DISTINCT url_host_name sweep over the columnar cc-index Parquet on S3. Left as a
# reference — the bounded proof uses the lighter CDX path below.
ATS_HOST_SUFFIXES = ("boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
                     "myworkdayjobs.com")
CC_INDEX_S3 = "s3://commoncrawl/cc-index/table/cc-main/warc/crawl={crawl}/subset=warc/"


def _slug_from_url(vendor: str, url: str) -> str | None:
    """Extract the board slug from a captured ATS URL.

    greenhouse/lever/ashby: first path segment (boards.greenhouse.io/{slug}/...).
    workday: the tenant subdomain ({tenant}.wdN.myworkdayjobs.com).
    """
    try:
        parts = urlsplit(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    host, path = (parts.netloc or "").lower(), parts.path or ""
    if vendor == "workday":
        labels = host.split(".")
        return labels[0] if labels and labels[0] not in ("www", "") else None
    segs = [s for s in path.split("/") if s]
    # greenhouse is usually captured as the embed widget: /embed/job_board?for={slug}
    if vendor == "greenhouse" and (not segs or segs[0] in ("embed", "embed_job", "")):
        forq = parse_qs(parts.query or "").get("for")
        return forq[0].lower() if forq and forq[0] else None
    if not segs:
        return None
    slug = segs[0].lower()
    # filter obvious non-slugs (api paths, assets, embed widgets)
    if slug in ("embed", "api", "v1", "v0", "assets", "static", "favicon.ico", "robots.txt"):
        return None
    return slug or None


def enumerate_ats_slugs(
    crawls: list[str] | None = None,
    *,
    per_host_limit: int = 200,
    host_patterns: dict[str, str] | None = None,
    time_cap_s: float = 600.0,
) -> dict[str, list[str]]:
    """Enumerate ``{vendor: [slug, ...]}`` from the CC index for the ATS hosts.

    Reuses ``CommonCrawlConnector._resolve_index`` (CDX → columnar fallback). Bounded:
    one recent crawl, ``per_host_limit`` slugs/host, ``time_cap_s`` wall-clock. Returns
    ``{}`` (logged) if the CC index is unreachable — the proof then leans on the
    curated arm and reports the blind arm as unavailable (honest, not a crash).
    """
    from backend.ingest.common_crawl import CommonCrawlConnector

    patterns = host_patterns or ENUM_HOST_PATTERNS
    conn = CommonCrawlConnector()
    crawl_ids = conn._recent_crawls(crawls, 1)  # ONE recent crawl — bounded
    if not crawl_ids:
        log.warning("cc_index: no CC crawl resolved (index unreachable) — blind arm unavailable")
        return {}
    crawl = crawl_ids[0]
    log.info("cc_index: enumerating ATS slugs from crawl %s (cap %d/host, %.0fs budget)",
             crawl, per_host_limit, time_cap_s)

    out: dict[str, set[str]] = {v: set() for v in patterns}
    t0 = time.time()
    for vendor, domain in patterns.items():
        if time.time() - t0 > time_cap_s:
            log.warning("cc_index: time cap %.0fs hit — stopping after %s", time_cap_s, vendor)
            break
        try:
            rows = conn._resolve_index(crawl, domain, per_host_limit) or []
        except Exception as e:  # noqa: BLE001 — index hiccup must not crash enumeration
            log.warning("cc_index: %s (%s) resolve failed: %s", vendor, domain, e)
            rows = []
        for r in rows:
            slug = _slug_from_url(vendor, r.get("url", ""))
            if slug:
                out[vendor].add(slug)
            if len(out[vendor]) >= per_host_limit:
                break
        log.info("cc_index: %-10s %-22s -> %d slugs  (%.0fs elapsed)",
                 vendor, domain, len(out[vendor]), time.time() - t0)

    result = {v: sorted(s) for v, s in out.items()}
    total = sum(len(s) for s in result.values())
    log.info("cc_index: enumerated %d ATS slugs across %d vendors from crawl %s",
             total, len([v for v in result if result[v]]), crawl)
    return result


if __name__ == "__main__":  # pragma: no cover — manual bounded smoke
    import json
    res = enumerate_ats_slugs(per_host_limit=30, time_cap_s=120)
    print(json.dumps({v: s[:8] for v, s in res.items()}, indent=2))
    print({v: len(s) for v, s in res.items()})
