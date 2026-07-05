"""Robert Half Salary Guide — US + CA tech role salary ESTIMATES by experience tier.

Robert Half publishes an annual Salary Guide whose Technology section lists ~70-80
curated tech roles per market (including emerging AI titles: AI Architect, AI/ML
Engineer, RPA Engineer, ML Developer…) each with three salary figures. Crucially,
those three figures are **experience TIERS, not statistical percentiles** — the guide
defines them as: Low = "new to the role / limited experience", Mid = "moderate
experience", High = "extensive experience and advanced skills". So we honestly emit
one row per (role, tier) with ``level_label`` carrying the native tier string and
``yoe=null`` — the guide gives an experience *band*, never a years number, so we never
invent years from a tier (charter rule).

Where the numbers live (recon-verified 2026-07-05): the salary guide landing page for
each market (``/us/en/…`` and ``/ca/en/…``) server-renders a JSON blob inside a JS
string literal — a ``relatedPositions`` array of ``{jobCategory, positions:[{jobTitle,
salaryLow, salaryHigh, salaryMidpoint, url}]}``. The blob is escaped as a JS string
(quotes as ``\\x22``, dashes as ``\\u002D``, slashes as ``\\/``); we de-escape then
pull each brace-delimited position object and ``json.loads`` it (order-independent,
robust). No XHR, no JS execution needed — the data is fully in the static HTML. US
values are USD, CA values are CAD; both are annual **base salary** (``basis=
"base-annual"``), kind="estimate" (Robert Half's own market survey, not an official
statistic).

Crawl posture: exactly two GETs (one per market), desktop Chrome UA, ~2.5s jittered
throttle, consecutive-failure circuit breaker. Fetch-once semantics — each market's
PARSED page (list of position records, or a miss marker) caches under
``staging/roberthalf/pages/`` and is never refetched (a miss caches too). The cache
is the checkpoint; re-runs resume.

ESTIMATE lens rules: rows land source-labeled (source="roberthalf", kind="estimate").
No sample sizes are published, so ``n=null`` (honest — the guide is a curated survey,
not a datapoint count). ROLES-ONLY: designation-level figures only; there are no
per-company tables or employer names on these pages to begin with.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.taxonomy import match_title_to_role

log = get_logger("ingest.roberthalf")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 3       # only 2 requests total — a small breaker is plenty

# (country, currency, salary-guide landing URL). One cell per market.
MARKETS: list[tuple[str, str, str]] = [
    ("US", "USD", "https://www.roberthalf.com/us/en/insights/salary-guide/technology"),
    ("CA", "CAD", "https://www.roberthalf.com/ca/en/insights/salary-guide/technology"),
]

# The three published figures are experience TIERS, not percentiles — map each to its
# native level_label (verbatim from the guide's own definitions). yoe stays None: a
# tier is an experience *band*, never a years number (charter: never invent years).
TIERS = [
    ("salaryLow", "limited experience"),
    ("salaryMidpoint", "moderate experience"),
    ("salaryHigh", "extensive experience"),
]


def _staging_dir():
    d = settings.staging_dir / "roberthalf"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(country: str):
    return _staging_dir() / "pages" / f"{country.lower()}.json"


def _fetch_html(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _deesc(s: str) -> str:
    """De-escape the JS string literal the salary JSON is embedded in (\\x22 -> ",
    \\u002D -> -, \\/ -> /) so each position object becomes parseable JSON."""
    s = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return s.replace("\\/", "/")


def _parse_page(html: str, country: str, currency: str) -> dict:
    """De-escaped HTML → {"rows": [estimate records]} (one row per role×tier), or
    {"miss": True} when the page carries no salary positions (a real signal — cache it).

    ROLES-ONLY: only the role-level ``positions`` objects (jobTitle + the three tier
    figures) are parsed; the surrounding page is ignored."""
    dec = _deesc(html)
    # each position object is a flat brace-delimited unit carrying jobTitle+salaryMidpoint
    chunks = re.findall(r'\{"jobTitle":"[^{}]*?"salaryMidpoint":\d+[^{}]*?\}', dec)
    if not chunks:
        return {"miss": True, "reason": "no salary positions in static HTML"}
    retrieved = time.strftime("%Y-%m-%d")
    rows: list[dict] = []
    seen: set[str] = set()
    for c in chunks:
        try:
            o = json.loads(c)
        except json.JSONDecodeError:
            continue
        title = (o.get("jobTitle") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        role_id = match_title_to_role(title)
        for key, level_label in TIERS:
            val = o.get(key)
            if not val:
                continue
            rows.append({
                "source": "roberthalf", "kind": "estimate",
                "role_id": role_id, "role_surface": title,
                "country": country, "currency": currency,
                "basis": "base-annual",
                "yoe": None,                      # a tier is an experience band, not years
                "level_label": level_label,       # native tier string, verbatim
                "median": val,                    # the tier figure for this role
                "p25": None, "p75": None, "p10": None, "p90": None,
                "n": None,                        # guide publishes no sample sizes
                "retrieved": retrieved,
            })
    if not rows:
        return {"miss": True, "reason": "positions present but no salary figures"}
    return {"rows": rows, "n_roles": len(seen), "retrieved": retrieved}


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch each market page not yet cached (2 total). Cache = checkpoint."""
    t0 = time.time()
    fetched = cached = misses = 0
    consec_fail = 0
    markets = MARKETS[:max_units] if max_units else MARKETS
    for country, currency, url in markets:
        if time_cap_s is not None and (time.time() - t0) > time_cap_s:
            log.warning("roberthalf: time cap %.0fs hit — landing partial", time_cap_s)
            break
        path = _cell_path(country)
        if path.exists() and not force:
            cached += 1
            continue
        try:
            html = _fetch_html(url)
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked
            consec_fail += 1
            log.warning("roberthalf: fetch failed %s (%s) [consec %d]",
                        country, str(e)[:80], consec_fail)
            if consec_fail >= MAX_CONSEC_FAIL:
                log.error("roberthalf: %d consecutive failures — stopping (resume later)",
                          consec_fail)
                break
            time.sleep(THROTTLE * 2)
            continue
        rec = _parse_page(html, country, currency)
        path.write_text(json.dumps(rec), encoding="utf-8")
        fetched += 1
        if rec.get("miss"):
            misses += 1
            log.info("roberthalf: %s — no salary data (%s)", country, rec.get("reason"))
        else:
            log.info("roberthalf: %s → %d roles, %d rows",
                     country, rec["n_roles"], len(rec["rows"]))
        time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("roberthalf summary: %s", s)
    return s


def _aggregate() -> list[dict]:
    """Fuse all cached market pages into the single aggregate the fusion step reads."""
    sd = _staging_dir()
    rows: list[dict] = []
    for f in sorted((sd / "pages").glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not rec.get("miss"):
            rows.extend(rec.get("rows", []))
    (sd / "estimates.json").write_text(json.dumps(rows), encoding="utf-8")
    return rows


def load_estimates() -> list[dict]:
    p = _staging_dir() / "estimates.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return _aggregate()


def run(**kw) -> dict:
    """Connector entrypoint (registry + collect_all)."""
    return fetch_all(**kw)


if __name__ == "__main__":  # pragma: no cover — manual smoke: US only
    print(json.dumps(run(max_units=1), indent=2))
