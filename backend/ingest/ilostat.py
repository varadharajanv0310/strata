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

# mean nominal monthly earnings of employees by sex + occupation (ISCO-08) + currency
INDICATOR = "EAR_4MTH_SEX_OCU_CUR_NB"
# ILOSTAT uses ISO-3; map to our ISO-2.
ISO3_TO_2 = {"IND": "IN", "USA": "US", "GBR": "GB", "CAN": "CA", "AUS": "AU", "SGP": "SG", "DEU": "DE"}
# public data API (no key). Coded output so classif1 carries the ISCO-08 code.
URL = ("https://rplumber.ilo.org/data/indicator/"
       "?id={ind}&ref_area={iso3}&timefrom=2015&format=.csv")
HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}


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
    """Fetch one country's earnings-by-occupation CSV → typed rows. Best-effort."""
    url = URL.format(ind=INDICATOR, iso3=iso3)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode("utf-8", errors="replace")
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    code2 = ISO3_TO_2.get(iso3, iso3)
    for row in reader:
        classif1 = row.get("classif1") or row.get("classif1.label") or ""
        if "ISCO08" not in classif1.upper():
            continue                                  # occupation-level rows only
        val = row.get("obs_value") or row.get("value")
        try:
            earnings = float(val)
        except (TypeError, ValueError):
            continue
        rows.append({
            "country": code2,
            "isco08": _isco_from_classif(classif1),
            "year": int(float(row.get("time") or 0)) or None,
            "earnings": earnings,
            "currency": (row.get("classif2") or row.get("classif2.label") or "").replace("CUR_TYPE_", ""),
            "sex": (row.get("sex") or "").replace("SEX_", ""),
        })
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
