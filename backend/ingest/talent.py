"""Talent.com — query-driven role salary ESTIMATES across all 7 strata countries.

The user-decided estimate lens (2026-07-04): no official table gives role-level salary
for every market, so strata ingests what salary aggregators publish. Talent.com is the
cross-country breadth anchor of that wave: a single query endpoint that resolves *any*
free-text job title in each of our seven markets via country subdomains — the widest
geographic coverage of any connector in the fleet, at role granularity.

Where the numbers live (recon-verified 2026-07-05): each salary page is a server-rendered
Next.js page. The salary payload rides in an RSC push blob as a JSON-LD ``Occupation``
object tagged ``id":"job-data-ld+json"``. It is *double-encoded*: the ``children`` string
is JSON-escaped (``\\"`` for every quote) inside the HTML, so we regex-extract the escaped
body, ``json.loads`` once to unescape, then ``json.loads`` again to get the object. Inside,
``estimatedSalary[0]`` is a ``MonetaryAmountDistribution`` carrying currency + duration
(``P1Y``) + percentile10/25/median/percentile75/percentile90. One GET per (country, role)
cell; no XHR. Values are annual **base salary** (labeled ``basis="base-annual"``).

Coverage: URL is ``https://{sub}.talent.com/salary?job={role}`` where ``sub`` maps to our
seven ISO2 markets — www=US, in=IN, uk=GB, ca=CA, au=AU, sg=SG, de=DE (note uk≠GB and
www≠US in the subdomain namespace; the currency comes from the JSON-LD, not assumed). Any
free-text title resolves, so we crawl our curated role surfaces × 7 countries.

Thin-page guard: Talent.com serves a generic fallback page for unknown/misspelled titles.
We validate that the JSON-LD ``name`` echoes the queried title (word-overlap check) and that
a median exists — a fallback whose name doesn't match the query is rejected as a miss, so we
never bank salary numbers that belong to a different (or no) role.

Crawl posture: desktop Chrome UA, ~2.5s jittered throttle, consecutive-failure circuit
breaker, and fetch-once semantics — each (country, role) cell caches its PARSED record under
``staging/talent/pages/`` and is never refetched (misses cache too). The cache is the
checkpoint; re-runs resume.

ESTIMATE lens rules: rows land source-labeled (source="talent", kind="estimate") — never
blended into official/realized lenses. yoe is always null (Talent.com exposes only entry/
experienced tiers on other widgets, never a years axis — we never invent years). ROLES-ONLY:
the occupation-level distribution is the only thing parsed; Talent.com's per-company job
listings on adjacent pages are deliberately NOT touched.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.taxonomy import match_title_to_role

log = get_logger("ingest.talent")

BASE = "https://{sub}.talent.com/salary?job={job}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 5       # transport-failure circuit breaker (site blocking us → stop)

# Country subdomain → our ISO2. Note the two irregulars: www serves the US market and
# "uk" is the subdomain for GB. Currency is read from the JSON-LD, never assumed here.
COUNTRIES: list[tuple[str, str]] = [
    ("www", "US"),
    ("in", "IN"),
    ("uk", "GB"),
    ("ca", "CA"),
    ("au", "AU"),
    ("sg", "SG"),
    ("de", "DE"),
]

# Free-text role queries (any title resolves on Talent.com). Curated strata surfaces —
# match_title_to_role maps each back to a role_id (or None → estimate-only surface).
ROLE_QUERIES: list[str] = [
    "software engineer",
    "machine learning engineer",
    "data engineer",
    "data scientist",
    "front end developer",
    "back end developer",
    "devops engineer",
    "site reliability engineer",
    "security engineer",
    "cloud architect",
    "product manager",
    "ux designer",
    "mobile developer",
    "data analyst",
    "engineering manager",
    "qa engineer",
    # long tail (may or may not resolve to a curated role)
    "full stack developer",
    "ai engineer",
    "solutions architect",
    "platform engineer",
    "android developer",
    "ios developer",
    "business analyst",
]

# JSON-LD Occupation blob, double-encoded inside the RSC push. Non-greedy body up to the
# id marker; the captured group is a JSON-escaped string (unescape once, then load).
_LD_RE = re.compile(
    r'"children":"(\{\\"@context\\".*?)","id":"job-data-ld\+json"', re.S)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _staging_dir():
    d = settings.staging_dir / "talent"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(sub: str, query: str):
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return _staging_dir() / "pages" / f"{sub}_{slug}.json"


def _fetch_html(sub: str, query: str, timeout: int = 30) -> str:
    url = BASE.format(sub=sub, job=urllib.parse.quote_plus(query))
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _extract_ld(html: str) -> dict | None:
    """Pull the JSON-LD Occupation object out of the escaped RSC blob (double-decode)."""
    m = _LD_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(json.loads('"' + m.group(1) + '"'))
    except (json.JSONDecodeError, ValueError):
        return None


def _echoes(query: str, name: str | None) -> bool:
    """Thin-page guard: does the JSON-LD role name share a content word with the query?
    Talent.com serves a generic fallback for unknown titles — reject when it doesn't echo."""
    if not name:
        return False
    q = set(_WORD_RE.findall(query.lower()))
    n = set(_WORD_RE.findall(name.lower()))
    stop = {"engineer", "developer", "manager", "analyst", "the", "of", "and"}
    core = (q - stop) & (n - stop)
    return bool(core) or bool(q & n)


def _parse_page(html: str, sub: str, iso2: str, query: str) -> dict:
    """RSC JSON-LD → one estimate record, or {"miss": True} for a fallback/empty page."""
    obj = _extract_ld(html)
    if not obj:
        return {"miss": True, "reason": "no job-data-ld+json"}
    if (obj.get("@type") or "") != "Occupation":
        return {"miss": True, "reason": "not an Occupation"}
    name = obj.get("name")
    if not _echoes(query, name):
        return {"miss": True, "reason": f"name '{name}' does not echo query"}
    dists = obj.get("estimatedSalary") or []
    dist = next((d for d in dists
                 if (d.get("@type") == "MonetaryAmountDistribution"
                     and d.get("median") is not None)), None)
    if not dist:
        return {"miss": True, "reason": "no MonetaryAmountDistribution median"}
    median = dist.get("median")
    if not median:
        return {"miss": True, "reason": "empty median"}
    surface = name or query
    rec = {
        "source": "talent", "kind": "estimate",
        "role_id": match_title_to_role(surface),
        "role_surface": surface,
        "country": iso2,
        "currency": dist.get("currency"),
        "basis": "base-annual",              # duration P1Y, distribution name "base"
        "yoe": None,                         # Talent.com exposes no years axis — never invent
        "level_label": None,
        "median": median,
        "p10": dist.get("percentile10"), "p25": dist.get("percentile25"),
        "p75": dist.get("percentile75"), "p90": dist.get("percentile90"),
        "n": None,                           # Talent.com does not publish a sample size
        "retrieved": time.strftime("%Y-%m-%d"),
    }
    return rec


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch every (country × role-query) cell not yet cached. Cache = checkpoint."""
    t0 = time.time()
    fetched = cached = misses = rows = 0
    consec_fail = 0
    units = [(sub, iso2, q) for sub, iso2 in COUNTRIES for q in ROLE_QUERIES]
    if max_units:
        units = units[:max_units]
    for sub, iso2, query in units:
        if time_cap_s is not None and (time.time() - t0) > time_cap_s:
            log.warning("talent: time cap %.0fs hit — landing partial", time_cap_s)
            break
        path = _cell_path(sub, query)
        if path.exists() and not force:
            cached += 1
            continue
        try:
            html = _fetch_html(sub, query)
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked
            consec_fail += 1
            log.warning("talent: fetch failed %s/%s (%s) [consec %d]",
                        sub, query, str(e)[:80], consec_fail)
            if consec_fail >= MAX_CONSEC_FAIL:
                log.error("talent: %d consecutive failures — site likely blocking; "
                          "stopping (resume later)", consec_fail)
                break
            time.sleep(THROTTLE * 2)
            continue
        rec = _parse_page(html, sub, iso2, query)
        path.write_text(json.dumps(rec), encoding="utf-8")
        fetched += 1
        if rec.get("miss"):
            misses += 1
            log.info("talent: %s/%s — miss (%s)", sub, query, rec.get("reason"))
        else:
            rows += 1
            log.info("talent: %s/%s → %s median %s %s",
                     iso2, query, rec["role_surface"], rec["median"], rec["currency"])
        time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("talent summary: %s", s)
    return s


def _aggregate() -> list[dict]:
    """Fuse all cached page records into the single aggregate the fusion step reads."""
    sd = _staging_dir()
    rows: list[dict] = []
    for f in sorted((sd / "pages").glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not rec.get("miss"):
            rows.append(rec)
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


if __name__ == "__main__":  # pragma: no cover — manual smoke: first 2 cells only
    print(json.dumps(run(max_units=2), indent=2))
