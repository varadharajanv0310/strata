"""Real **PPP conversion factors** from the World Bank (indicator ``PA.NUS.PPP``,
"PPP conversion factor, GDP — LCU per international $").

Replaces the last hand-picked numbers in strata's country dimension (the curated
``pppRate``, which a live probe showed were all biased ~3–9% high, India most of
all: 22.5 vs a real ~20.42). One unauthenticated request returns all 7 countries ×
2017–2025; the cached JSON is the checkpoint. Same units/divisor direction as the
existing ``pppRate`` (``pppUSD = salary / pppRate``), so it's a clean drop-in for
the cross-country PPP comparison views. ``natFactor`` is left untouched — it's a
separate, deliberate dampener for high-earner cost-of-living, not PPP.
"""
from __future__ import annotations

import json
import urllib.request

from backend.core.logging import get_logger
from backend.core.config import settings
from backend.warehouse.seed import FYEARS, YEARS

log = get_logger("ingest.worldbank_ppp")

WB_CODES = "IN;US;GB;CA;AU;SG;DE"
ISO3 = {"IND": "IN", "USA": "US", "GBR": "GB", "CAN": "CA", "AUS": "AU", "SGP": "SG", "DEU": "DE"}
OUR_CODES = set(ISO3.values())
URL = ("https://api.worldbank.org/v2/country/{codes}/indicator/PA.NUS.PPP"
       "?date=2017:2025&format=json&per_page=600")


def _staging_file():
    d = settings.staging_dir / "worldbank"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ppp.json"


def fetch_ppp(force: bool = False) -> dict:
    """Fetch + cache real PPP. Returns {code: {year: value}}; cache is the checkpoint."""
    f = _staging_file()
    if f.exists() and not force:
        return load_ppp()
    req = urllib.request.Request(URL.format(codes=WB_CODES), headers={"User-Agent": "strata/1.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        payload = json.loads(r.read().decode("utf-8"))
    rows = payload[1] if isinstance(payload, list) and len(payload) > 1 and payload[1] else []
    out: dict = {}
    for row in rows:
        code = ISO3.get(row.get("countryiso3code") or "") or (row.get("country") or {}).get("id")
        if code not in OUR_CODES:
            continue
        val = row.get("value")
        if val is None:
            continue
        out.setdefault(code, {})[int(row["date"])] = float(val)
    f.write_text(json.dumps(out), encoding="utf-8")
    log.info("World Bank PA.NUS.PPP fetched: %d countries, %d points",
             len(out), sum(len(v) for v in out.values()))
    return out


def load_ppp() -> dict:
    f = _staging_file()
    if not f.exists():
        return {}
    raw = json.loads(f.read_text(encoding="utf-8"))
    return {code: {int(y): v for y, v in yrs.items()} for code, yrs in raw.items()}


def latest_ppp(ppp: dict | None = None) -> dict:
    """{code: most-recent non-null PPP} — the headline rate for dim_country."""
    ppp = ppp if ppp is not None else load_ppp()
    return {code: yrs[max(yrs)] for code, yrs in ppp.items() if yrs}


def ppp_filled(ppp: dict | None = None) -> dict:
    """{code: {year: PPP}} across YEARS+FYEARS, carry-forward (then back-fill)."""
    ppp = ppp if ppp is not None else load_ppp()
    all_years = YEARS + FYEARS
    out: dict = {}
    for code, yrs in ppp.items():
        if not yrs:
            continue
        filled, last = {}, None
        for y in all_years:
            if y in yrs:
                last = yrs[y]
            if last is not None:
                filled[y] = last
        earliest = yrs[min(yrs)]
        for y in all_years:
            filled.setdefault(y, earliest)
        out[code] = filled
    return out
