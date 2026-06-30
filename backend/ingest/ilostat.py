"""ILOSTAT — the cross-country **wage spine**: mean nominal monthly earnings of
employees by occupation (ISCO-08), harmonized across all 7 of our countries on one
axis (council source #1).

Today each country's official wage comes from a different national agency with a
different definition (BLS OEWS vs ONS ASHE vs MOM …), so cross-country salary
comparison is apples-to-oranges. ILOSTAT publishes one harmonized indicator —
``EAR_4MTH_SEX_OCU_CUR_NB`` (mean nominal monthly earnings by sex × occupation ×
currency) — for every country, which is exactly the calibration backbone strata needs
for honest cross-country comparison. It feeds the **official salary lens**
(``fact_salary_official``) beside the national baselines.

Obtained from the public ILOSTAT data API (rplumber, no key) per country; the cached
JSON is the checkpoint. ROLES-ONLY: occupation × country × year × earnings only — no
employer anything. Mirrors the worldbank_ppp connector's shape (fetch+cache → load →
helpers). Credential-graceful; **not run in this pass** — coded for the later run.
"""
from __future__ import annotations

import csv
import io
import json
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.ilostat")

# Mean MONTHLY earnings of employees by sex + occupation (ISCO-08) + currency.
# (id confirmed live against the ILOSTAT SDMX dataflow catalog — the older
# EAR_4MTH_SEX_OCU_CUR_NB id is invalid and 400s.) NOTE: ILOSTAT publishes earnings at
# the ISCO occupation-GROUP level (mostly 1-digit major groups), so this is a coarse
# cross-country wage ANCHOR by occupation group, not a 4-digit role-specific wage.
INDICATOR = "EAR_EMTM_SEX_OCU_CUR_NB"
# ILOSTAT uses ISO-3; map to our ISO-2.
ISO3_TO_2 = {"IND": "IN", "USA": "US", "GBR": "GB", "CAN": "CA", "AUS": "AU", "SGP": "SG", "DEU": "DE"}
# local-currency (LCU) → the country's ISO currency, so the official lens labels it honestly.
LCU_CCY = {"IN": "INR", "US": "USD", "GB": "GBP", "CA": "CAD", "AU": "AUD", "SG": "SGD", "DE": "EUR"}
# public data API (no key). classif1 carries the ISCO code, classif2 the currency type.
URL = ("https://rplumber.ilo.org/data/indicator/"
       "?id={ind}&ref_area={iso3}&timefrom=2015&format=.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 strata/1.0 (+research; roles-only job-market explorer)"}


def _staging_dir():
    d = settings.staging_dir / "ilostat"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "earnings.json"


def _isco_from_classif(code: str) -> str:
    """'OCU_ISCO08_2' / 'OCU_ISCO08_251' → '2' / '251' (ISCO-08 major/sub group)."""
    c = (code or "").strip()
    if "ISCO08_" in c:
        return c.split("ISCO08_")[-1]
    return c


def _fetch_country(iso3: str, timeout: int = 60) -> list[dict]:
    """Fetch one country's earnings-by-occupation CSV → typed rows. Keeps ISCO-08
    occupation groups (not ISCO-88 or the TOTAL aggregate), SEX_T (totals, so no
    male/female duplicates), and the LOCAL-currency (LCU) series — the local official
    monthly wage, labeled with the country's ISO currency. Best-effort."""
    url = URL.format(ind=INDICATOR, iso3=iso3)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8-sig", errors="replace")   # response carries a BOM
    rows: list[dict] = []
    code2 = ISO3_TO_2.get(iso3, iso3)
    ccy = LCU_CCY.get(code2, "")
    seen: set[tuple] = set()
    for row in csv.DictReader(io.StringIO(text)):
        classif1 = (row.get("classif1") or "").upper()
        if "ISCO08_" not in classif1 or classif1.endswith("TOTAL"):
            continue                                  # ISCO-08 occupation groups only
        if (row.get("sex") or "").upper() != "SEX_T":
            continue                                  # totals only (avoid M/F dup rows)
        if "LCU" not in (row.get("classif2") or "").upper():
            continue                                  # local-currency series = the official local wage
        try:
            earnings = float(row.get("obs_value") or row.get("value"))
        except (TypeError, ValueError):
            continue
        isco = _isco_from_classif(row.get("classif1"))
        year = int(float(row.get("time") or 0)) or None
        if (isco, year) in seen:
            continue                                  # newest source wins; dedup per occ×year
        seen.add((isco, year))
        rows.append({"country": code2, "isco08": isco, "year": year,
                     "earnings": earnings, "currency": ccy, "sex": "T"})
    return rows


def fetch_earnings(force: bool = False, time_cap_s: float = 600.0) -> list[dict]:
    """Fetch + cache earnings-by-ISCO for all 7 countries. Cache is the checkpoint."""
    f = _staging_file()
    if f.exists() and not force:
        return load_earnings()
    out: list[dict] = []
    t0 = time.time()
    for iso3 in ISO3_TO_2:
        if time.time() - t0 > time_cap_s:
            log.warning("ilostat: time cap %ss hit — landing partial", time_cap_s)
            break
        try:
            rows = _fetch_country(iso3)
            out.extend(rows)
            print(f"[ilostat] {iso3}: {len(rows)} occupation×year earnings rows", flush=True)
        except Exception as e:  # noqa: BLE001 — one country must not sink the run
            log.warning("ilostat: %s fetch failed (%s) — skip", iso3, e)
    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("ILOSTAT earnings: %d rows across %d countries",
             len(out), len({r["country"] for r in out}))
    return out


def load_earnings() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache ILOSTAT earnings-by-occupation. Connector entrypoint."""
    rows = fetch_earnings(**kw)
    return {"rows": len(rows), "countries": sorted({r["country"] for r in rows}),
            "written": bool(rows)}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
