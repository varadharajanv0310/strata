"""Official **salary baselines** — government statistical wage tables used as
*calibration anchors* (keep the live Adzuna/SO numbers honest, brief: "official
aggregate salary anchors"). These are lower-value than the salary/demand feeds
(they don't drive the headline cards), so the design here is: build the two most
automatable national sources **solidly**, attempt the rest, and *flag* the
genuinely bespoke gov formats rather than burn an overnight run on six one-off
scrapers.

Built solid (real, key-free public APIs):
  * **BLS OEWS (US)** — national mean + median annual wage per SOC occupation,
    crosswalked SOC→role (same idea as the H-1B SOC map). api.bls.gov, JSON.
    NOTE: the BLS API silently returns empty results unless a ``User-Agent`` is
    sent, and the unregistered tier is ~25 queries/day — so we batch ≤50 series
    per query and cache aggressively (the cache is the checkpoint; a re-run never
    re-spends quota for SOCs already on disk). A registered ``BLS_API_KEY`` (env)
    is used if present to lift the quota, but is *optional*.
  * **Eurostat (DE / EU)** — Structure-of-Earnings-Survey mean annual earnings by
    occupation (ISCO-08), dataset ``earn_ses18_28``. One unauthenticated request
    per ISCO major group. Roles map to ISCO 1-digit groups (Managers / Professionals
    / Technicians) — coarse, but real official ground truth for DE.

Attempted, flagged-and-skipped (bespoke per-country formats, see ``_FLAGGED``):
  UK ONS ASHE, Singapore MOM, Canada Job Bank/NOC, India PLFS, ai-jobs.net.
  Each is a one-off Excel/HTML/portal scrape; given anchors are calibration-only
  we record *why* it's skipped instead of half-building a brittle scraper.

Lands one JSON per source under ``staging/baselines/<source>.json`` as
``[{role_id, country_code, year, median, currency_code, source}]`` (BLS also
carries ``mean``). ``run()`` is the orchestrator entry point.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.baselines")

# ---------------------------------------------------------------- staging ----

def _staging_dir():
    d = settings.staging_dir / "baselines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(source: str, rows: list[dict]) -> None:
    (_staging_dir() / f"{source}.json").write_text(json.dumps(rows), encoding="utf-8")


def _load(source: str) -> list[dict]:
    p = _staging_dir() / f"{source}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


# ============================================================= BLS OEWS (US) ==
# role_id -> 2018 SOC code (no dash). Same crosswalk idea as the H-1B SOC map:
# pick the published OEWS occupation that best matches the role. Several roles
# (data-eng, backend, mobile) share 15-1252 "Software Developers" — OEWS has no
# finer split; data-analyst shares 15-2051 "Data Scientists". Overlaps are real
# and expected for a coarse official anchor; they're flagged in the summary.
BLS_SOC = {
    "ml-eng":      "151221",  # Computer & Information Research Scientists
    "swe":         "151252",  # Software Developers
    "data-eng":    "151252",  # Software Developers (no distinct SOC)
    "frontend":    "151254",  # Web Developers
    "backend":     "151252",  # Software Developers
    "data-sci":    "152051",  # Data Scientists
    "devops":      "151244",  # Network & Computer Systems Administrators
    "sre":         "151244",  # (proxy) Network & Computer Systems Administrators
    "security":    "151212",  # Information Security Analysts
    "cloud-arch":  "151241",  # Computer Network Architects
    "pm":          "113021",  # Computer & Information Systems Managers (proxy)
    "ux":          "151255",  # Web & Digital Interface Designers
    "mobile":      "151252",  # Software Developers
    "data-analyst":"152051",  # Data Scientists (no distinct OEWS Data Analyst SOC)
    "eng-mgr":     "113021",  # Computer & Information Systems Managers
    "qa":          "151253",  # Software QA Analysts & Testers
}
BLS_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
# National (areatype N), all industries: OE U N 0000000 000000 <SOC6> <datatype2>
_OEWS_MEAN, _OEWS_MEDIAN = "04", "13"  # annual mean / annual median wage


# OEWS national series id (25 chars): OE U N + area(7×0) + industry(6×0) + SOC(6) + datatype(2)
_OEWS_PREFIX = "OEUN" + "0000000" + "000000"  # 17 chars


def _oews_series(soc: str, datatype: str) -> str:
    return f"{_OEWS_PREFIX}{soc}{datatype}"


def _bls_post(series_ids: list[str], start: str, end: str) -> dict:
    payload = {"seriesid": series_ids, "startyear": start, "endyear": end}
    key = os.environ.get("BLS_API_KEY", "").strip()
    if key:
        payload["registrationkey"] = key
    body = json.dumps(payload).encode("utf-8")
    # User-Agent is REQUIRED — without it the BLS API returns empty data arrays.
    req = urllib.request.Request(
        BLS_BASE, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "strata/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_bls(start_year: str = "2024", end_year: str = "2025") -> list[dict]:
    """US OEWS mean+median annual wage per role-SOC. Cached + key-optional.

    Resumable: if every role already has a value cached on disk we return it
    without spending API quota. Batches ≤50 series/query (BLS limit).
    """
    cached = _load("bls_oews")
    if cached:
        log.info("BLS OEWS: %d role-anchors already cached — skip refetch", len(cached))
        return cached

    socs = sorted(set(BLS_SOC.values()))
    series = [_oews_series(s, dt) for s in socs for dt in (_OEWS_MEAN, _OEWS_MEDIAN)]
    by_soc: dict[str, dict] = {}
    # The registered tier allows 50 series/query; the unregistered tier (no key)
    # silently returns empty data arrays for large batches, so keep chunks small
    # when no key is set. ~20 distinct SOCs -> a handful of small queries.
    chunk_size = 50 if os.environ.get("BLS_API_KEY", "").strip() else 10
    for i in range(0, len(series), chunk_size):
        chunk = series[i:i + chunk_size]
        data = None
        for attempt in range(4):
            try:
                resp = _bls_post(chunk, start_year, end_year)
                if resp.get("status") != "REQUEST_SUCCEEDED":
                    raise RuntimeError(resp.get("message") or resp.get("status"))
                data = resp["Results"]["series"]
                break
            except Exception as e:  # noqa: BLE001 — resilient: backoff then give up
                if attempt == 3:
                    log.error("BLS chunk %d failed permanently: %s", i // chunk_size, e)
                else:
                    time.sleep(3 * (2 ** attempt))
        if data is None:
            continue
        for s in data:
            sid = s["seriesID"]
            soc, dt = sid[17:23], sid[23:25]
            pts = s.get("data") or []
            if not pts:
                continue
            # newest non-empty value (data comes newest-first)
            val = next((p["value"] for p in pts if p.get("value")), None)
            if val is None:
                continue
            yr = next((int(p["year"]) for p in pts if p.get("value")), None)
            rec = by_soc.setdefault(soc, {"year": yr})
            rec["mean" if dt == _OEWS_MEAN else "median"] = float(val)
            if yr and (rec.get("year") or 0) < yr:
                rec["year"] = yr
        time.sleep(1.0)

    rows: list[dict] = []
    for role_id, soc in BLS_SOC.items():
        rec = by_soc.get(soc)
        if not rec or rec.get("median") is None and rec.get("mean") is None:
            continue
        # OEWS publishes both; median is the calibration anchor, mean kept alongside
        median = rec.get("median") or rec.get("mean")
        rows.append({
            "role_id": role_id, "country_code": "US", "year": rec.get("year"),
            "median": median, "mean": rec.get("mean"),
            "currency_code": "USD", "soc": soc, "source": "BLS OEWS",
        })
    if rows:
        _write("bls_oews", rows)
        log.info("BLS OEWS: landed %d US role-anchors (%d distinct SOCs)",
                 len(rows), len(by_soc))
    else:
        log.warning("BLS OEWS: 0 anchors — likely daily quota exhausted "
                    "(unregistered tier ~25 queries/day). Re-run picks up fresh quota; "
                    "set BLS_API_KEY to lift the limit.")
    return rows


# ============================================================= Eurostat (DE) ==
# role_id -> ISCO-08 major group (the finest split earn_ses18_28 publishes).
# OC1 Managers, OC2 Professionals (incl. ICT professionals — most tech roles),
# OC3 Technicians & associate professionals.
EU_ISCO = {
    "ml-eng": "OC2", "swe": "OC2", "data-eng": "OC2", "frontend": "OC2",
    "backend": "OC2", "data-sci": "OC2", "devops": "OC2", "sre": "OC2",
    "security": "OC2", "cloud-arch": "OC2", "ux": "OC2", "mobile": "OC2",
    "data-analyst": "OC2",
    "pm": "OC1", "eng-mgr": "OC1",            # Managers
    "qa": "OC3",                              # Technicians / associate professionals
}
EU_BASE = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
           "earn_ses18_28")
EU_YEAR = "2018"  # SES is quadrennial; 2018 is the latest fully published wave


def _eurostat_value(isco: str, geo: str = "DE") -> tuple[float | None, int]:
    """Mean annual earnings (EUR) for one ISCO group; returns (value, year)."""
    q = {
        "format": "JSON", "geo": geo, "time": EU_YEAR, "isco08": isco,
        "indic_se": "ERN", "sex": "T", "age": "TOTAL",
        "sizeclas": "TOTAL", "unit": "EUR",
    }
    url = f"{EU_BASE}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "strata/1.0"})
    with urllib.request.urlopen(req, timeout=50) as r:
        d = json.loads(r.read().decode("utf-8"))
    if "error" in d:
        raise RuntimeError(d["error"])
    vals = d.get("value") or {}
    if not vals:
        return None, int(EU_YEAR)
    # single-cell query -> one value
    v = next(iter(vals.values()))
    return float(v), int(EU_YEAR)


def fetch_eurostat() -> list[dict]:
    """DE mean annual earnings per role via ISCO-08 group. Cached + resumable."""
    cached = _load("eurostat")
    if cached:
        log.info("Eurostat: %d role-anchors already cached — skip refetch", len(cached))
        return cached

    iscos = sorted(set(EU_ISCO.values()))
    by_isco: dict[str, tuple[float | None, int]] = {}
    for isco in iscos:
        for attempt in range(4):
            try:
                by_isco[isco] = _eurostat_value(isco)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    log.error("Eurostat ISCO %s failed: %s", isco, e)
                    by_isco[isco] = (None, int(EU_YEAR))
                else:
                    time.sleep(2 ** attempt)
        time.sleep(0.5)

    rows: list[dict] = []
    for role_id, isco in EU_ISCO.items():
        val, yr = by_isco.get(isco, (None, int(EU_YEAR)))
        if val is None:
            continue
        rows.append({
            "role_id": role_id, "country_code": "DE", "year": yr,
            "median": val, "currency_code": "EUR", "isco08": isco,
            "source": "Eurostat SES",
        })
    if rows:
        _write("eurostat", rows)
        log.info("Eurostat: landed %d DE role-anchors (%d ISCO groups)",
                 len(rows), sum(1 for v in by_isco.values() if v[0] is not None))
    else:
        log.warning("Eurostat: 0 anchors landed")
    return rows


# ============================================================ UK ONS ASHE ====
# ASHE Table 14 (4-digit SOC2020), sub-table 14.7a "Annual pay - Gross" — real
# annual gross GBP **median** per occupation, full-time. Published as a multi-sheet
# xlsx inside a per-release zip; we read the "Full-Time" sheet (Description | Code |
# Number | Median | ...). SOC2020 IT unit groups crosswalk cleanly to our roles
# (much finer than the Eurostat/StatCan broad-group anchors), so this lands solid.
ONS_ASHE_ZIP = ("https://www.ons.gov.uk/file?uri=/employmentandlabourmarket/"
                "peopleinwork/earningsandworkinghours/datasets/"
                "occupation4digitsoc2010ashetable14/2025provisional/"
                "ashetable142025provisional.zip")
ONS_YEAR = 2025
# role_id -> SOC2020 4-digit unit group (most specific published match).
ONS_SOC = {
    "swe":         "2134",  # Programmers and software development professionals
    "backend":     "2134",
    "frontend":    "2134",
    "mobile":      "2134",
    "data-eng":    "2134",
    "ml-eng":      "2134",  # no distinct SOC; shares software-dev group
    "cloud-arch":  "2133",  # IT business analysts, architects and systems designers
    "data-sci":    "2133",  # (proxy) architects/analysts group — no SOC "data scientist"
    "security":    "2135",  # Cyber security professionals
    "qa":          "2136",  # IT quality and testing professionals
    "devops":      "2137",  # IT network professionals (proxy)
    "sre":         "2137",  # (proxy) IT network professionals
    "data-analyst":"3544",  # Data analysts
    "ux":          "2141",  # Web design professionals (proxy for UX/design)
    "pm":          "2131",  # IT project managers
    "eng-mgr":     "1137",  # Information technology directors
}


def fetch_uk_ons() -> list[dict]:
    """UK annual gross median per role via ONS ASHE Table 14.7a (SOC2020). Cached."""
    import io as _io
    import zipfile as _zip
    cached = _load("uk_ons_ashe")
    if cached:
        log.info("UK ONS ASHE: %d role-anchors already cached — skip refetch", len(cached))
        return cached
    try:
        import openpyxl  # noqa: F401
    except Exception as e:  # noqa: BLE001
        log.error("UK ONS ASHE: openpyxl unavailable (%s) — skipped", e)
        return []
    import openpyxl
    try:
        req = urllib.request.Request(ONS_ASHE_ZIP, headers={"User-Agent": "strata/1.0"})
        data = urllib.request.urlopen(req, timeout=180).read()
        z = _zip.ZipFile(_io.BytesIO(data))
        name = next(n for n in z.namelist()
                    if "14.7a" in n and "Annual pay - Gross" in n)
        wb = openpyxl.load_workbook(_io.BytesIO(z.read(name)), read_only=True, data_only=True)
        ws = wb["Full-Time"]
    except Exception as e:  # noqa: BLE001
        log.error("UK ONS ASHE: download/parse failed (%s) — skipped", e)
        return []

    # build SOC code -> median GBP map (Description col0, Code col1, Median col3)
    by_soc: dict[str, float] = {}
    for r in ws.iter_rows(min_row=5, values_only=True):
        code = str(r[1]).strip() if r[1] is not None else ""
        med = r[3]
        if code and isinstance(med, (int, float)):
            by_soc[code] = float(med)

    rows: list[dict] = []
    for role_id, soc in ONS_SOC.items():
        med = by_soc.get(soc)
        if med is None:
            continue
        rows.append({
            "role_id": role_id, "country_code": "GB", "year": ONS_YEAR,
            "median": round(med), "currency_code": "GBP", "soc2020": soc,
            "source": "UK ONS ASHE",
        })
    if rows:
        _write("uk_ons_ashe", rows)
        log.info("UK ONS ASHE: landed %d GB role-anchors (%d distinct SOC2020 groups)",
                 len(rows), len(set(ONS_SOC[r["role_id"]] for r in rows)))
    else:
        log.warning("UK ONS ASHE: 0 anchors landed")
    return rows


# ============================================================ Singapore MOM ===
# data.gov.sg open dataset d_ec5d0e... "Median Monthly Basic and Gross Wages of
# Selected Occupations by Industry" (source: MOM Survey on Annual Wage Changes).
# Key-free CKAN JSON API. Values are MONTHLY gross SGD per (occupation, industry);
# we take the Information & Communications industry where present (else median
# across industries) and annualise ×12. Occupation names map to our roles.
SG_DATASET = "d_ec5d0e4ebdd2baee2a5aa1322a3156a5"
SG_API = "https://data.gov.sg/api/action/datastore_search"
# role_id -> MOM occ_desc (exact string in the dataset).
SG_OCC = {
    "swe":      "software, web and multimedia developer",
    "backend":  "software, web and multimedia developer",
    "frontend": "software, web and multimedia developer",
    "mobile":   "software, web and multimedia developer",
    "data-eng": "software, web and multimedia developer",
    "ml-eng":   "software, web and multimedia developer",
    "data-sci": "data scientist",
    "devops":   "network, servers and computer systems administrator",
    "sre":      "network, servers and computer systems administrator",
    "eng-mgr":  "software and applications manager",
    "pm":       "software and applications manager",
    "cloud-arch": "systems analyst",
}
SG_PREF_IND = "information & communications"  # most representative industry for tech


def _sg_fetch_all() -> list[dict]:
    """Fetch the ENTIRE MOM wages dataset in one bulk call (~831 rows).

    data.gov.sg rate-limits per-request aggressively (HTTP 429), so issuing one
    query per occupation reliably starves the high-cardinality ones. The dataset
    is tiny (<1000 rows), so a single ``limit=1000`` pull gets everything and we
    filter in-Python — one request instead of a dozen.
    """
    url = f"{SG_API}?" + urllib.parse.urlencode({"resource_id": SG_DATASET, "limit": 1000})
    for attempt in range(8):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strata/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=90).read())
            return d["result"]["records"]
        except Exception:  # noqa: BLE001 — data.gov.sg rate-limits; back off
            time.sleep(4.0 * (attempt + 1))
    return []


def fetch_singapore() -> list[dict]:
    """SG annual gross median per role via data.gov.sg MOM wages. Cached + resumable."""
    cached = _load("sg_mom")
    if cached:
        log.info("Singapore MOM: %d role-anchors already cached — skip refetch", len(cached))
        return cached

    all_recs = _sg_fetch_all()
    if not all_recs:
        log.warning("Singapore MOM: bulk fetch returned 0 rows (rate-limited?) — re-run picks up")
        return []

    def _gross(r):
        try:
            return float(r.get("mthly_gross_wage_50_pctile"))
        except (TypeError, ValueError):
            return None

    # occupation -> (annual_sgd, year), computed once from the bulk pull
    occ_cache: dict[str, tuple[int, int] | None] = {}
    for occ in set(SG_OCC.values()):
        recs = [r for r in all_recs if r.get("occ_desc") == occ]
        if not recs:
            occ_cache[occ] = None
            log.warning("Singapore MOM: no rows for occ '%s'", occ)
            continue
        latest_year = max(int(r["year"]) for r in recs)
        latest = [r for r in recs if int(r["year"]) == latest_year]
        pref = [r for r in latest if r.get("ind") == SG_PREF_IND]
        pool = pref or latest
        vals = sorted(v for v in (_gross(r) for r in pool) if v)
        if not vals:
            occ_cache[occ] = None
            continue
        monthly = vals[len(vals) // 2]  # median across the chosen industry pool
        occ_cache[occ] = (round(monthly * 12), latest_year)

    rows: list[dict] = []
    for role_id, occ in SG_OCC.items():
        got = occ_cache.get(occ)
        if not got:
            continue
        annual, yr = got
        rows.append({
            "role_id": role_id, "country_code": "SG", "year": yr,
            "median": annual, "currency_code": "SGD", "mom_occ": occ,
            "source": "Singapore MOM",
        })
    if rows:
        _write("sg_mom", rows)
        log.info("Singapore MOM: landed %d SG role-anchors (%d distinct occupations)",
                 len(rows), sum(1 for v in occ_cache.values() if v))
    else:
        log.warning("Singapore MOM: 0 anchors landed")
    return rows


# ============================================================ Canada StatCan ==
# StatCan WDS REST API, table 14-10-0417-01 "Employee wages by occupation, annual".
# Key-free JSON. NOC is published only at broad-category granularity here (no finer
# software-developer split — same coarseness as the Eurostat ISCO anchor), so tech
# roles map to NOC member 18 "Professional occupations in engineering" / 17
# "Professional occupations in applied sciences". We read the MEDIAN HOURLY wage
# (full-time, age 25-54, both genders, Canada) and annualise ×2080 (52×40h).
CA_WDS = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromCubePidCoordAndLatestNPeriods"
CA_PID = 14100417
CA_HOURS_YR = 2080
# role_id -> NOC broad-category member id (18 engineering / 17 applied sciences).
CA_NOC = {
    "ml-eng": 17, "swe": 18, "data-eng": 18, "frontend": 18, "backend": 18,
    "data-sci": 17, "devops": 18, "sre": 18, "security": 18, "cloud-arch": 18,
    "mobile": 18, "data-analyst": 17, "qa": 18,
    # pm / eng-mgr / ux have no clean NOC-broad-category proxy here -> omitted
}


def _ca_coord(noc: int) -> str:
    # dims: Geography . Wages . TypeOfWork . NOC . Gender . AgeGroup (+ pad to 10)
    # Canada(1) . MedianHourly(4) . FullTime(2) . NOC . Total(1) . 25-54(3)
    return f"1.4.2.{noc}.1.3.0.0.0.0"


def fetch_canada() -> list[dict]:
    """CA annual median per role via StatCan 14-10-0417 (median hourly ×2080). Cached."""
    cached = _load("ca_statcan")
    if cached:
        log.info("Canada StatCan: %d role-anchors already cached — skip refetch", len(cached))
        return cached

    H = {"User-Agent": "strata/1.0", "Content-Type": "application/json",
         "Accept": "application/json"}
    by_noc: dict[int, tuple[float, int]] = {}
    for noc in sorted(set(CA_NOC.values())):
        body = json.dumps([{"productId": CA_PID, "coordinate": _ca_coord(noc),
                            "latestN": 1}]).encode("utf-8")
        for attempt in range(4):
            try:
                req = urllib.request.Request(CA_WDS, data=body, headers=H)
                d = json.loads(urllib.request.urlopen(req, timeout=90).read())
                o = d[0]
                if o.get("status") != "SUCCESS":
                    raise RuntimeError(o.get("status"))
                pts = o["object"]["vectorDataPoint"]
                if pts and pts[0].get("value") is not None:
                    yr = int(str(pts[0]["refPer"])[:4])
                    by_noc[noc] = (float(pts[0]["value"]), yr)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 3:
                    log.error("Canada StatCan NOC %d failed: %s", noc, e)
                else:
                    time.sleep(2 * (attempt + 1))
        time.sleep(0.8)

    rows: list[dict] = []
    for role_id, noc in CA_NOC.items():
        got = by_noc.get(noc)
        if not got:
            continue
        hourly, yr = got
        rows.append({
            "role_id": role_id, "country_code": "CA", "year": yr,
            "median": round(hourly * CA_HOURS_YR), "currency_code": "CAD",
            "noc_group": noc, "median_hourly": hourly, "source": "StatCan 14-10-0417",
        })
    if rows:
        _write("ca_statcan", rows)
        log.info("Canada StatCan: landed %d CA role-anchors (%d NOC groups)",
                 len(rows), len(by_noc))
    else:
        log.warning("Canada StatCan: 0 anchors landed")
    return rows


# ============================================== attempted / flagged sources ==
# The genuinely bespoke remainder. Anchors are calibration-only, so these are
# flagged with what they are + why, rather than half-built behind a brittle scraper.
_FLAGGED = {
    "in_plfs": "India PLFS — the Periodic Labour Force Survey publishes only "
               "aggregate regular-wage earnings (e.g. ~Rs 24k/month all-occupations), "
               "not an occupation-level IT wage table, and is released as PDF reports / "
               "unit-level microdata with no occupation-wage API. NASSCOM's IT comp "
               "benchmarking is a gated Deloitte survey PDF (not an open feed). No "
               "key-free machine-readable occupation-wage source exists for IN.",
    "ai_jobs_net": "ai-jobs.net — community salary CSV (not official/government); "
                   "useful but redundant with SO + Adzuna real feeds and not a "
                   "calibration *anchor*, so deprioritized.",
}


def _flag_sources() -> dict:
    out = {}
    for name, reason in _FLAGGED.items():
        out[name] = {"status": "flagged-skipped", "reason": reason}
        log.info("baseline source %s flagged-skipped: %s", name, reason.split(" — ")[0])
    return out


# ===================================================================== run ===

def run(start_year: str = "2024", end_year: str = "2025") -> dict:
    """Build all baseline anchors. Orchestrator entry point (collect_all).

    Returns a summary: per-source real row counts + flagged sources + why.
    Idempotent/resumable via the per-source staging JSON cache.
    """
    summary: dict = {"sources": {}, "flagged": {}, "total_rows": 0}

    bls = fetch_bls(start_year, end_year)
    summary["sources"]["bls_oews"] = {
        "status": "ok" if bls else "partial", "rows": len(bls), "country": "US",
    }

    eu = fetch_eurostat()
    summary["sources"]["eurostat"] = {
        "status": "ok" if eu else "partial", "rows": len(eu), "country": "DE",
    }

    ons = fetch_uk_ons()
    summary["sources"]["uk_ons_ashe"] = {
        "status": "ok" if ons else "partial", "rows": len(ons), "country": "GB",
    }

    sg = fetch_singapore()
    summary["sources"]["sg_mom"] = {
        "status": "ok" if sg else "partial", "rows": len(sg), "country": "SG",
    }

    ca = fetch_canada()
    summary["sources"]["ca_statcan"] = {
        "status": "ok" if ca else "partial", "rows": len(ca), "country": "CA",
    }

    summary["flagged"] = _flag_sources()
    summary["total_rows"] = len(bls) + len(eu) + len(ons) + len(sg) + len(ca)
    log.info("baselines run: %d total anchors (BLS=%d, Eurostat=%d, ONS=%d, MOM=%d, "
             "StatCan=%d); %d sources flagged", summary["total_rows"], len(bls),
             len(eu), len(ons), len(sg), len(ca), len(summary["flagged"]))
    return summary


def load_all() -> list[dict]:
    """All landed anchors across sources — for the fusion/calibration step."""
    rows: list[dict] = []
    for f in _staging_dir().glob("*.json"):
        try:
            rows.extend(json.loads(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return rows
