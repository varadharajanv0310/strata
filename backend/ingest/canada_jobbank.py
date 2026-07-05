"""Canada Job Bank wages — the CA **official** wage anchor (roles-only).

Canada's Job Bank (Employment and Social Development Canada) publishes an open,
credential-free wage dataset on ``open.canada.ca``: for every NOC-2021 occupation ×
geography (national / provincial / economic-region) it gives low / median / high
wage estimates plus Q1/Q3 quartiles and an average, sourced from the Labour Force
Survey and the 2021 Census. This is government-published *official* wage data — not
an aggregator estimate — so rows land ``kind="official"`` (source_id="canada-jobbank"),
the CA counterpart of the US H-1B / German Entgeltatlas official lenses.

Where the numbers live (recon-verified 2026-07-05): a single yearly CSV,
``.../resource/<id>/download/2a71-das-wage<YYYY>opendata-esdc-all-<date>-vf.csv``.
Columns (real header): ``NOC_CNP`` (``NOC_21232`` = the 5-digit NOC-2021 code),
``NOC_Title_eng``, ``prov`` (``NAT`` = national, else province), ``ER_Name``
(economic region), ``Low_/Median_/High_Wage``, ``Quartile1_/Quartile3_Wage``,
``Average_Wage``, ``Annual_Wage_Flag_Salaire_annuel`` (``1`` already annual, ``0``
hourly → ×2080 to annualize), ``Data_Source_E``, ``Reference_Period``. Wage cells
are blank when the estimate is suppressed for confidentiality.

We keep only the **national** (``prov="NAT"``) row per occupation — one clean
CA-wide figure per NOC, no per-region fan-out — and only the tech/IT NOC cluster
(the ``212xx`` computing occupations + computer engineers + IT managers), matching
strata's tech-job-market charter. hourly medians/quartiles annualize at ×2080
(~40 h/wk × 52 wk, the standard full-time-equivalent Job Bank itself uses when it
presents an annualized figure), so ``basis="base-annual"``, ``currency="CAD"``.

Resolution: each NOC title is resolved to one of our roles via the sanctioned
``match_title_to_role`` (with a plural/clause-split retry, since NOC labels are
plural compounds like "Software developers and programmers"); rows that don't
resolve are **kept with role_id=null** (NOC code + surface preserved) so the
catalog can bind them later. ``yoe`` is always null — the dataset has no experience
axis; no years are invented from anything.

Fetch-once: the yearly CSV caches to ``staging/canada_jobbank/`` and is never
re-downloaded (the cache IS the checkpoint; a 404/miss caches an empty marker too).
Polite: desktop Chrome UA, ~2.5 s jittered throttle between the (few) file GETs,
consecutive-failure circuit breaker. ROLES-ONLY: occupation-level wages only — the
dataset carries no employer names or company data to begin with.
"""
from __future__ import annotations

import csv
import json
import random
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.taxonomy import match_title_to_role

log = get_logger("ingest.canada_jobbank")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between file GETs (jittered ±40%)
MAX_CONSEC_FAIL = 3       # transport-failure circuit breaker
ANNUAL_HOURS = 2080.0     # full-time-equivalent hours/yr (40h × 52wk) for hourly→annual

# open.canada.ca "Wages" dataset (adad580f-…): one open CSV per year. Newest first —
# we take the first URL that downloads; the rest are the documented fallback pattern.
# If ESDC rotates the resource id / filename, update this list (the dataset landing
# page is https://open.canada.ca/data/en/dataset/adad580f-76b0-4502-bd05-20c125de9116).
# TODO: refresh the top URL each year from the dataset page when the new vintage drops.
CSV_URLS: list[tuple[int, str]] = [
    (2025, "https://open.canada.ca/data/dataset/adad580f-76b0-4502-bd05-20c125de9116/"
           "resource/9da94d63-b178-4a64-aeb3-b6a3bd721ad2/download/"
           "2a71-das-wage2025opendata-esdc-all-19nov2025-vf.csv"),
    (2024, "https://open.canada.ca/data/dataset/adad580f-76b0-4502-bd05-20c125de9116/"
           "resource/d16e10ea-77bf-4db8-bdb5-adc709e6cada/download/"
           "2a71-das-wage2024opendata-esdc-all-11dec2024-vf.csv"),
    (2023, "https://open.canada.ca/data/dataset/adad580f-76b0-4502-bd05-20c125de9116/"
           "resource/ff45366b-1c17-4862-8325-f6e7797c7c56/download/"
           "2a71-das-wage2023opendata-esdc-all-08dec2023-vf.csv"),
]

# Tech/IT NOC-2021 cluster we keep (strata is a tech job-market explorer, and the
# CSV carries ~500 occupations across every sector). The 212xx computing block, plus
# computer engineers (21311) and the computer/information-systems manager (20012).
# A code not in here is dropped; a kept code that doesn't title-resolve still lands
# with role_id=null (so it can be bound later) — honest coverage, tech-scoped.
TECH_NOC: set[str] = {
    "20012",  # Computer and information systems managers
    "21210",  # Mathematicians, statisticians and actuaries
    "21211",  # Data scientists
    "21220",  # Cybersecurity specialists
    "21221",  # Business systems specialists
    "21222",  # Information systems specialists
    "21223",  # Database analysts and data administrators
    "21230",  # Computer systems developers and programmers
    "21231",  # Software engineers and designers
    "21232",  # Software developers and programmers
    "21233",  # Web designers
    "21234",  # Web developers and programmers
    "21311",  # Computer engineers (except software engineers and designers)
    "22220",  # Computer network and web technicians
    "22221",  # User support technicians
}


def _staging_dir():
    d = settings.staging_dir / "canada_jobbank"
    (d / "csv").mkdir(parents=True, exist_ok=True)
    return d


def _csv_path(year: int):
    return _staging_dir() / "csv" / f"wage{year}.csv"


def _fetch_csv(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "text/csv,*/*",
                                               "Accept-Language": "en-CA,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _resolve_role(title: str) -> str | None:
    """NOC occupation title → our role_id via the sanctioned resolver.

    NOC labels are plural compounds ("Software developers and programmers"), which
    the singular-alias resolver misses on a straight pass — so we retry each
    ``and`` / ``/`` / ``,``-split clause, de-pluralized, through the SAME
    ``match_title_to_role``. No fabricated code→role mapping: unresolved titles
    stay null (kept downstream)."""
    r = match_title_to_role(title)
    if r:
        return r
    for clause in re.split(r"\band\b|/|,", title):
        clause = clause.strip()
        if not clause:
            continue
        depluralized = " ".join(re.sub(r"s$", "", w) for w in clause.split())
        r = match_title_to_role(depluralized) or match_title_to_role(clause)
        if r:
            return r
    return None


def _num(s: str | None) -> float | None:
    """Parse a wage cell → float, or None when blank/suppressed."""
    if s is None:
        return None
    s = s.strip()
    if not s or s.upper() in ("N/A", "NA"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_page(csv_bytes: bytes, year: int) -> list[dict]:
    """Job Bank wage CSV → national tech-occupation estimate rows (roles-only).

    Keeps only ``prov="NAT"`` rows (one CA-wide figure per NOC) whose NOC is in the
    tech cluster and whose median is published. Hourly medians/quartiles annualize
    at ×2080; rows already annual (``Annual_Wage_Flag=1``) pass through."""
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    rows: list[dict] = []
    retrieved = time.strftime("%Y-%m-%d")
    for r in csv.DictReader(text.splitlines()):
        if (r.get("prov") or "").strip().upper() != "NAT":
            continue
        raw_noc = (r.get("NOC_CNP") or "").strip()
        noc = raw_noc.replace("NOC_", "").replace("CNP_", "")
        if noc not in TECH_NOC:
            continue
        median = _num(r.get("Median_Wage_Salaire_Median"))
        if median is None:
            continue                                   # suppressed — no honest row to emit
        title = (r.get("NOC_Title_eng") or "").strip()
        annual_flag = (r.get("Annual_Wage_Flag_Salaire_annuel") or "").strip() == "1"
        mult = 1.0 if annual_flag else ANNUAL_HOURS

        def _ann(col: str) -> float | None:
            v = _num(r.get(col))
            return round(v * mult) if v is not None else None

        rows.append({
            "source": "canada_jobbank",
            "source_id": "canada-jobbank",
            "kind": "official",
            "role_id": _resolve_role(title),
            "role_surface": title,
            "country": "CA",
            "currency": "CAD",
            "basis": "base-annual",
            "yoe": None,                               # dataset has no experience axis
            "level_label": None,
            "median": round(median * mult),
            "p25": _ann("Quartile1_Wage_Salaire_Quartile1"),
            "p75": _ann("Quartile3_Wage_Salaire_Quartile3"),
            "p10": _ann("Low_Wage_Salaire_Minium"),    # Job Bank "low" ≈ 10th pct floor
            "p90": _ann("High_Wage_Salaire_Maximal"),  # Job Bank "high" ≈ 90th pct ceiling
            "n": None,                                 # LFS/Census estimate — no sample n published
            "noc_code": noc,
            "wage_source": (r.get("Data_Source_E") or "").strip(),
            "reference_period": (r.get("Reference_Period") or "").strip(),
            "annualized": not annual_flag,
            "vintage": year,
            "retrieved": retrieved,
        })
    return rows


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Download the newest available Job Bank wage CSV (cache = checkpoint), then
    aggregate. ``max_units`` caps how many yearly CSVs we try (default: stop after
    the first that succeeds)."""
    t0 = time.time()
    fetched = cached = misses = 0
    consec_fail = 0
    urls = CSV_URLS[:max_units] if max_units else CSV_URLS
    got_one = False
    for year, url in urls:
        if got_one:                                    # newest good vintage is enough
            break
        if time_cap_s is not None and (time.time() - t0) > time_cap_s:
            log.warning("canada_jobbank: time cap %.0fs hit", time_cap_s)
            break
        path = _csv_path(year)
        if path.exists() and not force:
            cached += 1
            got_one = True
            log.info("canada_jobbank: %d CSV cached", year)
            continue
        try:
            data = _fetch_csv(url)
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 — transport failure / rotated URL
            consec_fail += 1
            misses += 1
            log.warning("canada_jobbank: fetch failed %d %s [consec %d]",
                        year, str(e)[:100], consec_fail)
            if consec_fail >= MAX_CONSEC_FAIL:
                log.error("canada_jobbank: %d consecutive failures — stopping "
                          "(check CSV_URLS against the dataset page)", consec_fail)
                break
            time.sleep(THROTTLE * 2)
            continue
        if not data or not data.lstrip().upper().startswith(b"\xef\xbb\xbfNOC") \
                and b"NOC_CNP" not in data[:200]:
            misses += 1
            log.warning("canada_jobbank: %d payload not the wage CSV (len=%d) — skipping",
                        year, len(data))
            time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
            continue
        path.write_bytes(data)
        fetched += 1
        got_one = True
        log.info("canada_jobbank: %d CSV downloaded (%.1f MB)", year, len(data) / 1e6)
        time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("canada_jobbank summary: %s", s)
    return s


def _aggregate() -> list[dict]:
    """Parse the newest cached CSV → estimates.json (the single file the fuse reads).

    Uses the highest-year cached CSV so re-runs converge on the freshest vintage."""
    sd = _staging_dir()
    cached = sorted((sd / "csv").glob("wage*.csv"), reverse=True)
    rows: list[dict] = []
    for path in cached:
        m = re.search(r"wage(\d{4})", path.name)
        year = int(m.group(1)) if m else 0
        try:
            rows = _parse_page(path.read_bytes(), year)
        except Exception as e:  # noqa: BLE001 — a malformed cached file must not break the fuse
            log.error("canada_jobbank: parse failed for %s: %s", path.name, e)
            continue
        if rows:
            break                                      # newest good vintage wins
    (sd / "estimates.json").write_text(json.dumps(rows), encoding="utf-8")
    return rows


def load_estimates() -> list[dict]:
    p = _staging_dir() / "estimates.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return _aggregate()


def run(**kw) -> dict:
    """Connector entrypoint (registry + collect_all)."""
    return fetch_all(**kw)


if __name__ == "__main__":  # pragma: no cover — manual smoke
    print(json.dumps(run(), indent=2))
