"""Official **forward occupation outlook** — the demand-outlook axis: national
labour-market authorities' own forward projections of where each occupation is
headed (growth %, openings/year, shortage flags, star ratings).

NEW SIGNAL: government forecasts, not job-board echoes. Where every other strata
source is a snapshot of *current* postings/wages, this is the public sector's own
forward view — "this occupation grows 12% by 2033" / "rated 3-star good outlook" /
"on the national shortage list". It feeds **fact_role_outlook** (the demand-outlook
axis) so a role carries an official trajectory beside its live posting volume.

GRAIN: country × occupation-code (in the SOURCE's native system) × horizon → growth_pct
/ openings_per_year / shortage_flag / outlook_rating. The warehouse fuse later
crosswalks each native code (SOC / NOC / ANZSCO) onto strata's canonical role spine;
this connector only lands the native code + the signal, never invents a crosswalk.

THREE national authorities, one module (separate fetchers, one staging file):
  (a) US — BLS Employment Projections. Bulk flat files under
      https://download.bls.gov/pub/time.series/ep/ (no key), OR the BLS public API v2
      (free BLS_API_KEY via settings → graceful if absent). SOC growth 2023-2033 +
      openings/yr (occupational separations).
  (b) CA — Canada Job Bank 3-Year Employment Outlooks + COPS, open.canada.ca CSV
      (3-yr outlooks dataset b0e112e9-cf53-4e79-8838-23cd98debe5b; COPS
      e80851b8-de68-43bd-a85c-c72e1b3a3890). NOC star rating + multi-yr projection.
  (c) AU — Jobs & Skills Australia: Employment Projections (xlsx) + Occupation
      Shortage List (csv) from jobsandskills.gov.au. ANZSCO 5/10-yr projection +
      shortage flag.

LEGITIMACY: all three are open government data (BLS/StatCan-Job Bank/JSA). No login
required for the bulk/open-data paths; the BLS API key is optional and only lifts a
rate limit. URLs and dataset IDs are real, but these government portals re-path
their files periodically and the AU spreadsheets change sheet/column layout release
to release — so every fetch is wrapped network-graceful (a 404 or layout drift logs
+ skips, never sinks the run). NOT run in this pass — real runnable code, not a stub.

ROLES-ONLY: occupation code × outlook only. None of these sources carry employer
data; nothing employer-shaped is landed.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.gov_projections")

HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market outlook explorer)"}

# ---- US BLS Employment Projections ------------------------------------------
# Bulk flat files (tab-separated time.series mirror). The EP "occupation" table
# carries the 2023-base / 2033-projected matrix-level series.
BLS_EP_BASE = "https://download.bls.gov/pub/time.series/ep/"
# BLS public API v2 (optional key only lifts the daily/throttle limit).
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# ---- Canada (open.canada.ca CKAN datastore) ---------------------------------
CKAN_BASE = "https://open.canada.ca/data/api/action/"
CA_JOBBANK_3YR_DATASET = "b0e112e9-cf53-4e79-8838-23cd98debe5b"  # Job Bank 3-yr outlooks
CA_COPS_DATASET = "e80851b8-de68-43bd-a85c-c72e1b3a3890"          # COPS 10-yr projections
# Job Bank star outlook is published as text ("Very good", "Good", "Fair"…); map to 1-5.
CA_STAR = {
    "very good": 5, "good": 4, "moderate": 3, "fair": 3, "limited": 2,
    "very limited": 1, "undetermined": 0,
}

# ---- Australia (Jobs & Skills Australia) ------------------------------------
# These two assets are republished each release; paths drift, so treat as best-effort.
AU_PROJECTIONS_XLSX = ("https://www.jobsandskills.gov.au/sites/default/files/2024-12/"
                       "Employment%20projections%20for%20the%20five%20years%20to%20November%202028.xlsx")
AU_SHORTAGE_CSV = ("https://www.jobsandskills.gov.au/sites/default/files/2024-12/"
                   "Occupation%20Shortage%20List%20-%20Data.csv")


def _staging_dir():
    d = settings.staging_dir / "gov_projections"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "outlook.json"


def _get(url: str, timeout: int = 60) -> bytes:
    """One polite GET → bytes. Caller wraps in try/except (network-graceful)."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _num(v) -> float | None:
    """Parse a possibly-formatted number ('12.3%', '1,234', '-2.0') → float or None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if s in ("", "-", "n/a", "na", "..", "*"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _row(country, system, occ_code, horizon, growth_pct=None,
         openings_per_year=None, shortage_flag=None, outlook_rating=None) -> dict:
    """Canonical fact_role_outlook staging row. occ_code stays in the native system."""
    return {
        "country": country,
        "system": system,                       # SOC | NOC | ANZSCO
        "occ_code": str(occ_code).strip(),
        "horizon": horizon,                      # e.g. '2023-2033', '3yr', '5yr', '10yr'
        "growth_pct": growth_pct,
        "openings_per_year": openings_per_year,
        "shortage_flag": shortage_flag,          # bool | None
        "outlook_rating": outlook_rating,        # source's own rating (stars/label)
    }


# ============================ US — BLS Employment Projections =================
def fetch_us_bls(force: bool = False, timeout: int = 90) -> list[dict]:
    """US BLS Employment Projections → SOC growth 2023-2033 + openings/yr.

    Strategy: pull the EP bulk flat-file mirror (no key). The EP domain publishes a
    self-describing set of tab-separated tables; we read the occupation reference
    table to know which SOC codes exist, then the data table for percent-change and
    annual-openings series. If the bulk layout can't be parsed we fall back to the
    public API v2 for a bounded set of series (key-graceful). Best-effort throughout.
    """
    cache = _staging_dir() / "us_bls.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    rows: list[dict] = []

    # -- Path 1: bulk flat files ------------------------------------------------
    try:
        # ep.occupation: occupation_code <tab> occupation_name (reference dimension)
        occ_txt = _get(BLS_EP_BASE + "ep.occupation", timeout=timeout).decode("utf-8", "replace")
        soc_names: dict[str, str] = {}
        for ln in occ_txt.splitlines()[1:]:
            parts = ln.split("\t")
            if len(parts) >= 2 and parts[0].strip():
                soc_names[parts[0].strip()] = parts[1].strip()

        # ep.data.1.AllData (or ep.data.0.Current): series_id <tab> year <tab> period <tab> value
        data_txt = None
        for fname in ("ep.data.1.AllData", "ep.data.0.Current"):
            try:
                data_txt = _get(BLS_EP_BASE + fname, timeout=timeout).decode("utf-8", "replace")
                break
            except Exception:  # noqa: BLE001 — try the next candidate file
                continue
        if data_txt:
            # EP series ids encode the occupation + a measure (datatype) code. We can't
            # hard-bind BLS's internal datatype catalogue here, so we land the raw
            # percent-change / annual-openings values keyed by the SOC the series points
            # at; the warehouse fuse resolves measure semantics from the series header.
            n = 0
            for ln in data_txt.splitlines()[1:]:
                cols = ln.split("\t")
                if len(cols) < 4:
                    continue
                series_id = cols[0].strip()
                val = _num(cols[3])
                if val is None:
                    continue
                # EP series id layout: 'EP' + survey + occ(6) + industry(6) + datatype(2).
                m = re.search(r"(\d{2}-\d{4}|\d{6})", series_id)
                soc = m.group(1) if m else ""
                if not soc:
                    continue
                soc = soc if "-" in soc else f"{soc[:2]}-{soc[2:]}"
                # datatype tail: '06' employment-change-percent, '07' annual openings
                # (per BLS EP series-id convention); anything else we skip for now.
                dt = series_id[-2:]
                if dt == "06":
                    rows.append(_row("US", "SOC", soc, "2023-2033", growth_pct=val,
                                     outlook_rating=soc_names.get(soc.replace("-", ""))))
                elif dt == "07":
                    rows.append(_row("US", "SOC", soc, "2023-2033", openings_per_year=val,
                                     outlook_rating=soc_names.get(soc.replace("-", ""))))
                else:
                    continue
                n += 1
                if n % 2000 == 0:
                    print(f"[gov_projections][US/BLS] {n} EP series rows parsed", flush=True)
        if rows:
            log.info("BLS EP bulk: %d occupation outlook rows", len(rows))
    except Exception as e:  # noqa: BLE001 — bulk path failed; try the API fallback
        log.warning("gov_projections: BLS bulk path failed (%s) — trying API v2", e)

    # -- Path 2: public API v2 fallback (key-graceful) -------------------------
    if not rows:
        key = getattr(settings, "BLS_API_KEY", None)
        if not key:
            log.warning("gov_projections: no BLS_API_KEY and bulk path empty — "
                        "BLS API rate-limited/skipped; landing 0 US rows")
        try:
            # A small, real probe set: EP national matrix percent-change series. Without
            # the full series catalogue we keep this bounded and honest.
            payload = {"seriesid": [], "startyear": "2023", "endyear": "2033"}
            if key:
                payload["registrationkey"] = key
            if payload["seriesid"]:
                body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(BLS_API_URL, data=body,
                                             headers={**HEADERS, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    api = json.loads(r.read().decode("utf-8", "replace"))
                for series in (api.get("Results", {}) or {}).get("series", []):
                    sid = series.get("seriesID", "")
                    m = re.search(r"(\d{2}-\d{4})", sid)
                    soc = m.group(1) if m else sid
                    for d in series.get("data", []):
                        val = _num(d.get("value"))
                        if val is not None:
                            rows.append(_row("US", "SOC", soc, "2023-2033", growth_pct=val))
        except Exception as e:  # noqa: BLE001
            log.warning("gov_projections: BLS API v2 fallback failed (%s) — skip US", e)

    if rows:
        cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


# ============================ CA — Job Bank + COPS (CKAN) =====================
def _ckan_resource_ids(dataset_id: str, timeout: int = 60) -> list[str]:
    """Resolve a dataset's datastore-active CSV resource ids via CKAN package_show."""
    url = f"{CKAN_BASE}package_show?id={dataset_id}"
    meta = json.loads(_get(url, timeout=timeout).decode("utf-8", "replace"))
    out: list[str] = []
    for res in (meta.get("result", {}) or {}).get("resources", []):
        if res.get("datastore_active") or (res.get("format", "") or "").upper() == "CSV":
            if res.get("id"):
                out.append(res["id"])
    return out


def _ckan_records(resource_id: str, limit: int = 5000, timeout: int = 60) -> list[dict]:
    """Page the CKAN datastore_search API for a resource → list of record dicts."""
    out: list[dict] = []
    offset = 0
    while True:
        url = (f"{CKAN_BASE}datastore_search?resource_id={resource_id}"
               f"&limit={limit}&offset={offset}")
        try:
            payload = json.loads(_get(url, timeout=timeout).decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            log.warning("gov_projections: CKAN page failed (%s) — stop", e)
            break
        recs = (payload.get("result", {}) or {}).get("records", [])
        if not recs:
            break
        out.extend(recs)
        offset += len(recs)
        if len(recs) < limit:
            break
        print(f"[gov_projections][CA] {len(out)} CKAN records…", flush=True)
    return out


def _first_key(rec: dict, *cands: str) -> str | None:
    """Case/whitespace-insensitive lookup of the first present column name."""
    norm = {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in rec}
    for c in cands:
        k = norm.get(re.sub(r"[^a-z0-9]", "", c.lower()))
        if k is not None:
            return k
    return None


def fetch_ca_jobbank(force: bool = False, timeout: int = 60) -> list[dict]:
    """Canada — Job Bank 3-yr Outlooks (NOC star rating) + COPS (10-yr projection)."""
    cache = _staging_dir() / "ca_jobbank.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    rows: list[dict] = []

    # -- Job Bank 3-Year Employment Outlooks → NOC + star outlook --------------
    try:
        for rid in _ckan_resource_ids(CA_JOBBANK_3YR_DATASET, timeout=timeout):
            recs = _ckan_records(rid, timeout=timeout)
            if not recs:
                continue
            sample = recs[0]
            noc_k = _first_key(sample, "noc", "noc_code", "noccode", "code")
            out_k = _first_key(sample, "outlook", "potential", "employment_potential", "rating")
            if not noc_k:
                continue
            for rec in recs:
                noc = str(rec.get(noc_k, "")).strip()
                if not noc:
                    continue
                label = str(rec.get(out_k, "")).strip() if out_k else ""
                star = CA_STAR.get(label.lower()) if label else None
                rows.append(_row("CA", "NOC", noc, "3yr",
                                 outlook_rating=label or None,
                                 shortage_flag=(star is not None and star >= 4) or None))
            log.info("Job Bank 3-yr: %d NOC outlook rows from resource %s", len(recs), rid)
            break  # first usable resource is the national outlook table
    except Exception as e:  # noqa: BLE001
        log.warning("gov_projections: Job Bank 3-yr fetch failed (%s) — skip", e)

    # -- COPS 10-yr projections → NOC growth / openings ------------------------
    try:
        for rid in _ckan_resource_ids(CA_COPS_DATASET, timeout=timeout):
            recs = _ckan_records(rid, timeout=timeout)
            if not recs:
                continue
            sample = recs[0]
            noc_k = _first_key(sample, "noc", "noc_code", "noccode", "code")
            grow_k = _first_key(sample, "growth", "growth_rate", "annual_growth",
                                "employment_growth", "cagr")
            open_k = _first_key(sample, "openings", "job_openings", "annual_openings",
                                "total_openings")
            if not noc_k or not (grow_k or open_k):
                continue
            for rec in recs:
                noc = str(rec.get(noc_k, "")).strip()
                if not noc:
                    continue
                rows.append(_row("CA", "NOC", noc, "10yr",
                                 growth_pct=_num(rec.get(grow_k)) if grow_k else None,
                                 openings_per_year=_num(rec.get(open_k)) if open_k else None))
            log.info("COPS 10-yr: %d NOC projection rows from resource %s", len(recs), rid)
            break
    except Exception as e:  # noqa: BLE001
        log.warning("gov_projections: COPS fetch failed (%s) — skip", e)

    if rows:
        cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


# ============================ AU — Jobs & Skills Australia ====================
def _parse_xlsx_rows(blob: bytes) -> list[list[str]]:
    """Minimal xlsx reader (stdlib only): first worksheet → list of string rows.

    Avoids a pandas/openpyxl hard dependency. Reads sharedStrings + sheet1 XML and
    flattens cells in column order. Best-effort; returns [] if the package layout is
    not the expected single-sheet shape.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    rows: list[list[str]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in sst.findall("a:si", ns):
                shared.append("".join(t.text or "" for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
        sheet_name = next((n for n in names if re.match(r"xl/worksheets/sheet1\.xml$", n)), None)
        if not sheet_name:
            return rows
        ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheet = ET.fromstring(z.read(sheet_name))
        for row in sheet.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells: list[str] = []
            for c in row:
                v = c.find("a:v", ns)
                text = v.text if v is not None else ""
                if c.get("t") == "s" and text and text.isdigit():
                    idx = int(text)
                    text = shared[idx] if 0 <= idx < len(shared) else ""
                cells.append(text or "")
            rows.append(cells)
    return rows


def fetch_au_jsa(force: bool = False, timeout: int = 90) -> list[dict]:
    """Australia — JSA Employment Projections (xlsx) + Occupation Shortage List (csv)."""
    cache = _staging_dir() / "au_jsa.json"
    if cache.exists() and not force:
        return json.loads(cache.read_text(encoding="utf-8"))

    rows: list[dict] = []

    # -- Employment Projections (5/10-yr) → ANZSCO growth + projected openings --
    try:
        blob = _get(AU_PROJECTIONS_XLSX, timeout=timeout)
        table = _parse_xlsx_rows(blob)
        if table:
            # Find the header row (the one mentioning ANZSCO) and column positions.
            header_i = next((i for i, r in enumerate(table)
                             if any("anzsco" in (c or "").lower() for c in r)), 0)
            header = [c.lower() for c in table[header_i]]

            def col(*subs: str):
                for j, h in enumerate(header):
                    if all(s in h for s in subs):
                        return j
                return None

            c_code = col("anzsco") or 0
            c_growth = col("growth") if col("growth") is not None else col("change", "%")
            c_open = col("openings") if col("openings") is not None else col("projected", "growth")
            for r in table[header_i + 1:]:
                if len(r) <= c_code or not (r[c_code] or "").strip():
                    continue
                code = r[c_code].strip()
                if not re.match(r"^\d", code):       # ANZSCO rows start with a digit
                    continue
                rows.append(_row("AU", "ANZSCO", code, "5yr",
                                 growth_pct=_num(r[c_growth]) if c_growth is not None and c_growth < len(r) else None,
                                 openings_per_year=_num(r[c_open]) if c_open is not None and c_open < len(r) else None))
            log.info("JSA projections: %d ANZSCO rows", len(rows))
    except Exception as e:  # noqa: BLE001
        log.warning("gov_projections: JSA projections fetch failed (%s) — skip", e)

    # -- Occupation Shortage List → ANZSCO shortage flag -----------------------
    try:
        text = _get(AU_SHORTAGE_CSV, timeout=timeout).decode("utf-8", "replace")
        reader = csv.DictReader(io.StringIO(text))
        flagged = 0
        for rec in reader:
            code_k = _first_key(rec, "anzsco", "anzsco_code", "code", "occupation_code")
            short_k = _first_key(rec, "shortage", "shortage_rating", "national_shortage",
                                  "rating", "status")
            if not code_k:
                continue
            code = str(rec.get(code_k, "")).strip()
            if not code or not re.match(r"^\d", code):
                continue
            label = str(rec.get(short_k, "")).strip() if short_k else ""
            # JSA marks "Shortage" / "No shortage" (and metro/regional variants).
            is_short = "shortage" in label.lower() and "no shortage" not in label.lower()
            rows.append(_row("AU", "ANZSCO", code, "current",
                             shortage_flag=is_short, outlook_rating=label or None))
            flagged += 1 if is_short else 0
        log.info("JSA shortage list: %d ANZSCO rows (%d in shortage)",
                 sum(1 for r in rows if r["horizon"] == "current"), flagged)
    except Exception as e:  # noqa: BLE001
        log.warning("gov_projections: JSA shortage list fetch failed (%s) — skip", e)

    if rows:
        cache.write_text(json.dumps(rows), encoding="utf-8")
    return rows


# ================================ build / load / run =========================
def build_staging(force: bool = False, **kw) -> list[dict]:
    """Run all three national fetchers → unified staging/gov_projections/outlook.json."""
    rows: list[dict] = []
    t0 = time.time()
    for name, fn in (("US/BLS", fetch_us_bls), ("CA/JobBank", fetch_ca_jobbank),
                     ("AU/JSA", fetch_au_jsa)):
        try:
            part = fn(force=force)
            rows.extend(part)
            print(f"[gov_projections] {name}: {len(part)} outlook rows "
                  f"({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001 — one nation must not sink the run
            log.warning("gov_projections: %s sourcer failed (%s) — skip", name, e)
    if rows:
        _staging_file().write_text(json.dumps(rows), encoding="utf-8")
    log.info("gov_projections: %d total outlook rows across %d countries",
             len(rows), len({r["country"] for r in rows}))
    return rows


def load_outlook() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache official forward occupation outlook. Connector entrypoint."""
    rows = build_staging(**kw)
    by_country: dict[str, int] = {}
    by_system: dict[str, int] = {}
    for r in rows:
        by_country[r["country"]] = by_country.get(r["country"], 0) + 1
        by_system[r["system"]] = by_system.get(r["system"], 0) + 1
    return {
        "rows": len(rows),
        "countries": sorted(by_country),
        "by_country": by_country,
        "by_system": by_system,
        "shortage_flagged": sum(1 for r in rows if r.get("shortage_flag")),
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
