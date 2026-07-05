"""SEEK (AU) + JobStreet (SG) — advertised role-salary ESTIMATES from the Seek platform.

Two countries, one connector: SEEK Australia and JobStreet Singapore run the same
Seek career-advice codebase, so a single parser serves both. Each role page is a
server-rendered React page carrying ``window.__APOLLO_STATE__`` (the GraphQL cache)
plus a schema.org ``Occupation`` JSON-LD block. The role-level salary aggregate lives
in the Apollo cache at ``Role:{slug}.salarySuggestion`` (salary_median / salary_min /
salary_max and a wider ``suggestion.min/max`` band), with the advertised-ad count in
``roleReviewStatistics.total``. The JSON-LD ``estimatedSalary``
(``MonetaryAmountDistribution``) is the fallback / percentile source
(percentile10/25/median/75/90 + currency + duration).

Numbers are Seek's model of **employer-advertised** salaries, so kind="estimate".
Basis differs by market: AU pages quote annual AUD (basis="base-annual"); SG JobStreet
pages quote **monthly** SGD (basis="monthly") — the connector labels each honestly and
NEVER annualises the SG figure. There is no years-of-experience axis on these pages
(yoe=null); seniority is expressed as separate role slugs (e.g. senior-software-engineer),
so seniority — when the slug encodes it — lands in level_label, never as invented years.

URL patterns (recon 2026-07-05):
  AU: https://au.seek.com/career-advice/role/{slug}/salary   (AUD, annual)
  SG: https://sg.jobstreet.com/career-advice/role/{slug}/salary  (SGD, monthly)

Crawl posture mirrors the other connectors: desktop Chrome UA, ~2.5s jittered throttle,
consecutive-failure circuit breaker, fetch-once cache (each (market, slug) cell caches
its PARSED record; misses are cached too so a dead slug costs one request ever). The
cache is the checkpoint; re-runs resume.

ROLES-ONLY charter: only the role/designation-level aggregate is parsed. The Apollo
cache on the same page also holds per-employer ``Job``/``JobEmployer`` ad objects and a
per-city location breakdown — the employer objects are deliberately NEVER parsed; only
the national role aggregate is emitted (the fixed row shape has no city axis).
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

log = get_logger("ingest.seek_jobstreet")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 5       # transport-failure circuit breaker (site blocking us → stop)

# Per-market host / country / currency / basis. AU pages are annual AUD; SG JobStreet
# pages quote MONTHLY SGD — kept as basis="monthly", never annualised.
MARKETS: dict[str, dict] = {
    "au": {
        "base": "https://au.seek.com/career-advice/role/{slug}/salary",
        "country": "AU", "currency": "AUD", "basis": "base-annual",
    },
    "sg": {
        "base": "https://sg.jobstreet.com/career-advice/role/{slug}/salary",
        "country": "SG", "currency": "SGD", "basis": "monthly",
    },
}

# ~15 role slugs per market (Seek's own role aliases). A miss is a clean one-request
# 404, cached + reported. Seniority-encoding slugs carry a native level_label; the
# base slugs carry level_label=None. Slugs are shared across AU/SG where the taxonomy
# overlaps (JobStreet uses the same Seek role aliases).
SLUGS: list[tuple[str, str | None]] = [
    ("software-engineer", None),
    ("senior-software-engineer", "Senior"),
    ("full-stack-developer", None),
    ("front-end-developer", None),
    ("back-end-developer", None),
    ("web-developer", None),
    ("mobile-developer", None),
    ("data-engineer", None),
    ("data-scientist", None),
    ("data-analyst", None),
    ("machine-learning-engineer", None),
    ("devops-engineer", None),
    ("site-reliability-engineer", None),
    ("cloud-engineer", None),
    ("security-engineer", None),
    ("product-manager", None),
    ("engineering-manager", "Manager"),
    ("qa-engineer", None),
]


def _staging_dir():
    d = settings.staging_dir / "seek_jobstreet"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(market: str, slug: str):
    return _staging_dir() / "pages" / f"{market}_{slug}.json"


def _fetch_html(market: str, slug: str, timeout: int = 30) -> str:
    url = MARKETS[market]["base"].format(slug=slug)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _balanced_object(text: str, start: int) -> str | None:
    """Return the JSON object beginning at the '{' at ``start`` (brace-balanced, string
    aware). The Apollo blob is minified and contains nested braces inside strings, so a
    naive regex overshoots — we scan for the matching close brace."""
    depth = 0
    i = start
    instr = False
    esc = False
    while i < len(text):
        c = text[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return None


def _apollo_state(html: str) -> dict | None:
    idx = html.find("window.__APOLLO_STATE__")
    if idx == -1:
        return None
    brace = html.find("{", idx)
    if brace == -1:
        return None
    raw = _balanced_object(html, brace)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _json_ld_salary(html: str) -> dict | None:
    """schema.org Occupation → estimatedSalary MonetaryAmountDistribution (fallback +
    percentile source: cleaner p10/25/75/90 than the Apollo band)."""
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                         html, re.S):
        try:
            ld = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        blocks = ld if isinstance(ld, list) else [ld]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            est = b.get("estimatedSalary")
            if isinstance(est, dict) and est.get("median") is not None:
                return est
    return None


def _parse_page(html: str, market: str, slug: str, level_label: str | None) -> dict:
    """Apollo ``Role:{slug}.salarySuggestion`` (+ JSON-LD percentiles) → one estimate
    record, or {"miss": True} when the role page carries no salary aggregate."""
    mk = MARKETS[market]
    state = _apollo_state(html)
    role = None
    if state:
        role = state.get(f"Role:{slug}")
        if role is None:  # slug alias may differ from the Role id — take the sole Role
            roles = [v for k, v in state.items()
                     if isinstance(v, dict) and v.get("__typename") == "Role"
                     and v.get("salarySuggestion")]
            role = roles[0] if len(roles) == 1 else None

    ss = (role or {}).get("salarySuggestion") if isinstance(role, dict) else None
    ld = _json_ld_salary(html)

    if not ss and not ld:
        return {"miss": True, "reason": "no salarySuggestion / JSON-LD"}

    # Median: Apollo salary_median is authoritative (matches JSON-LD median); fall back
    # to JSON-LD median.
    median = None
    if ss and ss.get("salary_median") is not None:
        median = ss.get("salary_median")
    elif ld:
        median = ld.get("median")

    # Percentiles: prefer JSON-LD's true p10/25/75/90; else derive from the Apollo bands
    # (salary_min/max is the tighter mid band → p25/p75; suggestion.min/max is the wide
    # band → p10/p90). Honest: these are Seek's own quoted bands, not sample quantiles.
    if ld:
        p10 = ld.get("percentile10")
        p25 = ld.get("percentile25")
        p75 = ld.get("percentile75")
        p90 = ld.get("percentile90")
    else:
        sug = ss.get("suggestion") or {}
        p25 = ss.get("salary_min")
        p75 = ss.get("salary_max")
        p10 = sug.get("min")
        p90 = sug.get("max")

    n = None
    if isinstance(role, dict):
        stats = role.get("roleReviewStatistics") or {}
        n = stats.get("total")

    title = None
    if isinstance(role, dict):
        title = role.get("title")
    if not title:
        title = slug.replace("-", " ").title()

    # Currency is fixed by the market host (au.seek → AUD, sg.jobstreet → SGD). The
    # JSON-LD currency is only a cross-check; never let it override the host, so a page
    # served with a stale/foreign JSON-LD currency can't mislabel the row.
    currency = mk["currency"]
    if ld and ld.get("currency") and ld["currency"] != currency:
        log.warning("seek_jobstreet: %s/%s JSON-LD currency %s != market %s (keeping "
                    "market)", market, slug, ld["currency"], currency)

    rec = {
        "source": "seek_jobstreet",
        "kind": "estimate",
        "role_id": match_title_to_role(title),
        "role_surface": title,
        "country": mk["country"],
        "currency": currency,
        "basis": mk["basis"],
        "yoe": None,                       # no experience axis on these pages
        "level_label": level_label,        # native seniority from the slug, or None
        "median": median,
        "p25": p25, "p75": p75,
        "p10": p10, "p90": p90,
        "n": n,
        "market": market, "slug": slug,
        "retrieved": time.strftime("%Y-%m-%d"),
    }
    if not rec["median"]:
        return {"miss": True, "reason": "no median"}
    return rec


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch every (market × slug) cell not yet cached. Cache = checkpoint."""
    t0 = time.time()
    fetched = cached = misses = rows = 0
    consec_fail = 0
    units = [(m, slug, lvl) for m in MARKETS for slug, lvl in SLUGS]
    if max_units:
        units = units[:max_units]
    for market, slug, lvl in units:
        if time_cap_s is not None and (time.time() - t0) > time_cap_s:
            log.warning("seek_jobstreet: time cap %.0fs hit — landing partial", time_cap_s)
            break
        path = _cell_path(market, slug)
        if path.exists() and not force:
            cached += 1
            continue
        try:
            html = _fetch_html(market, slug)
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked / 404
            # A 404 is a real miss (bad slug), not a transport failure — cache it.
            code = getattr(e, "code", None)
            if code == 404:
                rec = {"miss": True, "reason": "http 404"}
                path.write_text(json.dumps(rec), encoding="utf-8")
                fetched += 1
                misses += 1
                consec_fail = 0
                log.info("seek_jobstreet: %s/%s — 404 (no such role)", market, slug)
                time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
                continue
            consec_fail += 1
            log.warning("seek_jobstreet: fetch failed %s/%s e=%s (%s) [consec %d]",
                        market, slug, str(e)[:80], code, consec_fail)
            if consec_fail >= MAX_CONSEC_FAIL:
                log.error("seek_jobstreet: %d consecutive failures — site likely "
                          "blocking; stopping (resume later)", consec_fail)
                break
            time.sleep(THROTTLE * 2)
            continue
        rec = _parse_page(html, market, slug, lvl)
        path.write_text(json.dumps(rec), encoding="utf-8")
        fetched += 1
        if rec.get("miss"):
            misses += 1
            log.info("seek_jobstreet: %s/%s — no salary (%s)", market, slug,
                     rec.get("reason"))
        else:
            rows += 1
            log.info("seek_jobstreet: %s/%s → median %s %s (n=%s)", market, slug,
                     rec["median"], rec["currency"], rec["n"])
        time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("seek_jobstreet summary: %s", s)
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
        if not rec.get("miss"):
            rows.append(rec)
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


if __name__ == "__main__":  # pragma: no cover — manual smoke: 2 units only
    print(json.dumps(run(max_units=2), indent=2))
