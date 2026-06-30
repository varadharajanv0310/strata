"""Relative search **interest** per role × country × time — the Job-Score interest axis.

Google Trends has no official API; we use the unofficial ``pytrends`` client, which
scrapes the same endpoint the website calls. That endpoint **rate-limits hard** (HTTP
429) and the numbers it returns are *relative*, not absolute: within a single request
the series is normalised to 0–100 against its own peak. So an interest value here means
"how this term's search interest moved over time / how it compares between countries in
the SAME request" — never an absolute volume. We label every record ``relative=True``.

To get a value that is comparable **across roles within a country**, we batch up to 5
roles per request (pytrends' max) and let Google normalise them against each other; the
per-request peak role gets 100, the rest are scaled relative to it. We then read the
**latest-year** mean for each role in that batch. Country axis is the request ``geo``.

Contract (brief §10): every (country, batch) request is cached under
``staging/google_trends/`` — that cache **is** the checkpoint. A killed/throttled run
resumes by skipping batches already on disk. Google blocking is expected, not fatal:
bounded backoff on 429, then we checkpoint whatever we have and STOP that unit, moving on.
Partial real interest beats none.
"""
from __future__ import annotations

import json
import random
import time

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.seed import COUNTRIES, ROLE_DEFS

log = get_logger("ingest.google_trends")

# our 7 markets → Google Trends geo codes (ISO-3166-1 alpha-2, == our codes)
GEO = {c["code"]: c["code"] for c in COUNTRIES}

# Representative search TERM per role. Plain role names like "Software Engineer" are
# too generic and collide; a representative skill/title gives a cleaner career-interest
# signal. Chosen to be the keyword a person *searching about this career* would type.
ROLE_TERM = {
    "ml-eng": "Machine Learning Engineer",
    "swe": "Software Engineer",
    "data-eng": "Data Engineer",
    "frontend": "Frontend Developer",
    "backend": "Backend Developer",
    "data-sci": "Data Scientist",
    "devops": "DevOps Engineer",
    "sre": "Site Reliability Engineer",
    "security": "Cybersecurity Engineer",
    "cloud-arch": "Cloud Architect",
    "pm": "Product Manager",
    "ux": "UX Designer",
    "mobile": "Mobile Developer",
    "data-analyst": "Data Analyst",
    "eng-mgr": "Engineering Manager",
    "qa": "QA Engineer",
}

BATCH_SIZE = 5          # pytrends hard max terms per interest_over_time request
TIMEFRAME = "today 5-y"  # weekly series over ~5y; we read the latest-year mean
MAX_RETRIES = 6          # bounded — Google blocks are expected, give up gracefully
BASE_BACKOFF = 10.0      # seconds; exponential. 429 from Google can need a long cooldown
MAX_BACKOFF = 150.0      # cap the exponential so a 429 storm can't hang one batch forever
THROTTLE = 11.0          # base spacing between requests (jittered ±50%) — proactive 429 avoidance
EMPTY_RETRIES = 2        # an empty frame is usually a SOFT block, not "no data" — retry a couple times
# a realistic desktop UA makes Google's bot-heuristics far less likely to 429 us
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _staging_dir():
    d = settings.staging_dir / "google_trends"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _batches(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _batch_path(country_code: str, batch_idx: int):
    return _staging_dir() / f"{country_code}_b{batch_idx}.json"


def _new_pytrends():
    # Lazy import so the module loads even if pytrends isn't installed yet.
    from pytrends.request import TrendReq
    # CRITICAL: retries=0 / backoff_factor=0. pytrends forwards these to urllib3's
    # Retry, which on a 429 silently retries AND honours Google's (often very long)
    # Retry-After header *inside* the call — stacking on our backoff and hanging the
    # process for minutes. We want all retry/backoff control here, with the exception
    # surfacing immediately so _fetch_batch can decide. timeout caps a single attempt.
    return TrendReq(hl="en-US", tz=0, timeout=(10, 25), retries=0, backoff_factor=0,
                    requests_args={"headers": {"User-Agent": _UA}})


def _fetch_batch(pytrends, terms: list[str], geo: str) -> dict | None:
    """One interest_over_time request for up to 5 terms in one geo.

    Returns {term: latest_year_mean_0_100} or None if Google blocked us past our
    retry budget (caller treats None as "stop this unit, keep what's cached").
    """
    empties = 0
    for attempt in range(MAX_RETRIES):
        try:
            pytrends.build_payload(terms, timeframe=TIMEFRAME, geo=geo)
            df = pytrends.interest_over_time()
            if df is None or df.empty:
                # an empty frame from Google is usually a SOFT block, not real "no data" —
                # back off + rebuild the session a couple of times before believing it.
                empties += 1
                if empties > EMPTY_RETRIES:
                    log.warning("trends %s %s — empty after %d tries (treating as no data)", geo, terms, empties)
                    return {}
                wait = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** (empties - 1))) + random.uniform(0, 4)
                log.warning("trends %s %s — empty frame (likely soft block), retry %d/%d in %.0fs",
                            geo, terms, empties, EMPTY_RETRIES, wait)
                time.sleep(wait)
                try:
                    pytrends = _new_pytrends()
                except Exception:  # noqa: BLE001
                    pass
                continue
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])
            # latest-year mean per term (last ~52 weekly points), rounded 0-100.
            tail = df.tail(52)
            out: dict[str, float] = {}
            for t in terms:
                if t in tail.columns:
                    out[t] = round(float(tail[t].mean()), 1)
            return out
        except Exception as e:  # noqa: BLE001 — connector must be resilient
            msg = str(e)
            is_429 = "429" in msg or "rate" in msg.lower() or "too many" in msg.lower()
            if attempt == MAX_RETRIES - 1:
                log.error("trends %s %s — giving up after %d tries: %s",
                          geo, terms, MAX_RETRIES, msg)
                return None
            wait = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** attempt)) + random.uniform(0, 4)
            log.warning("trends %s %s — %s (attempt %d/%d), backoff %.0fs",
                        geo, terms, "429/block" if is_429 else "error",
                        attempt + 1, MAX_RETRIES, wait)
            time.sleep(wait)
            # rebuild the client on block — a fresh session sometimes clears the throttle
            if is_429:
                try:
                    pytrends = _new_pytrends()
                except Exception:  # noqa: BLE001
                    pass
    return None


def fetch_all(throttle: float = THROTTLE, max_units: int | None = None,
              latest_year: int | None = None) -> dict:
    """Pull relative interest for every (country, role-batch); cache + resume.

    One request per (country, batch-of-5-roles). Roles in a batch are normalised
    against each other by Google, giving cross-role-within-country comparability.
    ``max_units`` caps how many *new* batch requests we make this run (smoke / budget).
    Returns a summary; the per-batch caches + the aggregate JSON are the checkpoint.
    """
    sd = _staging_dir()
    if latest_year is None:
        latest_year = time.gmtime().tm_year
    role_ids = [d["id"] for d in ROLE_DEFS]
    batches = list(_batches(role_ids, BATCH_SIZE))

    pytrends = None
    fetched = cached = blocked = empty_terms = 0
    for co in COUNTRIES:
        cc = co["code"]
        for bi, batch in enumerate(batches):
            out_path = _batch_path(cc, bi)
            if out_path.exists():
                cached += 1
                continue
            if max_units is not None and fetched >= max_units:
                break
            if pytrends is None:
                pytrends = _new_pytrends()
                time.sleep(random.uniform(2.0, 5.0))   # gentle warmup before the first request
            terms = [ROLE_TERM[rid] for rid in batch]
            res = _fetch_batch(pytrends, terms, GEO[cc])
            if res is None:
                # Google blocked us past budget — checkpoint nothing for this unit, STOP.
                blocked += 1
                log.warning("trends %s batch %d blocked — skipping (resume later)", cc, bi)
                continue
            records = []
            for rid, term in zip(batch, terms):
                val = res.get(term)
                if val is None:
                    empty_terms += 1
                    continue
                records.append({
                    "role_id": rid, "country_code": cc, "year": latest_year,
                    "interest": val, "relative": True, "term": term,
                    "source": "Google Trends", "timeframe": TIMEFRAME,
                })
            out_path.write_text(json.dumps(records), encoding="utf-8")
            fetched += 1
            got = [f"{r['role_id']}={r['interest']}" for r in records]
            log.info("trends %s batch %d · %s", cc, bi, " ".join(got) or "(no terms)")
            time.sleep(throttle + random.uniform(0, throttle * 0.5))
        else:
            continue
        # inner loop broke on max_units — stop outer loop too
        if max_units is not None and fetched >= max_units:
            break

    records = _aggregate()
    summary = {
        "fetched_batches": fetched, "cached_batches": cached,
        "blocked_batches": blocked, "empty_terms": empty_terms,
        "total_batches": len(batches) * len(COUNTRIES),
        "rows": len(records), "latest_year": latest_year,
    }
    log.info("google_trends summary: %s", summary)
    return summary


def _aggregate() -> list:
    """Fuse all per-batch caches into the single aggregate the fusion step reads."""
    sd = _staging_dir()
    rows: list = []
    seen = set()
    for f in sorted(sd.glob("*_b*.json")):
        try:
            for r in json.loads(f.read_text(encoding="utf-8")):
                key = (r["role_id"], r["country_code"], r["year"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(r)
        except Exception:  # noqa: BLE001
            continue
    out = sd / "interest.json"
    out.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def load_interest() -> list:
    """Return the aggregate interest rows from staging (re-fuses caches if needed)."""
    p = _staging_dir() / "interest.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return _aggregate()


def run(throttle: float = THROTTLE, max_units: int | None = None) -> dict:
    """Entrypoint the orchestrator (collect_all) calls at full scale."""
    return fetch_all(throttle=throttle, max_units=max_units)


if __name__ == "__main__":
    # tiny smoke run: a couple of batches only
    print(run(max_units=2))
