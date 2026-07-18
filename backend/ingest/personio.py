"""Personio public job XML feeds — the single best German (DE) posting lever.

The data-expansion research's top German source: Personio is the dominant German-SME
HR/ATS, and every tenant exposes a public one-GET XML feed at
``https://{tenant}.jobs.personio.de/xml`` with COMPLETE job bodies (CDATA) plus a free
structured axis strata wants — ``seniority`` (entry-level/experienced/…), ``department``,
``office`` (German city), ``employmentType``, ``occupation``. Thousands of DE tenants →
directly attacks the country balance where the US-skewed startup-ATS fleet is weakest.

Live-verified: tecvia.jobs.personio.de/xml → 10 positions, full German descriptions,
Regensburg offices, seniority tags. One GET per tenant, no auth, no pagination.

``kind='advertised'`` (live job ads). Tech-filtered on title. country=DE (the .de host
is Germany; .com tenants can be AT/CH — kept as DE-family and honestly a small caveat).
ROLES-ONLY: ``subcompany`` is an internal dedup aid only. NO FABRICATION: source +
retrieval labelled per row. Bounded/resumable: fetch-once cache per tenant; the tenant
slug universe scales later via the CC index of jobs.personio.de (this seed is the proof).
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.ingest.tech_filter import classify as is_tech

log = get_logger("ingest.personio")

FEED = "https://{tenant}.jobs.personio.de/xml"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
THROTTLE = 1.5
MAX_DESC_CHARS = 8000
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Proof seed — real DE Personio tenants. The universe (thousands) is harvested from the
# CC index of jobs.personio.de later; these prove the parser end-to-end.
SEED_TENANTS = [
    "tecvia", "personio", "sennder", "gymondo", "mymuesli", "wefox",
    "grover", "forto", "staffbase", "usercentrics",
]


def _staging_dir():
    d = settings.staging_dir / "personio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pages_dir():
    d = _staging_dir() / "pages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _postings_file():
    return _staging_dir() / "postings.parquet"


def _first(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return m.group(1).strip() if m else ""


def _text(raw: str) -> str:
    """CDATA/HTML → readable text."""
    if not raw:
        return ""
    t = html.unescape(_TAG.sub(" ", raw))
    return _WS.sub(" ", t).strip()


def _parse_feed(xml: str, tenant: str) -> list[dict]:
    rows: list[dict] = []
    for m in re.finditer(r"<position>(.*?)</position>", xml, re.S):
        b = m.group(1)
        title = _text(_first(b, "name"))
        if not title or not is_tech(title):
            continue
        # concatenate every jobDescription/value body section
        desc = _text(" ".join(re.findall(r"<value>(.*?)</value>", b, re.S)))
        if len(desc) < 80:
            continue
        rows.append({
            "title": title, "description": desc[:MAX_DESC_CHARS], "country_code": "DE",
            "city": _text(_first(b, "office")), "department": _text(_first(b, "department")),
            "seniority": _text(_first(b, "seniority")),
            "employment_type": _text(_first(b, "employmentType")),
            "occupation": _text(_first(b, "occupation")),
            "employer": tenant,               # internal dedup aid only (roles-only)
            "kind": "advertised", "source": "personio",
            "posted_at": _text(_first(b, "createdAt")),
            "retrieved": time.strftime("%Y-%m-%d"),
        })
    return rows


def fetch_tenant(tenant: str, *, force: bool = False) -> list[dict]:
    cache = _pages_dir() / f"{tenant}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))
    rows: list[dict] = []
    try:
        req = urllib.request.Request(FEED.format(tenant=tenant), headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            xml = r.read().decode("utf-8", "replace")
        rows = _parse_feed(xml, tenant)
    except Exception as e:  # noqa: BLE001 — a dead/private tenant must not sink the run
        log.info("personio: tenant %s feed failed (%s)", tenant, e)
    cache.write_text(json.dumps(rows), encoding="utf-8")
    if rows:
        log.info("personio: %s → %d tech postings", tenant, len(rows))
    return rows


def build_staging(tenants: list[str] | None = None, *, max_tenants: int | None = None,
                  time_cap_s: float = 600.0, force: bool = False) -> dict:
    import pandas as pd

    ts = tenants or SEED_TENANTS
    if max_tenants is not None:
        ts = ts[:max_tenants]
    all_rows: list[dict] = []
    t0 = time.time()
    for t in ts:
        if time.time() - t0 > time_cap_s:
            break
        all_rows.extend(fetch_tenant(t, force=force))
        time.sleep(THROTTLE)
    total = 0
    seniorities: dict[str, int] = {}
    if all_rows:
        df = pd.DataFrame(all_rows).drop_duplicates(subset=["title", "employer"])
        df.to_parquet(_postings_file(), index=False)
        total = len(df)
        seniorities = df["seniority"].value_counts().to_dict()
    summary = {"tenants": len(ts), "postings": total, "seniority": seniorities}
    log.info("personio staging: %s", summary)
    return summary


def load_postings():
    p = _postings_file()
    if not p.exists():
        return None
    import pandas as pd
    return pd.read_parquet(p)


def run(max_tenants: int | None = None, time_cap_s: float = 600.0,
        force: bool = False, **kw) -> dict:
    """Land Personio DE postings. Connector entrypoint (bounded)."""
    return build_staging(max_tenants=max_tenants, time_cap_s=time_cap_s, force=force)


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(max_tenants=3), indent=2))
