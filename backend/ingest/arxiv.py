"""arXiv OAI-PMH — the **EARLIEST leading emergence indicator**: research submission
velocity per CS subfield, the signal that fires before a skill ever shows up in a job
posting.

By the time "diffusion model" or "retrieval-augmented generation" appears in a job
description, the underlying research wave has already been cresting on arXiv for a year
or more. Counting cs.* submissions per category per month gives strata a *leading*
adoption curve — research velocity is the upstream tributary that job demand flows from
months later. This connector lands one row per (cs category, month): the count of
submissions in that subfield that month. The category (e.g. ``cs.LG``, ``cs.CL``,
``cs.CR``, ``cs.AI``) is the join key on the **skill** axis; the warehouse fuse later
crosswalks each arXiv category to strata's canonical skills.

GRAIN: skill(=cs category) × period(YYYY-MM). GLOBAL — research has no real geography
(authors are worldwide, arXiv exposes no reliable country), so every row lands
``country=""`` rather than faking a geo split across our 7 countries. ROLES-ONLY: there
is no employer/org concept here at all — only subfield × month × count.

OBTAINED FROM arXiv's public OAI-PMH endpoint
(``http://export.arxiv.org/oai2?verb=ListRecords&metadataPrefix=arXiv&set=cs``), the
sanctioned bulk-metadata interface (no key). It is RATE-LIMITED and paginates via an
opaque ``resumptionToken``; arXiv asks clients to be polite (one request every few
seconds) and may answer a page with HTTP 503 + ``Retry-After`` under load. This module
is therefore deliberately defensive: a flushing heartbeat every N pages, a ``time_cap_s``
and ``max_pages`` bound, ``Retry-After`` honouring, and per-request try/except so a
single bad page logs and is skipped rather than sinking the run. It will NOT be run in
this pass — but the code is real and runnable.

FEEDS: ``fact_skill_adoption`` (research-velocity signal).

→ staging/arxiv/velocity.json: [{skill(category), period(YYYY-MM), n, country:""}]
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.arxiv")

# OAI-PMH base + the bulk arXiv metadata format (carries the full category list per paper).
BASE = "http://export.arxiv.org/oai2"
SET = "cs"                       # the Computer Science set; subfields are the cs.* categories
PREFIX = "arXiv"
HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}

# OAI-PMH XML namespaces. The container is the OAI-PMH ns; the record body is arXiv's own.
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arxiv": "http://arxiv.org/OAI/arXiv/",
}

# Polite default: arXiv recommends spacing requests out; honour Retry-After on 503.
SLEEP_S = 3.0
RETRY_AFTER_DEFAULT = 30


def _staging_dir():
    d = settings.staging_dir / "arxiv"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "velocity.json"


def _raw_file():
    """Cache of the raw (category, YYYY-MM) tuples harvested, so a re-build is cheap."""
    return _staging_dir() / "_records.json"


def _request(params: dict, timeout: int = 90) -> str:
    """One OAI-PMH GET → XML text. Honours HTTP 503 + Retry-After (one retry). Raises
    on hard failure so the caller's try/except can log+skip that page."""
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 503:
            wait = int(e.headers.get("Retry-After", RETRY_AFTER_DEFAULT) or RETRY_AFTER_DEFAULT)
            log.warning("arxiv: 503 throttled — sleeping %ss then one retry", wait)
            time.sleep(min(wait, 120))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        raise


def _parse_page(xml_text: str) -> tuple[list[tuple[str, str]], str | None]:
    """Parse one ListRecords page → ([(category, YYYY-MM), ...], resumptionToken|None).

    Each record can carry several space-separated categories (primary + cross-lists); we
    count the paper once per category it touches, so a cs.LG∩cs.CL paper lifts both
    subfields' velocity. The month comes from the arXiv record's <created> date."""
    out: list[tuple[str, str]] = []
    root = ET.fromstring(xml_text)

    # OAI-level error (e.g. noRecordsMatch / badResumptionToken) — surface it, no records.
    err = root.find(".//oai:error", NS)
    if err is not None:
        log.warning("arxiv: OAI error code=%s msg=%s",
                    err.get("code"), (err.text or "").strip())

    for rec in root.findall(".//oai:record", NS):
        # Skip deleted records (header carries status="deleted", no metadata body).
        header = rec.find("oai:header", NS)
        if header is not None and header.get("status") == "deleted":
            continue
        meta = rec.find(".//arxiv:arXiv", NS)
        if meta is None:
            continue
        cats = (meta.findtext("arxiv:categories", default="", namespaces=NS) or "").split()
        created = (meta.findtext("arxiv:created", default="", namespaces=NS) or "").strip()
        period = created[:7] if len(created) >= 7 else ""   # 'YYYY-MM-DD' → 'YYYY-MM'
        if not period:
            continue
        for cat in cats:
            if cat.startswith("cs."):                       # CS subfields only
                out.append((cat, period))

    token_el = root.find(".//oai:resumptionToken", NS)
    token = token_el.text.strip() if token_el is not None and token_el.text else None
    return out, (token or None)


def fetch_records(
    force: bool = False,
    from_date: str | None = None,
    until_date: str | None = None,
    max_pages: int = 10_000,
    time_cap_s: float = 600.0,
    heartbeat_every: int = 10,
    sleep_s: float = SLEEP_S,
) -> list[list]:
    """Harvest raw (category, YYYY-MM) tuples from the cs set via OAI-PMH paging.

    The raw cache (``_records.json``) IS the checkpoint: if present and not ``force`` we
    reload it. ``from_date``/``until_date`` (``YYYY-MM-DD``) narrow the harvest window —
    leave both None for the full set. Bounded by ``max_pages`` + ``time_cap_s`` so a run
    can land a partial harvest and resume later. NETWORK-GRACEFUL: a bad page logs+breaks
    with whatever was gathered, never crashes."""
    raw = _raw_file()
    if raw.exists() and not force:
        return json.loads(raw.read_text(encoding="utf-8"))

    records: list[tuple[str, str]] = []
    # First page carries the verb + set + prefix + optional window; later pages carry ONLY
    # the resumptionToken (OAI-PMH rule — mixing the two is a protocol error).
    params: dict = {"verb": "ListRecords", "metadataPrefix": PREFIX, "set": SET}
    if from_date:
        params["from"] = from_date
    if until_date:
        params["until"] = until_date

    t0 = time.time()
    token: str | None = None
    for page in range(1, max_pages + 1):
        if time.time() - t0 > time_cap_s:
            log.warning("arxiv: time cap %ss hit at page %d — landing partial", time_cap_s, page)
            break
        try:
            xml_text = _request(token and {"verb": "ListRecords", "resumptionToken": token} or params)
        except Exception as e:  # noqa: BLE001 — one bad page must not sink the harvest
            log.warning("arxiv: page %d fetch failed (%s) — stop, land partial", page, e)
            break

        try:
            page_rows, token = _parse_page(xml_text)
        except ET.ParseError as e:
            log.warning("arxiv: page %d parse failed (%s) — stop, land partial", page, e)
            break

        records.extend(page_rows)
        if page % heartbeat_every == 0 or token is None:
            print(f"[arxiv] page {page}: +{len(page_rows)} cat-rows "
                  f"(total {len(records)}); more={token is not None}", flush=True)

        if token is None:               # exhausted — full harvest complete
            break
        time.sleep(sleep_s)             # be polite between pages

    # Land raw tuples (as lists — JSON has no tuple) so build_staging is a cheap re-run.
    out = [[c, p] for c, p in records]
    if out:
        raw.write_text(json.dumps(out), encoding="utf-8")
    log.info("arxiv: harvested %d (category,month) rows across %d categories",
             len(out), len({c for c, _ in out}))
    return out


def build_staging(records: list[list] | None = None) -> list[dict]:
    """Aggregate raw (category, month) tuples → velocity rows and land velocity.json.

    Final shape: ``[{"skill": cat, "period": "YYYY-MM", "n": count, "country": ""}]``.
    GLOBAL signal → ``country=""`` on every row (no fake geography)."""
    records = records if records is not None else fetch_records()
    counts: Counter = Counter((c, p) for c, p in records)
    rows = [
        {"skill": cat, "period": period, "n": n, "country": ""}
        for (cat, period), n in sorted(counts.items())
    ]
    f = _staging_file()
    if rows:
        f.write_text(json.dumps(rows), encoding="utf-8")
    log.info("arxiv velocity: %d (category×month) rows, %d categories, %d months",
             len(rows), len({r["skill"] for r in rows}), len({r["period"] for r in rows}))
    return rows


def load_velocity() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Harvest arXiv cs.* submission velocity → fact_skill_adoption staging. Entrypoint.

    Passes ``fetch_records`` kwargs through (``force``, ``from_date``, ``until_date``,
    ``max_pages``, ``time_cap_s`` …); always re-aggregates the staging file."""
    if kw.get("force") or not _staging_file().exists():
        records = fetch_records(**kw)
        rows = build_staging(records)
    else:
        rows = load_velocity()
    return {
        "rows": len(rows),
        "categories": len({r["skill"] for r in rows}),
        "months": len({r["period"] for r in rows}),
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
