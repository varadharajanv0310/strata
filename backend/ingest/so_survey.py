"""Stack Overflow Annual Developer Survey — **real person-level salary**, 2017–2024.

Public per-year CSVs (tens of thousands of respondents/year) carrying salary +
country + role (DevType) + experience (YearsCodePro). We harmonize the shifting
schema across years, map respondents to our 7 countries / 16 roles / experience
bands, and aggregate to **median USD comp per (role, country, experience, year)**
— real multi-year salary *history*, person-level (brief: labelled person-level,
USD-converted). Outliers are trimmed; thin cells are flagged low-confidence.

Cache (the per-year zips + the aggregate JSON) is the checkpoint — resumable.
"""
from __future__ import annotations

import csv
import io
import json
import statistics
import urllib.request
import zipfile
from collections import defaultdict

from backend.core.logging import get_logger
from backend.core.config import settings

log = get_logger("ingest.so_survey")

# per-year public dataset zips (stackoverflowsolutions mirror; follow redirects)
_BASE = "https://info.stackoverflowsolutions.com/rs/719-EMH-566/images/stack-overflow-developer-survey-{y}.zip"
# Per-year URL overrides where the stackoverflowsolutions mirror is broken/missing.
# 2023: the mirror URL returned an HTML redirect page (not a zip); the real public
#       dataset lives on the Stack Overflow CDN (hash-pinned production asset).
# 2017: the mirror DOES serve a real zip, but the schema diverges (handled below).
_URL_OVERRIDE = {
    2023: "https://cdn.stackoverflow.co/files/jo7n4k8s/production/"
          "49915bfd46d0902c3564fd9a06b509d08a20488c.zip/"
          "stack-overflow-developer-survey-2023.zip",
}
# NOTE on 2024: the stackoverflowsolutions mirror's "2024" zip is byte-identical
# (same MD5) to the 2023 public dataset — i.e. it silently served 2023 data under a
# 2024 name, which previously double-counted 2023. Stack Overflow's official Sanity
# CDN (project jo7n4k8s) hosts exactly ONE survey-results zip: 2023. No key-free
# official/mirror 2024 zip was found (2024 lives only on auth-gated Kaggle). Rather
# than double-count 2023, 2024 is intentionally OMITTED until a real source lands.
# To add it later: drop the real 2024 zip at staging/so_survey/so_2024.zip (114
# cols, ~65,437 rows) and append 2024 to SO_YEARS — the standard parse branch
# handles it unchanged.
SO_YEARS = [2017, 2018, 2019, 2020, 2021, 2022, 2023]  # 2017 via divergent-schema branch; 2024 omitted (no real source — see note)

SO_COUNTRY = {
    "India": "IN",
    "United States of America": "US", "United States": "US",
    "United Kingdom of Great Britain and Northern Ireland": "GB", "United Kingdom": "GB",
    "Canada": "CA", "Australia": "AU", "Singapore": "SG", "Germany": "DE",
}
CUR = {"IN": "USD", "US": "USD", "GB": "USD", "CA": "USD", "AU": "USD", "SG": "USD", "DE": "USD"}

# DevType token -> our role_id, most-specific first (first match wins)
DEVTYPE_ROLE = [
    ("machine learning", "ml-eng"), ("data scientist", "data-sci"), ("scientist", "data-sci"),
    ("data engineer", "data-eng"), ("data or business analyst", "data-analyst"), ("analyst", "data-analyst"),
    ("engineering manager", "eng-mgr"), ("devops", "devops"), ("site reliability", "sre"),
    ("security", "security"), ("cloud", "cloud-arch"), ("mobile", "mobile"),
    ("front-end", "frontend"), ("back-end", "backend"), ("full-stack", "swe"),
    ("embedded", "swe"), ("designer", "ux"), ("product manager", "pm"),
    ("qa", "qa"), ("test", "qa"), ("developer", "swe"),
]
SALARY_FIELDS = ["ConvertedCompYearly", "ConvertedComp", "ConvertedSalary"]
EXP_FIELDS = ["YearsCodePro", "YearsCode", "YearsCodedJob"]
DEVTYPE_FIELDS = ["DevType", "DeveloperType"]
SAL_LO, SAL_HI = 1000.0, 1_000_000.0  # trim jokes/garbage

# 2017 schema diverges from 2018+: different column names and an experience field
# expressed as text bands ("9 to 10 years") rather than a numeric/range string.
# Critically, the 2017 ``Salary`` column is ALREADY normalized to USD/year by Stack
# Overflow (verified: US ~$93k, UK ~$49k, DE ~$53k, IN ~$7k medians are all
# USD-scale regardless of the respondent's day-to-day ``Currency``), so it is
# directly comparable to the later years' ``ConvertedComp*`` USD figures — no FX
# conversion is applied. ``Currency`` is retained only as documentation of intent.
SO2017_SALARY = "Salary"            # annual base salary, already USD-converted
SO2017_DEVTYPE = "DeveloperType"    # '; '-delimited multi-select
SO2017_EXP = "YearsCodedJob"        # text bands, e.g. "9 to 10 years"


def _exp_band_2017(v: str):
    """Map 2017 ``YearsCodedJob`` text bands to our experience codes."""
    s = (v or "").strip().lower()
    if not s or s == "na":
        return "pooled"
    if "less than 1" in s:
        y = 0.0
    elif "20 or more" in s or "more than" in s:
        y = 20.0
    else:
        # forms like "9 to 10 years", "1 to 2 years", "3 to 4 years" -> take lower bound
        import re as _re
        m = _re.search(r"(\d+)", s)
        if not m:
            return "pooled"
        y = float(m.group(1))
    if y <= 2:
        return "0-2"
    if y <= 5:
        return "3-5"
    if y <= 9:
        return "6-9"
    return "10+"


def _staging_dir():
    d = settings.staging_dir / "so_survey"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _zip_path(year: int):
    return _staging_dir() / f"so_{year}.zip"


def _download(year: int) -> bool:
    p = _zip_path(year)
    if p.exists() and p.stat().st_size > 100_000:
        return True
    url = _URL_OVERRIDE.get(year) or _BASE.format(y=year)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "strata/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(p, "wb") as f:
            f.write(r.read())
        log.info("SO %d downloaded (%d bytes)", year, p.stat().st_size)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("SO %d download failed: %s", year, e)
        return False


def _pick(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None


def _role_of(devtype: str):
    d = (devtype or "").lower()
    if not d:
        return None
    for tok, rid in DEVTYPE_ROLE:
        if tok in d:
            return rid
    return None


def _exp_band(v: str):
    if not v:
        return "pooled"
    s = v.strip().lower()
    if "less than 1" in s:
        y = 0.0
    elif "more than" in s or "50" in s:
        y = 50.0
    else:
        try:
            y = float(s)
        except ValueError:
            return "pooled"
    if y <= 2:
        return "0-2"
    if y <= 5:
        return "3-5"
    if y <= 9:
        return "6-9"
    return "10+"


def fetch_and_aggregate() -> dict:
    """Download missing years, parse, aggregate to median USD comp per cell. Returns summary."""
    import time as _time
    t0 = _time.time()
    # cell key -> list of salaries.  cell = (role, country, exp, year); also exp='pooled'
    buckets: dict[tuple, list[float]] = defaultdict(list)
    per_year_rows: dict[int, int] = {}
    run_total = 0
    for year in SO_YEARS:
        if not _download(year):
            print(f"[so_survey] {year}: download FAILED (skip)  elapsed={_time.time()-t0:.0f}s", flush=True)
            continue
        try:
            z = zipfile.ZipFile(_zip_path(year))
        except Exception as e:  # noqa: BLE001
            log.error("SO %d bad zip: %s", year, e)
            print(f"[so_survey] {year}: bad zip ({e}) (skip)", flush=True)
            continue
        names = [n for n in z.namelist() if n.endswith("public.csv")]
        if not names:
            log.warning("SO %d: no public.csv", year)
            print(f"[so_survey] {year}: no public.csv (skip)", flush=True)
            continue
        reader = csv.DictReader(io.TextIOWrapper(z.open(names[0]), encoding="utf-8"))
        cols = reader.fieldnames or []

        # 2017 diverges: Salary (already USD), DeveloperType ('; ' multiselect),
        # YearsCodedJob (text bands). Resolve its columns + experience parser here.
        is_2017 = year == 2017
        if is_2017:
            sal_c, dev_c, exp_c = SO2017_SALARY, SO2017_DEVTYPE, SO2017_EXP
            exp_fn = _exp_band_2017
        else:
            sal_c = _pick(cols, SALARY_FIELDS)
            dev_c = _pick(cols, DEVTYPE_FIELDS)
            exp_c = _pick(cols, EXP_FIELDS)
            exp_fn = _exp_band
        if not (sal_c in cols and dev_c in cols and "Country" in cols):
            log.warning("SO %d: missing columns (sal=%s dev=%s)", year, sal_c, dev_c)
            print(f"[so_survey] {year}: missing columns (sal={sal_c} dev={dev_c}) (skip)", flush=True)
            continue
        kept = 0
        for row in reader:
            code = SO_COUNTRY.get((row.get("Country") or "").strip())
            if not code:
                continue
            raw = (row.get(sal_c) or "").strip()
            if raw in ("", "NA"):
                continue
            try:
                sal = float(raw)
            except ValueError:
                continue
            if not (SAL_LO <= sal <= SAL_HI):
                continue
            rid = _role_of(row.get(dev_c, ""))
            if not rid:
                continue
            band = exp_fn(row.get(exp_c, "")) if exp_c else "pooled"
            buckets[(rid, code, "pooled", year)].append(sal)
            if band != "pooled":
                buckets[(rid, code, band, year)].append(sal)
            kept += 1
        per_year_rows[year] = kept
        run_total += kept
        # streaming heartbeat: per-year kept + running total + per-country tally + elapsed
        pc = defaultdict(int)
        for (rid, c, exp, yr), v in buckets.items():
            if yr == year and exp == "pooled":
                pc[c] += len(v)
        pc_str = " ".join(f"{c}={pc[c]}" for c in sorted(pc))
        print(f"[so_survey] {year}: kept {kept}  run_total={run_total}  "
              f"[{pc_str}]  elapsed={_time.time()-t0:.0f}s", flush=True)
        log.info("SO %d: kept %d person-rows (our 7 countries, mapped role+salary)", year, kept)

    # aggregate -> median per cell (min sample 5)
    records = []
    for (rid, code, exp, year), vals in buckets.items():
        if len(vals) < 5:
            continue
        med = statistics.median(vals)
        n = len(vals)
        conf = "high" if n >= 200 else "med" if n >= 40 else "low"
        records.append({
            "role_id": rid, "country_code": code, "experience_code": exp, "year": year,
            "median": round(med), "currency_code": "USD", "sample_size": n,
            "confidence": conf, "kind": "person-level", "source": "Stack Overflow Survey",
        })
    out = _staging_dir() / "salary_agg.json"
    out.write_text(json.dumps(records), encoding="utf-8")
    summary = {
        "years": sorted(per_year_rows),
        "person_rows_per_year": per_year_rows,
        "total_person_rows": sum(per_year_rows.values()),
        "cells": len(records),
        "pooled_cells": sum(1 for r in records if r["experience_code"] == "pooled"),
    }
    log.info("SO survey aggregated: %s", summary)
    return summary


def load_agg() -> list:
    p = _staging_dir() / "salary_agg.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
