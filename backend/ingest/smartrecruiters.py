"""SmartRecruiters public postings API — enterprise DE+IN full-body postings.

The data-expansion research's top impact-per-effort ATS lever after the HF corpus: a
clean, unauthenticated, paged JSON API over enterprise career sites — and the enterprise
segment is exactly the DE/IN/SG-office weight the startup-ATS fleet (Greenhouse/Lever/
Ashby, US-skewed) structurally misses. Live-verified: BoschGroup ``totalFound`` = 4,764
with the first page a Bengaluru (IN) role.

Two-step, both public (no key):
  * LIST   GET api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=N
           → {"totalFound":N, "content":[{name(title), location{country,city,remote},
              department, function, experienceLevel(seniority!), typeOfEmployment, id}]}
  * DETAIL GET api.smartrecruiters.com/v1/companies/{company}/postings/{id}
           → {..., jobAd:{sections:{companyDescription, jobDescription, qualifications,
              additionalInformation}}}  — ~1-2k chars of real body text.

The LIST alone gives a free seniority signal (``experienceLevel``) and clean ISO country;
the DETAIL is fetched only for the body the extractor needs. Postings are current job
ads → ``kind='advertised'``. Tech-filtered on title, filtered to strata's 7 countries.

ROLES-ONLY: the company slug is the fetch key + an internal dedup aid, never a product
axis (same status as every connector's ``employer``). NO FABRICATION: source + retrieval
labelled per row. Polite: desktop UA, throttle, consecutive-failure circuit breaker;
fetch-once cache per company. Bounded/resumable via ``run(max_companies, max_postings,
time_cap_s)``; the company slug universe scales later via the CC index (same method as
the Greenhouse fleet), this seed is the proof set.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.ingest.tech_filter import classify as is_tech

log = get_logger("ingest.smartrecruiters")

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
THROTTLE = 1.2                 # seconds between requests (public API, be polite)
MAX_CONSEC_FAIL = 6
PAGE = 100
STRATA = {"US", "GB", "IN", "DE", "CA", "AU", "SG"}
MAX_DESC_CHARS = 8000
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Proof seed — enterprise tenants verified to carry DE/IN/APAC offices (the skew fix).
# The real slug universe is harvested from the CC index of api.smartrecruiters.com later.
SEED_COMPANIES = [
    "BoschGroup",       # DE HQ, huge IN (Bangalore) + global footprint — verified 4,764
    "Visa",             # global, APAC + IN offices
    "Signify",          # NL/EU + IN
    "IKEA",             # EU + APAC
    "PublicisGroupe",   # global incl. IN/SG
]


def _staging_dir():
    d = settings.staging_dir / "smartrecruiters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pages_dir():
    d = _staging_dir() / "pages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _postings_file():
    return _staging_dir() / "postings.parquet"


def _strip(html) -> str:
    if not html:
        return ""
    t = _TAG.sub(" ", str(html))
    for a, b in (("&amp;", "&"), ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'")):
        t = t.replace(a, b)
    return _WS.sub(" ", t).strip()[:MAX_DESC_CHARS]


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _detail_desc(company: str, pid: str) -> str:
    """Fetch a posting's detail and concatenate its jobAd body sections → text."""
    try:
        d = _get(f"{API.format(company=company)}/{pid}")
    except Exception as e:  # noqa: BLE001
        log.info("smartrecruiters: detail %s/%s failed (%s)", company, pid, e)
        return ""
    sections = ((d.get("jobAd") or {}).get("sections") or {})
    parts = []
    for k in ("jobDescription", "qualifications", "companyDescription", "additionalInformation"):
        s = (sections.get(k) or {}).get("text") or ""
        if s:
            parts.append(s)
    return _strip(" ".join(parts))


def fetch_company(company: str, *, max_postings: int, time_cap_s: float,
                  force: bool = False) -> list[dict]:
    """List → tech-filter on title → detail-fetch the body for kept rows. Cached per company."""
    cache = _pages_dir() / f"{company}.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    rows: list[dict] = []
    offset = 0
    consec_fail = 0
    t0 = time.time()
    total = None
    while True:
        if time.time() - t0 > time_cap_s or len(rows) >= max_postings:
            break
        try:
            page = _get(f"{API.format(company=company)}?limit={PAGE}&offset={offset}")
            consec_fail = 0
        except Exception as e:  # noqa: BLE001
            consec_fail += 1
            log.info("smartrecruiters: %s offset %d failed (%s)", company, offset, e)
            if consec_fail >= MAX_CONSEC_FAIL:
                break
            time.sleep(THROTTLE)
            continue
        total = page.get("totalFound") if total is None else total
        content = page.get("content") or []
        if not content:
            break
        for p in content:
            title = (p.get("name") or "").strip()
            if not is_tech(title):
                continue
            loc = p.get("location") or {}
            cc = (loc.get("country") or "").upper()
            if cc not in STRATA:
                continue
            pid = p.get("id") or p.get("uuid")
            if not pid:
                continue
            time.sleep(THROTTLE)
            desc = _detail_desc(company, pid)
            if len(desc) < 80:
                continue
            rows.append({
                "title": title, "description": desc, "country_code": cc,
                "city": loc.get("city") or "", "remote": bool(loc.get("remote")),
                "department": (p.get("department") or {}).get("label") or "",
                "seniority": (p.get("experienceLevel") or {}).get("label") or "",
                "employment_type": (p.get("typeOfEmployment") or {}).get("label") or "",
                "employer": company,          # internal dedup aid only (roles-only)
                "kind": "advertised", "source": "smartrecruiters",
                "posted_at": p.get("releasedDate") or "",
                "retrieved": time.strftime("%Y-%m-%d"),
            })
            if len(rows) >= max_postings:
                break
        offset += PAGE
        if total is not None and offset >= total:
            break
        time.sleep(THROTTLE)

    cache.write_text(json.dumps(rows), encoding="utf-8")
    log.info("smartrecruiters: %s → %d tech postings (totalFound=%s)", company, len(rows), total)
    return rows


def build_staging(companies: list[str] | None = None, *, max_companies: int | None = None,
                  max_postings: int = 300, time_cap_s: float = 600.0,
                  force: bool = False) -> dict:
    """Fetch the seed companies → consolidated postings.parquet."""
    import pandas as pd

    comps = companies or SEED_COMPANIES
    if max_companies is not None:
        comps = comps[:max_companies]
    all_rows: list[dict] = []
    t0 = time.time()
    for c in comps:
        if time.time() - t0 > time_cap_s:
            log.info("smartrecruiters: time cap hit — stopping company loop")
            break
        try:
            all_rows.extend(fetch_company(c, max_postings=max_postings,
                                          time_cap_s=time_cap_s, force=force))
        except Exception as e:  # noqa: BLE001
            log.warning("smartrecruiters: company %s failed (%s) — skip", c, e)

    by_country: dict[str, int] = {}
    if all_rows:
        df = pd.DataFrame(all_rows).drop_duplicates(subset=["title", "employer", "city"])
        df.to_parquet(_postings_file(), index=False)
        by_country = df["country_code"].value_counts().to_dict()
        total = len(df)
    else:
        total = 0
    summary = {"companies": len(comps), "postings": total, "by_country": by_country}
    log.info("smartrecruiters staging: %s", summary)
    return summary


def load_postings():
    p = _postings_file()
    if not p.exists():
        return None
    import pandas as pd
    return pd.read_parquet(p)


def run(max_companies: int | None = None, max_postings: int = 300,
        time_cap_s: float = 600.0, force: bool = False, **kw) -> dict:
    """Land SmartRecruiters enterprise postings. Connector entrypoint (bounded)."""
    return build_staging(max_companies=max_companies, max_postings=max_postings,
                         time_cap_s=time_cap_s, force=force)


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(max_companies=1, max_postings=20), indent=2))
