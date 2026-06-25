"""USAJobs — the US **public-sector pay floor**: GS/pay-band salary ranges by OPM
occupational series, the federal-government counterpart to strata's private wage
signals.

Private postings (Adzuna et al.) tell us what the market pays; they say nothing
about the *floor* a US tech worker can fall back on in federal service. The USAJobs
API exposes every live federal vacancy with its statutory ``PositionRemuneration``
(min/max on a GS step or pay band) tagged by ``JobCategory`` (the OPM occupational
series, e.g. 2210 "Information Technology Management", 1550 "Computer Science"). That
gives a per-series, government-set salary floor/ceiling — a calibration anchor that
no private board provides. It feeds the **official salary lens**
(``fact_salary_official``) as the US public-sector baseline beside ILOSTAT and the
national agencies.

Grain: country='US' × OPM series × period (publication date). Country is always 'US'
(USAJobs is federal-only); ``PositionLocation`` is landed as a free-text duty-station
string, not a geo key. Obtained from the public USAJobs search API
(https://data.usajobs.gov/api/search) which requires a free Authorization-Key plus a
contact email in User-Agent — both pulled from settings; **credential-graceful**: if
the key is absent the connector logs a warning and returns [] rather than crashing.
The cached JSON is the checkpoint. ROLES-ONLY: we keep the OPM series, title, salary
band, duty station and date — the **hiring agency / department is dropped** as a
product field. Not run in this pass; coded for the later run.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.usajobs")

BASE_URL = "https://data.usajobs.gov/api/search"
HOST = "data.usajobs.gov"
RESULTS_PER_PAGE = 500  # API max

# OPM occupational series most relevant to a tech job-market warehouse. The API's
# Keyword filter is broad; we sweep these series families so the staging file is
# tech-weighted rather than the whole federal vacancy firehose. Series codes are
# OPM's own (https://www.opm.gov/policy-data-oversight/classification-qualifications).
TECH_KEYWORDS = (
    "Information Technology",
    "Computer Science",
    "Computer Engineer",
    "Data Scientist",
    "Software",
    "Cybersecurity",
    "Statistician",
    "Operations Research",
    "Electronics Engineer",
)


def _staging_dir():
    d = settings.staging_dir / "usajobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "postings.json"


def _credentials() -> tuple[str, str] | None:
    """(email, api_key) from settings, or None (graceful) if either is missing."""
    api_key = getattr(settings, "usajobs_api_key", None) or getattr(settings, "USAJOBS_API_KEY", None)
    email = getattr(settings, "usajobs_email", None) or getattr(settings, "USAJOBS_EMAIL", None)
    # Fall back to the operator email pydantic-settings may carry under another name.
    email = email or getattr(settings, "contact_email", None)
    if not api_key:
        log.warning(
            "usajobs: no USAJOBS_API_KEY in settings — skipping fetch (get a free key "
            "at https://developer.usajobs.gov/apirequest/). Returning []."
        )
        return None
    if not email:
        # API requires a User-Agent email; without one requests are rejected. Warn but
        # supply a clear placeholder so the failure (if run) is legible, not silent.
        log.warning("usajobs: no USAJOBS_EMAIL/contact_email in settings — using placeholder UA.")
        email = "strata-research@example.com"
    return str(email), str(api_key)


def _headers(email: str, api_key: str) -> dict:
    return {
        "Host": HOST,
        "User-Agent": email,
        "Authorization-Key": api_key,
    }


def _parse_remuneration(remun: list | None) -> tuple[float | None, float | None]:
    """PositionRemuneration -> (min, max) in the listed currency, annualized where given."""
    if not remun:
        return None, None
    block = remun[0] if isinstance(remun, list) and remun else {}
    if not isinstance(block, dict):
        return None, None

    def _num(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    return _num(block.get("MinimumRange")), _num(block.get("MaximumRange"))


def _series_from_category(cats: list | None) -> str:
    """JobCategory -> OPM series code (e.g. '2210'); first category wins."""
    if not cats:
        return ""
    first = cats[0] if isinstance(cats, list) and cats else {}
    if isinstance(first, dict):
        return str(first.get("Code") or "").strip()
    return ""


def _location_from(locs: list | None) -> str:
    """PositionLocation -> a single duty-station string. No geo key — US is implied."""
    if not locs:
        return ""
    first = locs[0] if isinstance(locs, list) and locs else {}
    if isinstance(first, dict):
        return str(first.get("LocationName") or "").strip()
    return ""


def _fetch_keyword(keyword: str, headers: dict, max_pages: int, timeout: int = 45) -> list[dict]:
    """Page one keyword sweep into normalized, roles-only posting rows. Best-effort."""
    rows: list[dict] = []
    for page in range(1, max_pages + 1):
        params = {
            "Keyword": keyword,
            "ResultsPerPage": RESULTS_PER_PAGE,
            "Page": page,
        }
        url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))

        items = (((payload or {}).get("SearchResult") or {}).get("SearchResultItems")) or []
        if not items:
            break
        for it in items:
            d = (it or {}).get("MatchedObjectDescriptor") or {}
            smin, smax = _parse_remuneration(d.get("PositionRemuneration"))
            rows.append({
                "country": "US",  # USAJobs is federal-only
                "series": _series_from_category(d.get("JobCategory")),
                "title": str(d.get("PositionTitle") or "").strip(),
                "salary_min": smin,
                "salary_max": smax,
                "location": _location_from(d.get("PositionLocation")),
                "date": str(d.get("PublicationStartDate") or "")[:10],
                # NOTE: d["OrganizationName"] / d["DepartmentName"] deliberately DROPPED
                # — employer is never a product field (roles-only charter).
            })
        # Stop early if the API returned a short (final) page.
        if len(items) < RESULTS_PER_PAGE:
            break
    return rows


def fetch_postings(
    force: bool = False,
    max_pages: int = 2,
    time_cap_s: float = 600.0,
) -> list[dict]:
    """Fetch + cache US public-sector postings across the tech series sweep.

    Cache file IS the checkpoint: if it exists and not ``force``, returns load_postings().
    Credential-graceful (no key → warn + []) and network-graceful (per-keyword try/except).
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_postings()

    creds = _credentials()
    if creds is None:
        return []
    email, api_key = creds
    headers = _headers(email, api_key)

    out: list[dict] = []
    seen: set[tuple] = set()
    t0 = time.time()
    for kw in TECH_KEYWORDS:
        if time.time() - t0 > time_cap_s:
            log.warning("usajobs: time cap %ss hit — landing partial", time_cap_s)
            break
        try:
            rows = _fetch_keyword(kw, headers, max_pages=max_pages)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                log.warning("usajobs: auth rejected (HTTP %s) — check USAJOBS_API_KEY/email. Aborting.", e.code)
                break
            log.warning("usajobs: '%s' fetch failed (HTTP %s) — skip", kw, e.code)
            continue
        except Exception as e:  # noqa: BLE001 — one keyword must not sink the run
            log.warning("usajobs: '%s' fetch failed (%s) — skip", kw, e)
            continue
        # Dedup across overlapping keyword sweeps on (series, title, date, location).
        added = 0
        for row in rows:
            key = (row["series"], row["title"], row["date"], row["location"], row["salary_min"])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
            added += 1
        print(f"[usajobs] {kw}: {len(rows)} hits, {added} new postings", flush=True)

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info(
        "USAJobs postings: %d rows across %d OPM series",
        len(out), len({r["series"] for r in out if r["series"]}),
    )
    return out


def load_postings() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache USAJobs public-sector salary bands. Connector entrypoint."""
    rows = fetch_postings(**kw)
    series = sorted({r["series"] for r in rows if r["series"]})
    with_band = sum(1 for r in rows if r["salary_min"] is not None)
    return {
        "rows": len(rows),
        "series": series,
        "with_salary_band": with_band,
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
