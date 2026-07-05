"""PayScale — the 7-country role × experience-BRACKET backbone of strata's estimate lens.

Where AmbitionBox gives India a true per-YEAR axis, PayScale is the only recon'd source
that spans all seven strata countries (IN US GB CA AU SG DE) with a consistent role page
shape. It never resolves to single-year cells — its experience axis is coarse BRACKETS
(1-4, 5-9, 10-19, 20+ years, sometimes "<1 year") — so it anchors the cross-country
skeleton while AmbitionBox fills India's fine grain.

Where the numbers live (recon-verified 2026-07-05): each research page is a server-rendered
Next.js page whose ``<script id="__NEXT_DATA__">`` JSON carries
``props.pageProps.pageData``:

  * ``.compensation.salary`` — the role-overall base-annual percentiles
    (keys "10"/"25"/"50"/"75"/"90" + ``profileCount``). This is BASE salary, not total
    comp (``.total`` and ``.bonus`` exist alongside but are deliberately NOT parsed — we
    keep the ``basis="base-annual"`` label honest).
  * ``.byDimension['Job by Experience'].rows`` — one row per experience bracket, each with
    ``name`` (the bracket string, e.g. "1-4 years"), ``profileCount``, and ``range['50']``
    (bracket median only; PayScale exposes no per-bracket percentiles).
  * ``.dimensions.job`` — the human role title; ``.currencyCode`` — the ISO currency;
    ``.lastUpdated`` — the page refresh timestamp.

One GET per (role, country) page. We emit ONE row per experience bracket
(``yoe=None`` + ``level_label=<bracket string>`` — PayScale gives ranges, never a year, so
we NEVER invent a years number) PLUS one role-overall row (the ``.compensation.salary``
percentiles, ``level_label=None``).

URL shape: ``https://www.payscale.com/research/{CC}/Job={Role_Slug}/Salary`` where CC is
PayScale's country code — IN US UK CA AU SG DE. NOTE PayScale uses **UK**, not GB; we map
its "UK" pages back to our ISO2 "GB". ``Role_Slug`` is Title_Case_With_Underscores
(e.g. "Analytics_Engineer", "Software_Engineer").

Crawl posture: desktop Chrome UA, ~2.5s jittered throttle, circuit breaker on consecutive
transport failures, fetch-once semantics — each (slug, country) cell caches its PARSED
record under ``staging/payscale/pages/`` and is never refetched (misses cache too, so a 404
role/country costs one request ever). The cache is the checkpoint; re-runs resume.

ESTIMATE lens rules: rows land source-labeled (source="payscale", kind="estimate") with
``profileCount`` kept as ``n`` for confidence weighting. ROLES-ONLY: designation-level
aggregates only — the ``Job by Employer`` / ``Job by Location`` / ``Job by Skill``
breakdowns on the same page are deliberately NOT parsed.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.taxonomy import match_title_to_role

log = get_logger("ingest.payscale")

BASE = "https://www.payscale.com/research/{cc}/Job={slug}/Salary"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 5       # transport-failure circuit breaker (site blocking us → stop)

# PayScale country code -> our ISO2. PayScale spells the UK "UK"; we store "GB".
COUNTRIES: list[tuple[str, str]] = [
    ("IN", "IN"),
    ("US", "US"),
    ("UK", "GB"),   # <-- PayScale "UK" == our "GB"
    ("CA", "CA"),
    ("AU", "AU"),
    ("SG", "SG"),
    ("DE", "DE"),
]

# (Role_Slug, our_role_id | None). Title_Case_With_Underscores per PayScale's URL scheme.
# A missing (role, country) page is a clean one-request 404, cached, reported. Rows with
# role_id=None still land (estimate-only surfaces the catalog resolves later).
SLUGS: list[tuple[str, str | None]] = [
    ("Software_Engineer", "swe"),
    ("Machine_Learning_Engineer", "ml-eng"),
    ("Data_Engineer", "data-eng"),
    ("Data_Scientist", "data-sci"),
    ("Front_End_Developer_%2F_Engineer", "frontend"),
    ("Back_End_Developer%2FEngineer", "backend"),
    ("DevOps_Engineer", "devops"),
    ("Site_Reliability_Engineer_(SRE)", "sre"),
    ("Cyber_Security_Engineer", "security"),
    ("Cloud_Architect", "cloud-arch"),
    ("Product_Manager%2C_Software", "pm"),
    ("Analytics_Engineer", None),   # recon-verified live (US page cached)
]


def _staging_dir():
    d = settings.staging_dir / "payscale"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(slug: str, cc: str):
    safe = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")
    return _staging_dir() / "pages" / f"{safe}_{cc}.json"


def _fetch_html(slug: str, cc: str, timeout: int = 30) -> str:
    url = BASE.format(cc=cc, slug=slug)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _num(v):
    """PayScale percentile values are floats; keep None as None, coerce clean numbers."""
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _parse_page(html: str, slug: str, cc: str, our_country: str,
                role_id_seed: str | None) -> dict:
    """__NEXT_DATA__ → a bundle of estimate rows (overall + one per experience bracket),
    or {"miss": True} when the (role, country) page doesn't exist / carries no salary
    (a real signal — cache it)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {"miss": True, "reason": "no __NEXT_DATA__"}
    try:
        pd = json.loads(m.group(1))["props"]["pageProps"]["pageData"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"miss": True, "reason": "no pageData"}
    if not isinstance(pd, dict):
        return {"miss": True, "reason": "pageData not object"}

    surface = (pd.get("dimensions") or {}).get("job") \
        or slug.replace("_", " ").replace("%2F", "/").title()
    currency = pd.get("currencyCode") or ""
    retrieved = time.strftime("%Y-%m-%d")
    # Prefer the taxonomy resolver on the real title; fall back to the seed role_id.
    role_id = match_title_to_role(surface) or role_id_seed

    def base_row():
        return {
            "source": "payscale", "kind": "estimate",
            "role_id": role_id, "role_surface": surface,
            "country": our_country, "currency": currency,
            "basis": "base-annual",
            "yoe": None, "level_label": None,
            "median": None, "p25": None, "p75": None, "p10": None, "p90": None,
            "n": None, "retrieved": retrieved,
        }

    rows: list[dict] = []

    # --- role-overall row: compensation.salary (BASE percentiles) ---
    comp = (pd.get("compensation") or {}).get("salary") or {}
    overall_median = _num(comp.get("50"))
    if overall_median is not None:
        r = base_row()
        r.update({
            "median": overall_median,
            "p10": _num(comp.get("10")), "p25": _num(comp.get("25")),
            "p75": _num(comp.get("75")), "p90": _num(comp.get("90")),
            "n": comp.get("profileCount"),
        })
        rows.append(r)

    # --- one row per experience bracket: byDimension['Job by Experience'].rows ---
    exp_dim = (pd.get("byDimension") or {}).get("Job by Experience") or {}
    for br in (exp_dim.get("rows") or []):
        if not isinstance(br, dict):
            continue
        med = _num((br.get("range") or {}).get("50"))
        label = br.get("name")
        if med is None or not label:
            continue
        r = base_row()
        r.update({
            "median": med,
            "level_label": label,   # native bracket string; yoe stays None (never invent)
            "n": br.get("profileCount"),
        })
        rows.append(r)

    if not rows:
        return {"miss": True, "reason": "no salary rows"}
    return {"rows": rows}


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch every (role × country) cell not yet cached. Cache = checkpoint.

    ``max_units`` caps the number of ROLE slugs crawled (each spans all 7 countries)."""
    t0 = time.time()
    fetched = cached = misses = rows = 0
    consec_fail = 0
    slugs = SLUGS[:max_units] if max_units else SLUGS
    for slug, role_id_seed in slugs:
        for cc, our_country in COUNTRIES:
            if time_cap_s is not None and (time.time() - t0) > time_cap_s:
                log.warning("payscale: time cap %.0fs hit — landing partial", time_cap_s)
                return _summary(fetched, cached, misses, t0)
            path = _cell_path(slug, cc)
            if path.exists() and not force:
                cached += 1
                continue
            try:
                html = _fetch_html(slug, cc)
                consec_fail = 0
            except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked
                consec_fail += 1
                log.warning("payscale: fetch failed %s/%s e=%s [consec %d]",
                            slug, cc, str(e)[:80], consec_fail)
                if consec_fail >= MAX_CONSEC_FAIL:
                    log.error("payscale: %d consecutive failures — site likely "
                              "blocking; stopping (resume later)", consec_fail)
                    return _summary(fetched, cached, misses, t0)
                time.sleep(THROTTLE * 2)
                continue
            rec = _parse_page(html, slug, cc, our_country, role_id_seed)
            path.write_text(json.dumps(rec), encoding="utf-8")
            fetched += 1
            if rec.get("miss"):
                misses += 1
                log.info("payscale: %s/%s — no page (%s)", slug, cc, rec.get("reason"))
            else:
                n_rows = len(rec["rows"])
                rows += n_rows
                log.info("payscale: %s/%s → %d rows (overall+brackets)", slug, cc, n_rows)
            time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("payscale summary: %s", s)
    return s


def _aggregate() -> list[dict]:
    """Fuse all cached page records into the single aggregate the fusion step reads."""
    sd = _staging_dir()
    rows: list[dict] = []
    for f in sorted((sd / "pages").glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if rec.get("miss"):
            continue
        for r in rec.get("rows", []):
            rows.append(r)
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


if __name__ == "__main__":  # pragma: no cover — manual smoke: 1 slug only
    print(json.dumps(run(max_units=1), indent=2))
