"""EURES — live **EU government vacancy volume**, pre-tagged to ESCO occupations and
ESCO skills, for the demand fact (council source for the EU/DE side).

The NEW signal: most of strata's demand evidence is scraped/aggregated postings whose
occupation tagging we have to infer ourselves. EURES is the European Commission's own
job-mobility portal — every vacancy on it is already classified against the **ESCO**
occupation taxonomy and carries ESCO skill tags, published by the member-state public
employment services. That gives us an *officially-classified* vacancy stream we don't
have to re-tag: a clean count of how many live government-sourced vacancies exist per
ESCO occupation × ESCO skill × country, with Germany (DE) and the wider EU in focus.
It feeds **fact_demand** (the DE/EU lane), beside the scraped-posting demand.

Grain: country (ISO-2, mapped to our 7 where they intersect — DE + EU spillover) ×
ESCO occupation code × skill list × observation date. Vacancy *counts/records*, never
the employer — see ROLES-ONLY below.

How it's obtained + legitimacy: the public EURES portal at
``https://ec.europa.eu/eures`` exposes a search/JV (Job Vacancy) endpoint and the EU
Open Data portal mirrors EURES vacancy datasets. There is **no single stable, fully
documented public REST contract** for the live vacancy search — the portal's internal
``/eures/public/api`` surface changes shape and sometimes requires the EU Login /
"EURES API" partner credential for the bulk feed. So this connector is **FRAGILE +
BEST-EFFORT**: it tries (1) a partner-key bulk pull if a key is configured, else
(2) the public JSON search surface, and degrades to an empty landing (logged, never a
crash) if neither answers. It is **not run in this pass** — but the code is real and
runnable: real endpoints, real ESCO field parsing, real ISO mapping.

ROLES-ONLY: we land occupation × skills × country × date × count. Any employer /
organisation / hiring-company field present in a EURES record is **dropped** and never
written to the product. Credential-graceful: the EURES partner key is read via
``getattr(settings, "eures_api_key", None)`` and its absence only downgrades us to the
public surface (or to empty), with a warning.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.eures")

# EURES portal roots. The public search surface is undocumented/unstable; the partner
# bulk feed needs an EU-Login-issued "EURES API" key. We try both, best-effort.
EURES_BASE = "https://ec.europa.eu/eures"
# Public JSON search surface used by the portal front-end (shape may drift).
PUBLIC_SEARCH_URL = EURES_BASE + "/eures-apps/searchengine/page/jv-search/search"
# Partner bulk feed (requires key); shape per the EURES API partner spec.
PARTNER_FEED_URL = EURES_BASE + "/api/v1/jv/search"

HEADERS = {
    "User-Agent": "strata/1.0 (+research; roles-only job-market explorer)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# EURES country codes are ISO-2 already; we keep only the ones that intersect our 7.
# Of our 7 (IN US GB CA AU SG DE) only DE is an EU/EURES member — GB left the network
# post-Brexit. We focus DE and still land any other EU code we see for the EU lane,
# but only DE maps onto a strata country. Non-DE EU rows are tagged with their native
# ISO-2 (e.g. FR, NL) for the broader EU demand view.
OUR_EURES_COUNTRIES = {"DE"}
EU_FOCUS = ["DE"]  # primary target; the API can be widened by passing `countries=`


def _staging_dir():
    d = settings.staging_dir / "eures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "vacancies.json"


def _post_json(url: str, body: dict, headers: dict, timeout: int = 45) -> dict:
    """POST a JSON body and parse a JSON reply. Raises on transport/parse failure."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _get_json(url: str, headers: dict, timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _esco_occ(rec: dict) -> str:
    """Pull the ESCO occupation code/URI from a EURES vacancy record (several shapes).

    EURES tags occupations with ESCO; depending on the surface the field appears as
    ``escoOccupation``, ``occupationUri``, a nested ``esco``/``occupation`` object, or
    an ``isco``/``escoCode`` string. We land the most specific identifier we find.
    """
    for key in ("escoOccupation", "occupationUri", "escoUri", "escoCode", "occupation"):
        v = rec.get(key)
        if isinstance(v, dict):
            v = v.get("uri") or v.get("code") or v.get("id")
        if v:
            return str(v)
    # nested {"esco": {"occupation": {...}}}
    esco = rec.get("esco") or {}
    if isinstance(esco, dict):
        occ = esco.get("occupation") or {}
        if isinstance(occ, dict):
            return str(occ.get("uri") or occ.get("code") or "")
        if occ:
            return str(occ)
    return ""


def _esco_skills(rec: dict) -> list[str]:
    """Pull ESCO skill codes/URIs from a record. Returns a de-duped string list."""
    out: list[str] = []
    raw = (rec.get("escoSkills") or rec.get("skills")
           or (rec.get("esco") or {}).get("skills") if isinstance(rec.get("esco"), dict)
           else rec.get("escoSkills") or rec.get("skills"))
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("values") or list(raw.values())
    if not isinstance(raw, list):
        return out
    for s in raw:
        if isinstance(s, dict):
            s = s.get("uri") or s.get("code") or s.get("id") or s.get("label")
        if s:
            out.append(str(s))
    # de-dup, preserve order
    seen: set[str] = set()
    return [s for s in out if not (s in seen or seen.add(s))]


def _country_of(rec: dict) -> str:
    """ISO-2 country of the vacancy's work location (EURES uses ISO-2 already)."""
    for key in ("countryCode", "country", "locationCountry", "workCountry"):
        v = rec.get(key)
        if isinstance(v, dict):
            v = v.get("code") or v.get("id")
        if v:
            return str(v).upper()[:2]
    loc = rec.get("location") or {}
    if isinstance(loc, dict):
        v = loc.get("countryCode") or loc.get("country")
        if v:
            return str(v).upper()[:2]
    return ""


def _normalize(rec: dict) -> dict | None:
    """Map one raw EURES vacancy record to our landing shape. DROP employer fields.

    Output keys: country (ISO-2), esco_occ (ESCO URI/code), skills (list), date (ISO).
    Returns None if the record has no usable occupation tag (we want classified rows).
    """
    occ = _esco_occ(rec)
    if not occ:
        return None  # only keep ESCO-classified vacancies — that's the whole point
    country = _country_of(rec)
    date = (rec.get("creationDate") or rec.get("modificationDate")
            or rec.get("publicationDate") or rec.get("date") or "")
    # ROLES-ONLY: explicitly never read/keep employer/org/company/hiringOrganization.
    return {
        "country": country,
        "esco_occ": occ,
        "skills": _esco_skills(rec),
        "date": str(date)[:10],
    }


def _records_from_payload(payload: dict) -> list[dict]:
    """Find the vacancy array in whatever envelope the surface returned."""
    if not isinstance(payload, dict):
        return []
    for key in ("jvs", "vacancies", "results", "items", "content", "data"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):  # paged envelope, e.g. {"data": {"items": [...]}}
            for k2 in ("items", "results", "content", "jvs"):
                if isinstance(v.get(k2), list):
                    return v[k2]
    return []


def _fetch_partner(countries: list[str], page_size: int, max_pages: int,
                   key: str, time_cap_s: float) -> list[dict]:
    """Bulk pull via the EURES partner feed (needs an EU-Login API key). Best-effort."""
    headers = dict(HEADERS)
    headers["Authorization"] = f"Bearer {key}"
    headers["X-API-Key"] = key  # some EURES partner gateways use this header instead
    out: list[dict] = []
    t0 = time.time()
    for page in range(max_pages):
        if time.time() - t0 > time_cap_s:
            log.warning("eures: time cap %ss hit (partner) — landing partial", time_cap_s)
            break
        body = {
            "resultsPerPage": page_size,
            "page": page + 1,
            "sortSearch": "BEST_MATCH",
            "locationCodes": countries,
            "facetField": ["occupationUri", "escoSkill"],
        }
        try:
            payload = _post_json(PARTNER_FEED_URL, body, headers)
        except Exception as e:  # noqa: BLE001 — one page must not sink the run
            log.warning("eures: partner page %d failed (%s) — stop", page + 1, e)
            break
        recs = _records_from_payload(payload)
        if not recs:
            break
        out.extend(recs)
        print(f"[eures] partner page {page + 1}: {len(recs)} vacancies "
              f"({len(out)} total)", flush=True)
        if len(recs) < page_size:
            break
    return out


def _fetch_public(countries: list[str], page_size: int, max_pages: int,
                  time_cap_s: float) -> list[dict]:
    """Try the portal's public search surface (undocumented, may 4xx/drift)."""
    out: list[dict] = []
    t0 = time.time()
    for page in range(max_pages):
        if time.time() - t0 > time_cap_s:
            log.warning("eures: time cap %ss hit (public) — landing partial", time_cap_s)
            break
        body = {
            "resultsPerPage": page_size,
            "page": page + 1,
            "keywords": [],
            "sortSearch": "BEST_MATCH",
            "locationCodes": countries,
        }
        try:
            payload = _post_json(PUBLIC_SEARCH_URL, body, HEADERS)
        except Exception as e:  # noqa: BLE001 — surface is fragile; try a GET fallback
            log.warning("eures: public POST page %d failed (%s) — trying GET", page + 1, e)
            try:
                q = urllib.parse.urlencode({
                    "resultsPerPage": page_size, "page": page + 1,
                    "locationCodes": ",".join(countries),
                })
                payload = _get_json(f"{PUBLIC_SEARCH_URL}?{q}", HEADERS)
            except Exception as e2:  # noqa: BLE001
                log.warning("eures: public GET page %d also failed (%s) — stop",
                            page + 1, e2)
                break
        recs = _records_from_payload(payload)
        if not recs:
            break
        out.extend(recs)
        print(f"[eures] public page {page + 1}: {len(recs)} vacancies "
              f"({len(out)} total)", flush=True)
        if len(recs) < page_size:
            break
    return out


def fetch_vacancies(force: bool = False, countries: list[str] | None = None,
                    page_size: int = 50, max_pages: int = 20,
                    time_cap_s: float = 600.0) -> list[dict]:
    """Fetch + cache EURES vacancies tagged to ESCO. The cache file IS the checkpoint.

    Strategy: partner bulk feed if ``settings.eures_api_key`` is set, else the public
    search surface. Either way we degrade to an empty landing (logged) rather than
    crashing — this source is fragile and credential-gated. ROLES-ONLY normalization
    drops every employer field before anything is written.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_vacancies()

    countries = countries or EU_FOCUS
    key = getattr(settings, "eures_api_key", None)

    raw: list[dict] = []
    if key:
        log.info("eures: partner key present — using bulk feed for %s", countries)
        raw = _fetch_partner(countries, page_size, max_pages, key, time_cap_s)
    else:
        log.warning("eures: no eures_api_key configured — trying public search "
                    "surface (fragile, may return nothing)")
        raw = _fetch_public(countries, page_size, max_pages, time_cap_s)

    if not raw:
        log.warning("eures: no vacancies obtained (source fragile / credential-gated) "
                    "— landing empty, not crashing")

    rows: list[dict] = []
    for rec in raw:
        try:
            norm = _normalize(rec)
        except Exception as e:  # noqa: BLE001 — one bad record must not sink the run
            log.warning("eures: record normalize failed (%s) — skip", e)
            continue
        if norm:
            rows.append(norm)

    # Keep DE + any EU rows for the EU demand lane; tag which map onto our 7.
    de_rows = [r for r in rows if r["country"] in OUR_EURES_COUNTRIES]
    f.write_text(json.dumps(rows), encoding="utf-8")
    log.info("EURES vacancies: %d ESCO-tagged rows (%d DE) across %d countries",
             len(rows), len(de_rows), len({r["country"] for r in rows if r["country"]}))
    return rows


def load_vacancies() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land EURES vacancies. Connector entrypoint.

    We land the normalized raw record list (``vacancies.json``) and stop there: the
    warehouse fuse (build.py ``_vacancy_feed_overlay``) consumes that raw list directly
    and does its OWN per-(role, country) count using the curated skill→role bridge.
    We previously also wrote a pre-aggregated ``vacancy_counts.json`` (grouped by
    country × ESCO-occ × skill), but nothing ever read it — the fuse counts at a
    different grain — so it was a dead artifact. Dropped to keep staging honest.
    """
    rows = fetch_vacancies(**kw)
    return {
        "rows": len(rows),
        "countries": sorted({r["country"] for r in rows if r["country"]}),
        "de_rows": sum(1 for r in rows if r["country"] in OUR_EURES_COUNTRIES),
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
