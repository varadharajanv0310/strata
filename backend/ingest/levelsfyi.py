"""levels.fyi — global tech-comp aggregator: role × location TOTAL-COMP + base ESTIMATES.

The estimate lens, senior/high-comp anchor (2026-07-05): levels.fyi is the crowdsourced
compensation aggregator engineers actually trust for total comp (base + bonus + stock),
covering all 7 strata countries. It is the only recon'd source that publishes a stock/
equity-inclusive **total compensation** percentile band per role — the picture base-only
sites (ITJobsWatch) and CTC sites (AmbitionBox) structurally cannot show.

Where the numbers live (recon-verified 2026-07-05 against the cached India analytics-
engineer page): each title×location page is a server-rendered Next.js page whose
``<script id="__NEXT_DATA__">`` JSON carries ``props.pageProps.serverJobTitlePercentiles``
— a single title×location aggregate with ``count`` and percentile bands (p10/p25/p50/
p75/p90) for ``totalCompensation`` AND ``baseSalary`` (plus ``bonus`` / ``stockGrant``).

CURRENCY: the percentile numbers are quoted in **USD**. The same payload ships
``locationCurrency`` (local ISO), ``locationExchangeRate`` (local-per-USD), and
``locationInfo.countryCodeIso2``. We convert USD → local with the payload's OWN rate
(usd * locationExchangeRate) and label the emitted currency as the local one, so a row
reads in the country's money exactly as the site renders it. ``defaultCountryMedian``
== ``totalCompensation.p50`` (USD) is the built-in cross-check.

JSON route (no HTML parse needed after bootstrap): once a buildId is known,
``/_next/data/{buildId}/en-us/t/{family}/title/{title}/locations/{location}.json``
returns the same pageProps directly. We bootstrap the buildId from ``__NEXT_DATA__`` on
any HTML page (cached or one live fetch) and fetch the lightweight JSON per cell after.

LEVELS: the ladder standard (Entry/Mid/Senior/Staff) is a site concept, but the
title-page ``serverJobTitlePercentiles`` block is a *title×location* aggregate — it is
NOT broken out per level. So every row here carries ``level_label=None`` and ``yoe=None``:
we refuse to invent a level or a years number the data does not contain (HARD RULE).
The value levels.fyi adds to strata is the stock-inclusive **tc-annual** basis, not a
per-level axis. base-annual rows are emitted alongside for the base-vs-total spread.

Crawl posture (mirrors the ambitionbox template): desktop Chrome UA, ~2.5s jittered
throttle, consecutive-failure circuit breaker, and fetch-once semantics — each
(family, title, location) cell caches its PARSED record under ``staging/levelsfyi/pages/``
and is never refetched (misses cache too, so a dead cell costs one request ever). The
cache is the checkpoint; re-runs resume.

ROLES-ONLY: title/designation-level aggregates only. levels.fyi's per-company salary
tables, employer names, and company leveling maps on adjacent routes are deliberately
NEVER parsed. Rows land source-labeled (source="levelsfyi", kind="estimate") with the
crowdsourced ``count`` kept for confidence weighting.
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

log = get_logger("ingest.levelsfyi")

HOST = "https://www.levels.fyi"
# Bootstrap buildId from any page's __NEXT_DATA__; fall back to this recon'd one on miss.
FALLBACK_BUILD_ID = "Hh_WyK8T6lBqyQ9RdCatn"
# HTML title page (for bootstrap + parse fallback) and the light JSON data route.
HTML_URL = HOST + "/t/{family}/title/{title}/locations/{location}"
JSON_URL = HOST + "/_next/data/{build}/en-us/t/{family}/title/{title}/locations/{location}.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 5       # transport-failure circuit breaker (site blocking us → stop)

# strata's 7 countries → the site's location slug (country-level pages).
LOCATIONS: dict[str, str] = {
    "IN": "india",
    "US": "united-states",
    "GB": "united-kingdom",
    "CA": "canada",
    "AU": "australia",
    "SG": "singapore",
    "DE": "germany",
}

# (family_slug, title_slug, strata_role_id | None). Tech families + the title slugs that
# ship in every payload's ``jobTitles`` (recon-verified against the cached page). role_id
# is a best-guess seed; the parser ALSO re-resolves via match_title_to_role on the site's
# own jobTitle name, and keeps role_id=None rows (estimate-only long-tail surfaces).
TITLES: list[tuple[str, str, str | None]] = [
    # ---- Software Engineer family ----
    ("software-engineer", "backend-software-engineer", "backend"),
    ("software-engineer", "frontend-software-engineer", "frontend"),
    ("software-engineer", "full-stack-software-engineer", None),
    ("software-engineer", "mobile-software-engineer", "mobile"),
    ("software-engineer", "machine-learning-engineer", "ml-eng"),
    ("software-engineer", "machine-learning-ops-engineer", None),   # MLOps long-tail
    ("software-engineer", "data-engineer", "data-eng"),
    ("software-engineer", "analytics-engineer", None),              # long-tail (cached cell)
    ("software-engineer", "devops-engineer", "devops"),
    ("software-engineer", "site-reliability-engineer", "sre"),
    ("software-engineer", "security-software-engineer", "security"),
    ("software-engineer", "ai-engineer", None),
    ("software-engineer", "research-scientist", None),
    ("software-engineer", "embedded-systems", None),
    # ---- adjacent tech families ----
    ("data-scientist", "data-scientist", "data-sci"),
    ("data-analyst", "data-analyst", "data-analyst"),
    ("product-manager", "product-manager", "pm"),
    ("product-designer", "product-designer", "ux"),
    ("software-engineering-manager", "software-engineering-manager", "eng-mgr"),
    ("solution-architect", "solution-architect", "cloud-arch"),
    ("technical-program-manager", "technical-program-manager", None),
]


def _staging_dir():
    d = settings.staging_dir / "levelsfyi"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(family: str, title: str, cc: str):
    return _staging_dir() / "pages" / f"{family}__{title}__{cc}.json"


def _http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _extract_next_data(text: str) -> dict | None:
    """Return the Next.js data object from either the JSON data-route body (already JSON
    with top-level ``pageProps``) or an HTML page's ``__NEXT_DATA__`` script tag."""
    t = text.lstrip()
    if t.startswith("{"):
        try:
            obj = json.loads(text)
            if "pageProps" in obj:
                return obj
            if "props" in obj:           # full __NEXT_DATA__ shape
                return obj
        except json.JSONDecodeError:
            pass
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _bootstrap_build_id() -> str:
    """Fetch one HTML page to read the live buildId; fall back to the recon'd constant."""
    fam, title, _ = TITLES[0]
    loc = LOCATIONS["US"]
    try:
        html = _http_get(HTML_URL.format(family=fam, title=title, location=loc))
        data = _extract_next_data(html)
        bid = (data or {}).get("buildId")
        if bid:
            log.info("levelsfyi: bootstrapped buildId=%s", bid)
            return bid
    except Exception as e:  # noqa: BLE001
        log.warning("levelsfyi: buildId bootstrap failed (%s) — using fallback",
                    str(e)[:80])
    return FALLBACK_BUILD_ID


def _pp(data: dict) -> dict | None:
    """pageProps from either JSON-route ({"pageProps":...}) or __NEXT_DATA__ ({"props":{"pageProps":...}})."""
    if "pageProps" in data:
        return data["pageProps"]
    try:
        return data["props"]["pageProps"]
    except (KeyError, TypeError):
        return None


def _band(block: dict | None, mult: float) -> dict:
    """A USD percentile block → local-currency median/p10/p25/p75/p90 (rounded)."""
    if not isinstance(block, dict):
        return {}
    def cv(k):
        v = block.get(k)
        return round(v * mult) if isinstance(v, (int, float)) else None
    return {"median": cv("p50"), "p10": cv("p10"), "p25": cv("p25"),
            "p75": cv("p75"), "p90": cv("p90")}


def _parse_page(text: str, family: str, title: str, cc: str,
                role_id: str | None) -> dict:
    """Next data → up to two estimate rows (tc-annual + base-annual) wrapped in one
    cache record, or {"miss": True} when the cell carries no percentiles (a real signal
    — cache it so the dead cell is never refetched)."""
    data = _extract_next_data(text)
    if not data:
        return {"miss": True, "reason": "no next-data"}
    pp = _pp(data)
    if not pp:
        return {"miss": True, "reason": "no pageProps"}
    sjp = pp.get("serverJobTitlePercentiles")
    if not isinstance(sjp, dict) or not sjp.get("totalCompensation"):
        return {"miss": True, "reason": "no serverJobTitlePercentiles"}

    # local currency + rate from the payload itself (never a hardcoded FX guess)
    currency = pp.get("locationCurrency") or "USD"
    rate = pp.get("locationExchangeRate")
    mult = float(rate) if isinstance(rate, (int, float)) and rate > 0 else 1.0
    info = pp.get("locationInfo") or {}
    country = info.get("countryCodeIso2") or cc      # trust payload, fall back to grid cc

    surface = sjp.get("jobTitle") or (pp.get("jobTitle") or {}).get("name") \
        or title.replace("-", " ").title()
    # re-resolve role_id from the site's own title name (keeps None if unmapped)
    resolved = match_title_to_role(surface) or role_id
    n = sjp.get("count")
    retrieved = time.strftime("%Y-%m-%d")

    def _row(basis: str, band: dict) -> dict:
        return {
            "source": "levelsfyi", "kind": "estimate",
            "role_id": resolved, "role_surface": surface,
            "slug": f"{family}/{title}",
            "country": country, "currency": currency, "basis": basis,
            "yoe": None, "level_label": None,        # title×location aggregate: no level axis
            "median": band.get("median"),
            "p25": band.get("p25"), "p75": band.get("p75"),
            "p10": band.get("p10"), "p90": band.get("p90"),
            "n": n, "retrieved": retrieved,
        }

    rows: list[dict] = []
    tc = _band(sjp.get("totalCompensation"), mult)
    if tc.get("median"):
        rows.append(_row("tc-annual", tc))
    base = _band(sjp.get("baseSalary"), mult)
    if base.get("median"):
        rows.append(_row("base-annual", base))
    if not rows:
        return {"miss": True, "reason": "empty percentiles"}
    return {"rows": rows, "family": family, "title": title,
            "country": country, "n": n}


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch every (family, title, location) cell not yet cached. Cache = checkpoint.

    ``max_units`` caps the number of TITLES iterated (each title fans out over the 7
    locations)."""
    t0 = time.time()
    fetched = cached = misses = 0
    consec_fail = 0
    build_id = None
    titles = TITLES[:max_units] if max_units else TITLES
    for family, title, role_id in titles:
        for cc, loc in LOCATIONS.items():
            if time_cap_s is not None and (time.time() - t0) > time_cap_s:
                log.warning("levelsfyi: time cap %.0fs hit — landing partial", time_cap_s)
                return _summary(fetched, cached, misses, t0)
            path = _cell_path(family, title, cc)
            if path.exists() and not force:
                cached += 1
                continue
            if build_id is None:                      # lazy bootstrap on first real fetch
                build_id = _bootstrap_build_id()
            url = JSON_URL.format(build=build_id, family=family, title=title, location=loc)
            try:
                body = _http_get(url)
                consec_fail = 0
            except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked/stale build
                consec_fail += 1
                log.warning("levelsfyi: fetch failed %s/%s @%s e=%s [consec %d]",
                            family, title, cc, str(e)[:80], consec_fail)
                if consec_fail >= MAX_CONSEC_FAIL:
                    log.error("levelsfyi: %d consecutive failures — site blocking or "
                              "stale buildId; stopping (resume later)", consec_fail)
                    return _summary(fetched, cached, misses, t0)
                time.sleep(THROTTLE * 2)
                continue
            rec = _parse_page(body, family, title, cc, role_id)
            path.write_text(json.dumps(rec), encoding="utf-8")
            fetched += 1
            if rec.get("miss"):
                misses += 1
                log.info("levelsfyi: %s/%s @%s — miss (%s)",
                         family, title, cc, rec.get("reason"))
            else:
                r0 = rec["rows"][0]
                log.info("levelsfyi: %s @%s → %s median %s%s (n=%s)",
                         title, cc, r0["basis"], r0["currency"], r0["median"], r0["n"])
            time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("levelsfyi summary: %s", s)
    return s


def _aggregate() -> list[dict]:
    """Fuse all cached cell records (each holds a `rows` list) into the flat aggregate
    the fusion step reads."""
    sd = _staging_dir()
    rows: list[dict] = []
    for f in sorted((sd / "pages").glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if rec.get("miss"):
            continue
        rows.extend(rec.get("rows", []))
    (sd / "estimates.json").write_text(json.dumps(rows), encoding="utf-8")
    return rows


def load_estimates() -> list[dict]:
    p = _staging_dir() / "estimates.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return _aggregate()


def run(**kw) -> dict:
    """Connector entrypoint (registry + collect_all).

    Signature: run(max_units=None, time_cap_s=None, force=False) -> summary dict."""
    return fetch_all(**kw)


if __name__ == "__main__":  # pragma: no cover — manual smoke: 2 titles only
    print(json.dumps(run(max_units=2), indent=2))
