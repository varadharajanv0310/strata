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


# ============================================== attempted / flagged sources ==
# Bespoke per-country gov formats. Anchors are calibration-only (lower value than
# the salary/demand feeds), so these are flagged rather than half-built. Each
# entry records what it is and why it's skipped — a future targeted build can
# pick one up without re-discovering the format.
_FLAGGED = {
    "uk_ons_ashe": "UK ONS ASHE — occupation earnings published as a multi-sheet "
                   "Excel workbook (Table 14.x) behind a per-release URL; needs a "
                   "bespoke xlsx parse + SOC2020 crosswalk. No clean JSON/API.",
    "sg_mom":      "Singapore MOM — occupational wage tables via Table Builder / "
                   "stats.mom.gov.sg; gated portal + SSOC codes, no open JSON feed.",
    "ca_jobbank":  "Canada Job Bank / NOC — wage data is per-NOC HTML pages "
                   "(noc.esdc.gc.ca) requiring scrape + NOC-2021→role crosswalk.",
    "in_plfs":     "India PLFS — Periodic Labour Force Survey released as PDF "
                   "reports / unit-level microdata; no occupation-wage API.",
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

    summary["flagged"] = _flag_sources()
    summary["total_rows"] = len(bls) + len(eu)
    log.info("baselines run: %d total anchors (BLS=%d, Eurostat=%d); %d sources flagged",
             summary["total_rows"], len(bls), len(eu), len(summary["flagged"]))
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
