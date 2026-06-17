"""ATS board connectors (Greenhouse / Lever / Ashby) — real public-JSON ingestion.

The brainstorm's crown jewel: Greenhouse, Lever, Ashby expose **public JSON board
APIs** — structured postings, no HTML scraping. The slug universe comes from
``cc_index.py`` (CC index) + a curated country-diverse seed. ``fetch_fleet`` polls a
set of boards over the proven ``polite_fleet`` harness (per-host pacing, backoff,
circuit-breaker, checkpoint, resumable), tech-filters at ingest, and tags each
posting's country from its location — so what reaches clustering is clean + balanced.

Parsers are written against the LIVE payload shapes (probed 2026-06):
  * Greenhouse  GET boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
                → {"jobs":[{title, location:{name}, departments:[{name}], content(HTML),
                   absolute_url, updated_at, company_name, id}]}  (salary rarely present)
  * Lever       GET api.lever.co/v0/postings/{slug}?mode=json
                → [{text(title), categories:{location,department,team,commitment},
                   descriptionPlain, country(ISO), hostedUrl, createdAt, salaryRange}]
  * Ashby       GET api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
                → {"jobs":[{title, location(str), department, descriptionPlain, isRemote,
                   jobUrl, publishedAt, compensation:{scrapeableCompensation:[...]}}]}
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass, field

import requests

from backend.core.logging import get_logger
from backend.ingest.polite_fleet import FleetRetry, ParquetCheckpoint, PoliteFleet, Watchdog
from backend.ingest.tech_filter import classify as is_tech

log = get_logger("ingest.ats")

HEADERS = {"User-Agent": "strata-jobmarket/1.0 (research; contact via repo)"}

# live JSON board-API endpoints (the ones we actually poll)
API_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
}
# host each vendor's requests hit — drives polite-fleet per-host pacing/circuit
API_HOST = {
    "greenhouse": "boards-api.greenhouse.io",
    "lever": "api.lever.co",
    "ashby": "api.ashbyhq.com",
}
RETRY_STATUS = {429, 500, 502, 503, 504}
_TAG = re.compile(r"<[^>]+>")


@dataclass
class Posting:
    """Normalized posting — the common shape every ATS parser emits."""
    source: str                      # 'greenhouse' | 'lever' | 'ashby'
    board_slug: str
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    country: str | None = None       # one of our 7 ISO codes, else None
    remote: bool | None = None
    department: str = ""
    description: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    posted_at: str = ""
    url: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def disclosed(self) -> bool:
        return self.salary_min is not None or self.salary_max is not None


# --------------------------------------------------------------------------- #
#  location -> one of our 7 countries                                          #
# --------------------------------------------------------------------------- #
_OUR = {"IN", "US", "GB", "CA", "AU", "SG", "DE"}
_COUNTRY_WORDS = {
    "india": "IN", "united states": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united kingdom": "GB", "uk": "GB", "u.k.": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "britain": "GB", "great britain": "GB",
    "canada": "CA", "australia": "AU", "singapore": "SG",
    "germany": "DE", "deutschland": "DE",
}
_US_STATES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in",
    "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv",
    "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn",
    "tx", "ut", "vt", "va", "wa", "wv", "wi", "wy", "dc",
}
_CITY_COUNTRY = {
    # IN
    "bangalore": "IN", "bengaluru": "IN", "mumbai": "IN", "delhi": "IN", "new delhi": "IN",
    "gurgaon": "IN", "gurugram": "IN", "hyderabad": "IN", "pune": "IN", "chennai": "IN",
    "noida": "IN", "kolkata": "IN", "ahmedabad": "IN", "jaipur": "IN",
    # GB
    "london": "GB", "manchester": "GB", "edinburgh": "GB", "cambridge": "GB",
    "bristol": "GB", "leeds": "GB", "glasgow": "GB", "oxford": "GB", "birmingham": "GB",
    # CA
    "toronto": "CA", "vancouver": "CA", "montreal": "CA", "ottawa": "CA", "calgary": "CA",
    "waterloo": "CA", "kitchener": "CA", "edmonton": "CA",
    # AU
    "sydney": "AU", "melbourne": "AU", "brisbane": "AU", "perth": "AU", "canberra": "AU",
    "adelaide": "AU",
    # SG
    "singapore": "SG",
    # DE
    "berlin": "DE", "munich": "DE", "münchen": "DE", "muenchen": "DE", "hamburg": "DE",
    "frankfurt": "DE", "cologne": "DE", "köln": "DE", "koeln": "DE", "stuttgart": "DE",
    "düsseldorf": "DE", "duesseldorf": "DE", "karlsruhe": "DE",
    # US (major hubs; states below also catch it)
    "san francisco": "US", "new york": "US", "seattle": "US", "austin": "US",
    "boston": "US", "chicago": "US", "los angeles": "US", "denver": "US", "atlanta": "US",
    "palo alto": "US", "mountain view": "US", "sunnyvale": "US", "san jose": "US",
    "washington": "US", "remote us": "US", "remote - us": "US",
}


def location_to_country(loc: str | None, hint: str | None = None) -> str | None:
    """Map a free-text location (+ optional ISO hint) to one of our 7 countries, else None."""
    if hint and hint.strip().upper() in _OUR:
        return hint.strip().upper()
    s = (loc or "").lower().strip()
    if not s:
        return None
    for word, code in _COUNTRY_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", s):
            return code
    for city, code in _CITY_COUNTRY.items():
        if city in s:
            return code
    # trailing US state code: "San Francisco, CA" / "Austin, TX"
    tail = re.split(r"[,/|]", s)[-1].strip()
    if tail in _US_STATES:
        return "US"
    return None


def _strip_html(html: str | None) -> str:
    if not html:
        return ""
    return _TAG.sub(" ", html).replace("&amp;", "&").replace("&nbsp;", " ").strip()[:4000]


# --------------------------------------------------------------------------- #
#  per-vendor connectors                                                        #
# --------------------------------------------------------------------------- #
class AtsConnector:
    vendor: str = ""

    def endpoint(self, slug: str) -> str:
        return API_ENDPOINTS[self.vendor].format(slug=slug)

    def parse(self, slug: str, payload) -> list[Posting]:
        raise NotImplementedError


class GreenhouseConnector(AtsConnector):
    vendor = "greenhouse"

    def parse(self, slug: str, payload) -> list[Posting]:
        out: list[Posting] = []
        for j in (payload or {}).get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            depts = j.get("departments") or []
            out.append(Posting(
                source="greenhouse", board_slug=slug, external_id=str(j.get("id", "")),
                title=(j.get("title") or "").strip(), company=j.get("company_name", "") or slug,
                location=loc, country=location_to_country(loc),
                remote="remote" in loc.lower() or None,
                department=(depts[0].get("name") if depts else "") or "",
                description=_strip_html(j.get("content")),
                posted_at=j.get("updated_at", "") or j.get("first_published", ""),
                url=j.get("absolute_url", ""),
            ))
        return out


class LeverConnector(AtsConnector):
    vendor = "lever"

    def parse(self, slug: str, payload) -> list[Posting]:
        out: list[Posting] = []
        for j in (payload or []):
            cats = j.get("categories") or {}
            loc = cats.get("location", "") or ""
            sal = j.get("salaryRange") or {}
            out.append(Posting(
                source="lever", board_slug=slug, external_id=str(j.get("id", "")),
                title=(j.get("text") or "").strip(), company=slug,
                location=loc, country=location_to_country(loc, j.get("country")),
                remote=(j.get("workplaceType", "").lower() == "remote") or None,
                department=cats.get("department") or cats.get("team") or "",
                description=(j.get("descriptionPlain") or "")[:4000],
                salary_min=_num(sal.get("min")), salary_max=_num(sal.get("max")),
                salary_currency=sal.get("currency", "") or "",
                posted_at=str(j.get("createdAt", "")), url=j.get("hostedUrl", ""),
            ))
        return out


class AshbyConnector(AtsConnector):
    vendor = "ashby"

    def parse(self, slug: str, payload) -> list[Posting]:
        out: list[Posting] = []
        for j in (payload or {}).get("jobs", []):
            loc = j.get("location", "") or ""
            lo, hi, cur = _ashby_comp(j.get("compensation"))
            out.append(Posting(
                source="ashby", board_slug=slug, external_id=str(j.get("id", "")),
                title=(j.get("title") or "").strip(), company=slug,
                location=loc, country=location_to_country(loc),
                remote=bool(j.get("isRemote")) or None,
                department=j.get("department") or j.get("team") or "",
                description=(j.get("descriptionPlain") or "")[:4000],
                salary_min=lo, salary_max=hi, salary_currency=cur,
                posted_at=j.get("publishedAt", ""), url=j.get("jobUrl", ""),
            ))
        return out


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _ashby_comp(comp):
    """Pull (min, max, currency) from Ashby's compensation block when present."""
    if not isinstance(comp, dict):
        return None, None, ""
    for tier in (comp.get("scrapeableCompensation") or []):
        rng = (tier or {}).get("compensationRange") or {}
        lo, hi = _num(rng.get("minValue")), _num(rng.get("maxValue"))
        if lo or hi:
            return lo, hi, rng.get("currencyCode", "") or ""
    return None, None, ""


CONNECTORS: dict[str, type[AtsConnector]] = {
    "greenhouse": GreenhouseConnector,
    "lever": LeverConnector,
    "ashby": AshbyConnector,
}


# --------------------------------------------------------------------------- #
#  fleet — poll many boards politely, tech-filter at ingest                    #
# --------------------------------------------------------------------------- #
def fetch_board(vendor: str, slug: str, *, timeout: float = 20.0) -> list[Posting]:
    """Fetch + parse one board (raises FleetRetry on transient status)."""
    conn = CONNECTORS[vendor]()
    try:
        r = requests.get(conn.endpoint(slug), headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        raise FleetRetry(503, str(e))
    if r.status_code in RETRY_STATUS:
        raise FleetRetry(r.status_code)
    if r.status_code != 200:
        return []          # 404 / gone — dead board
    try:
        return conn.parse(slug, r.json())
    except (ValueError, KeyError, TypeError):
        return []


def fetch_fleet(
    slugs_by_vendor: dict[str, list[str]],
    *,
    per_board_cap: int = 200,
    total_cap: int = 5000,
    tech_only: bool = True,
    arm: str = "curated",
    max_workers: int = 8,
    per_host_rate: float = 5.0,
    checkpoint_path: str | None = None,
    time_cap_s: float = 1800.0,
) -> tuple[list[dict], dict]:
    """Poll boards across vendors via the polite-fleet harness; tech-filter + country-tag.

    Returns (postings, stats). ``postings`` are dicts (one per kept tech posting, tagged
    with arm). ``stats`` carries per-vendor board/dead/raw/tech/disclosed counts + the
    removed-by-filter sample — everything the honest proof must report.
    """
    units = [(v, s) for v, slugs in slugs_by_vendor.items() if v in CONNECTORS for s in slugs]
    lock = threading.Lock()
    counter = {"n": 0}
    t0 = time.time()
    stats = {v: {"boards": 0, "live": 0, "dead": 0, "raw": 0, "tech": 0, "disclosed": 0}
             for v in CONNECTORS}
    removed_sample: list[str] = []

    def fetch_fn(unit):
        vendor, slug = unit
        with lock:
            if counter["n"] >= total_cap or (time.time() - t0) > time_cap_s:
                return []
        postings = fetch_board(vendor, slug)          # may raise FleetRetry (fleet retries)
        rows: list[dict] = []
        with lock:
            st = stats[vendor]
            st["boards"] += 1
            st["live"] += 1 if postings else 0
            st["dead"] += 0 if postings else 1
            for p in postings[:per_board_cap]:
                st["raw"] += 1
                if tech_only and not is_tech(p.title, p.description):
                    if len(removed_sample) < 40:
                        removed_sample.append(f"{vendor}:{p.title}")
                    continue
                if counter["n"] >= total_cap:
                    break
                st["tech"] += 1
                st["disclosed"] += 1 if p.disclosed else 0
                counter["n"] += 1
                d = asdict(p)
                d.pop("raw", None)
                d["arm"] = arm
                rows.append(d)
        if st_heartbeat(counter["n"], t0):
            log.info("[ats:%s] %d tech postings landed (%.0fs)", arm, counter["n"], time.time() - t0)
        return rows

    fleet = PoliteFleet(
        host_of=lambda u: API_HOST[u[0]], max_workers=max_workers, per_host_rate=per_host_rate,
        max_retries=3, watchdog=Watchdog(trip_after=9999),  # ATS 429s rare; don't global-trip
        checkpoint=ParquetCheckpoint(checkpoint_path) if checkpoint_path else None,
    )
    landed = fleet.run(units, fetch_fn)
    stats["_removed_sample"] = removed_sample
    stats["_fleet"] = fleet.stats
    stats["_elapsed_s"] = round(time.time() - t0, 1)
    stats["_total_tech"] = counter["n"]
    log.info("ats fleet[%s] done: %d tech postings from %d boards in %.0fs",
             arm, counter["n"], len(units), time.time() - t0)
    return landed, stats


_HB = {"last": 0}


def st_heartbeat(n: int, t0: float) -> bool:
    """Throttle heartbeat logs to ~every 250 postings."""
    if n - _HB["last"] >= 250:
        _HB["last"] = n
        return True
    return False


# --------------------------------------------------------------------------- #
#  curated country-diverse seed (the proof's CONTROL arm)                       #
#  Multinationals (global offices → country spread via location filter) +       #
#  regional tech employers. Dead slugs are reported, not hidden.                #
# --------------------------------------------------------------------------- #
CURATED_SEED: dict[str, list[str]] = {
    "greenhouse": [
        # multinationals w/ global offices (spread IN/GB/DE/CA/AU/SG via location filter)
        "stripe", "gitlab", "cloudflare", "hashicorp", "databricks", "twilio", "elastic",
        "mongodb", "confluent", "gusto", "airbnb", "dropbox", "robinhood", "coinbase",
        "brex", "instacart", "doordash", "samsara", "lyft", "asana", "figma", "plaid",
        "affirm", "discord", "reddit", "pinterest", "faire", "flexport", "scaleai",
        # regional-leaning
        "monzo", "starling", "wise", "gocardless", "deliveroo", "improbable", "checkout",
        "razorpay", "postman", "meesho", "cred", "groww", "browserstack", "innovaccer",
        "getyourguide", "celonis", "n26", "wealthsimple", "clio", "jobber", "canva",
        "safetyculture", "cultureamp", "grab", "nium",
    ],
    "ashby": [
        "1password", "ramp", "linear", "notion", "vercel", "runway", "cohere", "hex",
        "replicate", "modal", "baseten", "perplexity", "harvey", "sierra", "decagon",
        "mistral", "elevenlabs", "supabase", "posthog", "deel", "remote", "pleo",
        "gocardless", "synthesia", "pigment", "tractian", "multiverse",
    ],
    "lever": [
        "plaid", "brex", "netflix", "spotify", "ledger", "voiceflow", "biorender",
        "swordhealth", "kong", "leadgenius", "attentive",
    ],
}


if __name__ == "__main__":  # pragma: no cover — tiny manual smoke (poll 1 board/vendor)
    import json
    for vendor, slugs in {"greenhouse": ["stripe"], "ashby": ["1password"], "lever": ["biorender"]}.items():
        ps = fetch_board(vendor, slugs[0])
        tech = [p for p in ps if is_tech(p.title, p.description)]
        cc = {}
        for p in tech:
            cc[p.country] = cc.get(p.country, 0) + 1
        print(f"{vendor}/{slugs[0]}: {len(ps)} raw, {len(tech)} tech, countries={cc}")
        if tech:
            print("   e.g.", json.dumps({k: tech[0].__dict__[k] for k in ("title", "location", "country", "department")}))
