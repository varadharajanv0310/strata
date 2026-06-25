"""Bundesagentur für Arbeit (Germany's federal employment agency) — TWO official DE
labour-market signals in one module, both keyed on the German occupation taxonomy
**KldB-2010**:

  (a) **Entgeltatlas** — median gross MONTHLY wage by occupation (+ region / age / sex).
      This is the authoritative German wage register; it is the only one of our 7
      countries whose statutory wage source is published at occupation grain rather
      than survey microdata, and it gives strata a real DE baseline for the official
      salary lens. NEW SIGNAL = median gross monthly wage by KldB. Feeds
      ``fact_salary_official``.
  (b) **Jobsuche** — live vacancy COUNT per occupation keyword from the agency's own
      job board (the largest single vacancy pool in Germany). NEW SIGNAL = current DE
      vacancy volume per occupation. Feeds ``fact_demand``.

Grain: country='DE' × occupation (KldB code for wages / keyword for vacancies) × period.
Geography is real and DE-only here (NOT a global skill signal), so country is always
'DE'.

How obtained + legitimacy:
  - Entgeltatlas exposes an **undocumented JSON API** behind its web app
    (web.arbeitsagentur.de/entgeltatlas/). There is no published contract, so this is
    best-effort and **version-pinned** (``ENTGELTATLAS_API_VERSION``); a schema change
    upstream will make a fetch return nothing rather than crash. We hit it only at a
    polite rate. If it changes shape we log + skip — never fabricate.
  - Jobsuche is a **public REST API** but the bearer key in wide public use
    (``X-API-Key: jobboerse-jobsuche``) is **unofficial** — it ships in the agency's
    own SPA and is reused across the ecosystem; we pin + flag it and allow override via
    ``settings.bundesagentur_jobsuche_key``. We request ``.maxErgebnisse`` only (the
    total-hits counter), never employer detail.

ROLES-ONLY: we land occupation × region × period × signal value. The Jobsuche payload
carries ``arbeitgeber`` (employer) on each hit — we **drop it entirely** and read only
the aggregate count. No company/employer is ever landed as a product field.

Credential-graceful, polite (heartbeat + time/unit caps + timeouts), network-graceful
(per-unit try/except). Mirrors the ilostat / worldbank_ppp connector shape
(fetch+cache → build → load → run). **Not run in this pass** — real runnable code.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.bundesagentur")

# ── version pins (this is undocumented / unofficial — pin so a drift is visible) ──
ENTGELTATLAS_API_VERSION = "v2"   # web.arbeitsagentur.de/entgeltatlas/ internal JSON API
JOBSUCHE_API_VERSION = "v4"       # rest.arbeitsagentur.de/.../jobsuche-service/pc/v4
JOBSUCHE_PUBLIC_KEY = "jobboerse-jobsuche"  # UNOFFICIAL public key shipped in the SPA

ENTGELTATLAS_BASE = "https://web.arbeitsagentur.de/entgeltatlas/backend/v2/entgelt"
JOBSUCHE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"

UA = "strata/1.0 (+research; roles-only job-market explorer)"

# A small seed of KldB-2010 occupation codes (5-digit Berufsgattung) spanning tech roles
# we care about. The warehouse fuse crosswalks KldB→our role taxonomy later; here we just
# land the source-native code + the wage. Keep this list short + overridable.
DEFAULT_KLDB = [
    "43412",  # Softwareentwicklung
    "43414",  # Informatik (technische)
    "43422",  # Web-/Multimediaprogrammierung
    "43432",  # IT-Systemanalyse / -beratung
    "43442",  # Datenbankadministration
    "43412",  # (dedup happens below)
]

# Occupation keywords for the Jobsuche vacancy counter (German labour-market terms).
DEFAULT_VACANCY_KEYWORDS = [
    "Softwareentwickler",
    "Data Scientist",
    "DevOps Engineer",
    "Data Engineer",
    "Frontend Entwickler",
    "Backend Entwickler",
    "Machine Learning Engineer",
    "Cloud Architekt",
    "Cyber Security",
    "Datenbankadministrator",
]


def _staging_dir():
    d = settings.staging_dir / "bundesagentur"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _wages_file():
    return _staging_dir() / "wages.json"


def _vacancies_file():
    return _staging_dir() / "vacancies.json"


def _jobsuche_key() -> str:
    """Bearer key for Jobsuche — settings override else the pinned UNOFFICIAL public key."""
    return getattr(settings, "bundesagentur_jobsuche_key", None) or JOBSUCHE_PUBLIC_KEY


# ─────────────────────────────── (a) Entgeltatlas wages ──────────────────────────────

def _fetch_kldb_wage(kldb: str, timeout: int = 45) -> list[dict]:
    """Hit the undocumented Entgeltatlas JSON API for one KldB code → wage rows.

    Best-effort + version-pinned: the endpoint is internal to the web app and may change
    without notice. We parse defensively (every field via .get) and land
    country='DE' × kldb × region × sex × median_gross_monthly × year. Returns [] on any
    drift/failure rather than raising.
    """
    # The web app calls .../entgelt/{kldb}?... returning a JSON envelope of "entgelte"
    # broken down by region (Bundesland) / age / sex. We only need the median monthly
    # gross ("entgelt" in EUR). Shape is undocumented — read it leniently.
    url = f"{ENTGELTATLAS_BASE}/{urllib.parse.quote(kldb)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "X-API-Version": ENTGELTATLAS_API_VERSION,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))

    # Envelope may be a list of breakdown dicts or {"entgelte": [...]}. Handle both.
    items = payload.get("entgelte") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        # also accept a flat single-value envelope
        items = [payload] if isinstance(payload, dict) else []

    year = None
    if isinstance(payload, dict):
        year = payload.get("jahr") or payload.get("year") or payload.get("stand")

    rows: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        val = (it.get("entgelt") or it.get("median") or it.get("median_gross_monthly")
               or it.get("wert") or it.get("value"))
        try:
            median = float(val)
        except (TypeError, ValueError):
            continue
        region = (it.get("region") or it.get("bundesland") or it.get("regionName")
                  or "")  # '' = all-Germany aggregate
        sex = (it.get("geschlecht") or it.get("sex") or "")
        rows.append({
            "country": "DE",
            "kldb": kldb,
            "region": str(region),
            "median_gross_monthly": median,
            "year": int(float(year)) if year not in (None, "") else None,
            "sex": str(sex),
        })
    return rows


def fetch_wages(force: bool = False, kldb_codes: list[str] | None = None,
                time_cap_s: float = 600.0, sleep_s: float = 0.5) -> list[dict]:
    """Fetch + cache Entgeltatlas median monthly wages by KldB. Cache IS the checkpoint."""
    f = _wages_file()
    if f.exists() and not force:
        return load_wages()

    codes = list(dict.fromkeys(kldb_codes or DEFAULT_KLDB))  # dedup, preserve order
    out: list[dict] = []
    t0 = time.time()
    for i, kldb in enumerate(codes, 1):
        if time.time() - t0 > time_cap_s:
            log.warning("bundesagentur(wages): time cap %ss hit — landing partial", time_cap_s)
            break
        try:
            rows = _fetch_kldb_wage(kldb)
            out.extend(rows)
            print(f"[bundesagentur] Entgeltatlas {kldb}: {len(rows)} wage rows "
                  f"({i}/{len(codes)})", flush=True)
        except Exception as e:  # noqa: BLE001 — one KldB must not sink the run
            log.warning("bundesagentur(wages): KldB %s failed (%s) — skip "
                        "(undocumented API, pin=%s)", kldb, e, ENTGELTATLAS_API_VERSION)
        time.sleep(sleep_s)  # polite

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("Entgeltatlas wages: %d rows across %d KldB codes",
             len(out), len({r["kldb"] for r in out}))
    return out


def load_wages() -> list[dict]:
    f = _wages_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


# ─────────────────────────────── (b) Jobsuche vacancies ──────────────────────────────

def _fetch_vacancy_count(keyword: str, timeout: int = 45) -> int | None:
    """Query Jobsuche for one occupation keyword → total vacancy count (.maxErgebnisse).

    We request size=1 and read only the aggregate hit-count. The per-hit ``arbeitgeber``
    (employer) field is never read or landed — ROLES-ONLY. Returns None on failure.
    """
    qs = urllib.parse.urlencode({
        "was": keyword,
        "size": 1,        # we only want the counter, not the listings
        "page": 1,
        "angebotsart": 1,  # 1 = Arbeit (regular employment), excludes training/etc.
    })
    req = urllib.request.Request(
        f"{JOBSUCHE_URL}?{qs}",
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            # UNOFFICIAL public key (pinned + flagged); overridable via settings.
            "X-API-Key": _jobsuche_key(),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))
    count = payload.get("maxErgebnisse")
    try:
        return int(count)
    except (TypeError, ValueError):
        return None


def fetch_vacancies(force: bool = False, keywords: list[str] | None = None,
                    time_cap_s: float = 600.0, sleep_s: float = 0.5) -> list[dict]:
    """Fetch + cache DE vacancy counts per occupation keyword. Cache IS the checkpoint.

    Credential-graceful: if no key is available at all (shouldn't happen — a pinned
    public default exists) we warn + return []. Employer data is dropped by construction.
    """
    f = _vacancies_file()
    if f.exists() and not force:
        return load_vacancies()

    if not _jobsuche_key():
        log.warning("bundesagentur(vacancies): no Jobsuche key — skipping vacancy fetch")
        return []

    kws = list(dict.fromkeys(keywords or DEFAULT_VACANCY_KEYWORDS))
    today = time.strftime("%Y-%m-%d")
    out: list[dict] = []
    t0 = time.time()
    for i, kw in enumerate(kws, 1):
        if time.time() - t0 > time_cap_s:
            log.warning("bundesagentur(vacancies): time cap %ss hit — landing partial",
                        time_cap_s)
            break
        try:
            count = _fetch_vacancy_count(kw)
            if count is None:
                log.warning("bundesagentur(vacancies): '%s' returned no count — skip", kw)
                time.sleep(sleep_s)
                continue
            out.append({
                "country": "DE",
                "occupation": kw,
                "count": count,
                "date": today,
            })
            print(f"[bundesagentur] Jobsuche '{kw}': {count} vacancies "
                  f"({i}/{len(kws)})", flush=True)
        except Exception as e:  # noqa: BLE001 — one keyword must not sink the run
            log.warning("bundesagentur(vacancies): '%s' failed (%s) — skip "
                        "(unofficial key, pin=%s)", kw, e, JOBSUCHE_API_VERSION)
        time.sleep(sleep_s)  # polite

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("Jobsuche vacancies: %d occupation counts (DE)", len(out))
    return out


def load_vacancies() -> list[dict]:
    f = _vacancies_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


# ─────────────────────────────────── orchestration ───────────────────────────────────

def run(**kw) -> dict:
    """Land + cache both DE signals: Entgeltatlas wages + Jobsuche vacancies.

    Accepts force / time_cap_s / sleep_s / kldb_codes / keywords (forwarded as relevant).
    Connector entrypoint — collect_all calls this.
    """
    force = kw.get("force", False)
    time_cap_s = kw.get("time_cap_s", 600.0)
    sleep_s = kw.get("sleep_s", 0.5)

    wages = fetch_wages(force=force, kldb_codes=kw.get("kldb_codes"),
                        time_cap_s=time_cap_s, sleep_s=sleep_s)
    vacancies = fetch_vacancies(force=force, keywords=kw.get("keywords"),
                                time_cap_s=time_cap_s, sleep_s=sleep_s)

    return {
        "rows": len(wages) + len(vacancies),
        "wages": len(wages),
        "vacancies": len(vacancies),
        "kldb_codes": sorted({r["kldb"] for r in wages}),
        "occupations": sorted({r["occupation"] for r in vacancies}),
        "country": "DE",
        "written": bool(wages or vacancies),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
