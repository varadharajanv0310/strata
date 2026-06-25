"""Cedefop **Skills-OVATE** — EU skill-demand *from online job vacancies*, pre-aggregated
to ESCO occupation × skill × country × period (council source for the EU labour-market
signal, DE focus).

The NEW signal strata gets here is **demand inferred from live vacancies** rather than
from surveys or wage tables: Cedefop's Skills Online Vacancy Analysis Tool for Europe
(Skills-OVATE) machine-reads millions of online job adverts across EU member states and
publishes, as open data, how often each ESCO skill is requested for each ESCO occupation
in each country and quarter. That is exactly the "which skills are hot, where" axis our
demand fact needs — and crucially it is already aggregated by Cedefop, so there is NO
employer/advert-level data to land (ROLES-ONLY is satisfied by construction).

Grain: country (ISO-2, EU members; DE focus) × ESCO occupation code × ESCO skill ×
period (quarter or year) × a demand value (share of vacancies requesting the skill, or a
vacancy count). Country='' is never used here — every Skills-OVATE row is geo-bound to a
member state, so this is real geography (unlike a global skill taxonomy).

Obtained from the Cedefop data portal / Skills-OVATE open-data downloads
(https://www.cedefop.europa.eu/en/tools/skills-online-vacancies). LEGITIMACY: open
public data published by Cedefop (an EU agency); no key, no scraping of adverts — we pull
the already-aggregated tables. FRAGILITY: Cedefop has reshaped these downloads more than
once (CSV vs JSON, column renames, occasional portal moves), so every remote call and
every parse is wrapped to degrade gracefully — a shape change logs a warning and lands
what it can rather than crashing. Lands → ``staging/cedefop/skill_demand.json``:
``[{country, esco_occ, skill, period, share_or_count}]``.

Feeds the warehouse **fact_skill_adoption / fact_demand** (EU skill-demand) lens beside
the other demand sources; the warehouse fuse maps ESCO occupation codes onto our role
spine. Credential-graceful and network-graceful; **not run in this pass** — coded for the
later run.
"""
from __future__ import annotations

import csv
import io
import json
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.cedefop_ovate")

# Skills-OVATE covers EU/EEA member states. Our 7-country list only overlaps in DE, so DE
# is the focus; we still keep the full EU map so multi-country downloads land correctly
# and the warehouse can use the wider EU context. ISO-2 already (Cedefop uses ISO-2).
EU_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}
# Of our 7 strata countries, only DE is an EU member — Skills-OVATE has no IN/US/GB/CA/AU/SG.
FOCUS = "DE"

# Cedefop publishes the OVATE aggregates from its data portal. The portal has moved /
# reshaped over time, so we try a small set of candidate open-data endpoints in order and
# use the first that responds. These are best-effort; a 404/format change just falls
# through to the next candidate and finally to a graceful empty land.
PORTAL = "https://www.cedefop.europa.eu"
CANDIDATE_URLS = [
    # Configured override wins if the operator pins an exact CSV/JSON export URL.
    None,  # placeholder for settings.CEDEFOP_OVATE_URL (filled at runtime)
    # Known open-data style export paths (occupation × skill × country × period aggregate).
    f"{PORTAL}/en/tools/skills-online-vacancies/data-download?type=skill_occupation&format=csv",
    f"{PORTAL}/en/tools/skills-online-vacancies/api/skill-demand?format=csv",
    f"{PORTAL}/sites/default/files/ovate/skill_occupation_country.csv",
]
HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}

# Tolerant header aliases — Cedefop has renamed these between releases. We map whatever
# we find onto our canonical keys; an unknown shape just yields fewer populated fields.
_COUNTRY_KEYS = ("country", "country_code", "geo", "nation", "member_state", "iso2")
_OCC_KEYS = ("esco_occ", "occupation", "occupation_code", "esco_occupation",
             "isco", "isco_code", "occ_code", "occupationUri", "occupation_uri")
_SKILL_KEYS = ("skill", "skill_label", "esco_skill", "skill_name", "skillUri", "skill_uri")
_PERIOD_KEYS = ("period", "quarter", "time", "year", "date", "reference_period")
_VALUE_KEYS = ("share", "share_or_count", "value", "count", "vacancy_count",
               "demand", "n_vacancies", "frequency", "pct", "percentage")


def _staging_dir():
    d = settings.staging_dir / "cedefop"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "skill_demand.json"


def _first(row: dict, keys: tuple[str, ...]) -> str:
    """First non-empty value among candidate column names (case-insensitive)."""
    lower = {k.lower().strip(): v for k, v in row.items() if k}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _norm_occ(raw: str) -> str:
    """Keep the source's native ESCO/ISCO occupation token; strip a URI wrapper if present.

    Cedefop exposes occupations either as ESCO URIs
    (http://data.europa.eu/esco/occupation/<uuid>) or as ISCO codes. We land the bare
    token verbatim — the warehouse fuse owns the crosswalk to our role spine.
    """
    c = (raw or "").strip()
    if "/" in c:
        c = c.rstrip("/").rsplit("/", 1)[-1]
    return c


def _norm_skill(raw: str) -> str:
    """Keep the native ESCO skill token/label; strip a URI wrapper if present."""
    s = (raw or "").strip()
    if "/" in s and "esco" in s.lower():
        s = s.rstrip("/").rsplit("/", 1)[-1]
    return s


def _to_value(raw: str):
    """Parse the demand value to float; tolerate '%', thousands separators, blanks."""
    s = (raw or "").strip().replace("%", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_csv(text: str) -> list[dict]:
    """Parse a Cedefop OVATE CSV export into canonical rows. Shape-tolerant."""
    rows: list[dict] = []
    # Sniff delimiter; Cedefop has shipped both ',' and ';' separated files.
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    for row in reader:
        country = _first(row, _COUNTRY_KEYS).upper()
        # Some exports spell country as a full name or ISO-3; keep only recognizable EU ISO-2.
        if len(country) != 2 or country not in EU_COUNTRIES:
            # Tolerate an ISO-3 'DEU' style by trimming, else skip non-geo rows.
            if country[:2] in EU_COUNTRIES:
                country = country[:2]
            else:
                continue
        occ = _norm_occ(_first(row, _OCC_KEYS))
        skill = _norm_skill(_first(row, _SKILL_KEYS))
        if not occ and not skill:
            continue  # neither join key present → not a usable demand row
        val = _to_value(_first(row, _VALUE_KEYS))
        rows.append({
            "country": country,                       # EU ISO-2 (real geography)
            "esco_occ": occ,                          # native ESCO/ISCO occupation token
            "skill": skill,                           # native ESCO skill token/label
            "period": _first(row, _PERIOD_KEYS),      # quarter or year as published
            "share_or_count": val,                    # demand: share (%) or vacancy count
        })
    return rows


def _parse_json(text: str) -> list[dict]:
    """Parse a JSON OVATE export. Accepts a top-level list or a {'data': [...]} envelope."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return []
    records = obj.get("data") if isinstance(obj, dict) else obj
    if not isinstance(records, list):
        return []
    rows: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        country = _first(rec, _COUNTRY_KEYS).upper()
        if country[:2] in EU_COUNTRIES:
            country = country[:2]
        else:
            continue
        occ = _norm_occ(_first(rec, _OCC_KEYS))
        skill = _norm_skill(_first(rec, _SKILL_KEYS))
        if not occ and not skill:
            continue
        rows.append({
            "country": country,
            "esco_occ": occ,
            "skill": skill,
            "period": _first(rec, _PERIOD_KEYS),
            "share_or_count": _to_value(_first(rec, _VALUE_KEYS)),
        })
    return rows


def _fetch_url(url: str, timeout: int = 90) -> list[dict]:
    """Download one candidate URL and parse by content/shape. Best-effort, may return []."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = (r.headers.get("Content-Type") or "").lower()
        raw = r.read()
    text = raw.decode("utf-8", errors="replace")
    looks_json = "json" in ctype or text.lstrip()[:1] in ("[", "{")
    rows = _parse_json(text) if looks_json else _parse_csv(text)
    if not rows and not looks_json:
        # Some servers mislabel JSON as text/plain or octet-stream — try the other parser.
        rows = _parse_json(text)
    return rows


def fetch_skill_demand(force: bool = False, time_cap_s: float = 600.0) -> list[dict]:
    """Fetch + cache Cedefop OVATE skill-demand. Cache file IS the checkpoint.

    Tries the configured override first, then each known candidate URL until one yields
    rows. Network/format failure on any candidate logs + skips to the next; if none work
    we land/keep an empty file and the run survives.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_skill_demand()

    override = getattr(settings, "CEDEFOP_OVATE_URL", None)
    candidates = ([override] if override else []) + [u for u in CANDIDATE_URLS if u]
    if not candidates:
        log.warning("cedefop_ovate: no download URL available — landing empty")
        return []

    out: list[dict] = []
    t0 = time.time()
    for i, url in enumerate(candidates):
        if time.time() - t0 > time_cap_s:
            log.warning("cedefop_ovate: time cap %ss hit — landing partial", time_cap_s)
            break
        try:
            print(f"[cedefop_ovate] trying candidate {i + 1}/{len(candidates)}: {url}",
                  flush=True)
            rows = _fetch_url(url)
            if rows:
                out = rows
                print(f"[cedefop_ovate] got {len(rows)} skill-demand rows from {url}",
                      flush=True)
                break
            log.info("cedefop_ovate: candidate %s returned no usable rows — next", url)
        except Exception as e:  # noqa: BLE001 — one bad endpoint must not sink the run
            log.warning("cedefop_ovate: candidate %s failed (%s) — next", url, e)

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("Cedefop OVATE skill-demand: %d rows across %d countries (focus=%s)",
             len(out), len({r["country"] for r in out}), FOCUS)
    return out


def load_skill_demand() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache Cedefop Skills-OVATE EU skill-demand. Connector entrypoint."""
    rows = fetch_skill_demand(**kw)
    countries = sorted({r["country"] for r in rows})
    return {
        "rows": len(rows),
        "countries": countries,
        "focus": FOCUS,
        "focus_rows": sum(1 for r in rows if r["country"] == FOCUS),
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
