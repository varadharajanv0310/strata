"""AmbitionBox — India-deep aggregator salary ESTIMATES by role × years-of-experience.

The user-decided estimate lens (2026-07-04): published/licensed datasets never reach
role × experience-year granularity (there is no official table for "Data Scientist,
5 years, India"), so strata ingests the pre-computed estimates that salary aggregators
publish. AmbitionBox is the India anchor of that wave: the ONLY recon'd source with a
true PER-YEAR experience axis (``?experience=1..8``), thousands of designation pages
including genuine long-tail roles, and large sample sizes (Data Scientist: 57.6k
reports; at 5y: 7.1k).

Where the numbers live (recon-verified 2026-07-04): each designation page is a
server-rendered Next.js page whose ``<script id="__NEXT_DATA__">`` JSON carries
``props.pageProps.salaryData.profileSalaryData`` — medianCtc, percentiles
(p10/p25/p75/p90/p99), totalDatapoints, minExp/maxExp, lastUpdated. One GET per
(role, experience) cell; no XHR. Values are annual **CTC in INR** (labeled
``basis="ctc-annual"`` — CTC is not base salary; keep the label honest).

Crawl posture: local/residential only (datacenter IPs hang), desktop Chrome UA,
~2.5s jittered throttle, circuit breaker on consecutive transport failures, and
fetch-once semantics — each (slug, exp) cell caches its PARSED record under
``staging/ambitionbox/pages/`` and is never refetched (misses cache too, so 404
slugs cost one request ever). The cache is the checkpoint; re-runs resume.

ESTIMATE lens rules: rows land source-labeled (source="ambitionbox", kind="estimate")
with sample sizes kept for confidence weighting — never blended into the advertised/
realized/official lenses. ROLES-ONLY: designation-level aggregates; the per-company
breakdown on the same pages is deliberately NOT parsed.
"""
from __future__ import annotations

import json
import random
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.ambitionbox")

BASE = "https://www.ambitionbox.com/profile/{slug}-salary"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 5       # transport-failure circuit breaker (site blocking us → stop)
EXPERIENCE_YEARS = list(range(1, 9))   # ?experience=1..8 — the site's per-year cap

# (slug, our_role_id | None). Our 16 curated roles first (best-guess slugs — a miss
# is a clean one-request 404, cached, reported), then long-tail designations that
# have no strata role yet (role_id None → they land as estimate-only surfaces).
SLUGS: list[tuple[str, str | None]] = [
    ("software-engineer", "swe"),
    ("machine-learning-engineer", "ml-eng"),
    ("data-engineer", "data-eng"),
    ("data-scientist", "data-sci"),
    ("front-end-developer", "frontend"),
    ("back-end-developer", "backend"),
    ("devops-engineer", "devops"),
    ("site-reliability-engineer", "sre"),
    ("cyber-security-engineer", "security"),
    ("cloud-architect", "cloud-arch"),
    ("product-manager", "pm"),
    ("ux-designer", "ux"),
    ("mobile-application-developer", "mobile"),
    ("data-analyst", "data-analyst"),
    ("engineering-manager", "eng-mgr"),
    ("qa-engineer", "qa"),
    # ---- long tail (no curated strata role yet) ----
    ("analytics-engineer", None),          # recon-verified live (668 reports)
    ("platform-engineer", None),
    ("full-stack-developer", None),
    ("ai-engineer", None),
    ("big-data-engineer", None),
    ("lead-data-scientist", None),
    ("decision-scientist", None),
    ("research-scientist", None),
    ("solution-architect", None),
    ("data-architect", None),
    ("database-administrator", None),
    ("embedded-software-engineer", None),
    ("technical-program-manager", None),
    ("android-developer", None),
    # senior variants — the per-year axis caps around 5y on base designations
    # (higher years return null cells); seniors carry the >5y salary picture.
    ("senior-software-engineer", None),
    ("senior-data-scientist", None),
    ("senior-data-engineer", None),
    ("principal-software-engineer", None),
    ("tech-lead", None),
]


def _staging_dir():
    d = settings.staging_dir / "ambitionbox"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(slug: str, exp: int | None):
    return _staging_dir() / "pages" / f"{slug}_e{exp if exp is not None else 'all'}.json"


def _fetch_html(slug: str, exp: int | None, timeout: int = 30) -> str:
    url = BASE.format(slug=slug) + (f"?experience={exp}" if exp is not None else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _parse_page(html: str, slug: str, exp: int | None, role_id: str | None) -> dict:
    """__NEXT_DATA__ → one estimate record, or {"miss": True} when the designation
    page doesn't exist / carries no salary data (a real signal — cache it)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {"miss": True, "reason": "no __NEXT_DATA__"}
    try:
        data = json.loads(m.group(1))
        p = data["props"]["pageProps"]["salaryData"]["profileSalaryData"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return {"miss": True, "reason": "no profileSalaryData"}   # 404-style page
    pct = p.get("percentiles") or {}
    rec = {
        "source": "ambitionbox", "kind": "estimate",
        "role_id": role_id, "slug": slug,
        "role_surface": p.get("jobProfileName") or slug.replace("-", " ").title(),
        "country": "IN", "currency": "INR", "basis": "ctc-annual",
        "yoe": exp,                                   # None = all-experience aggregate
        "median": p.get("medianCtc"),
        "p10": pct.get("percentile10"), "p25": pct.get("percentile25"),
        "p75": pct.get("percentile75"), "p90": pct.get("percentile90"),
        "p99": pct.get("percentile99"),
        "n": p.get("totalDatapoints"),
        "min_exp": p.get("minExp"), "max_exp": p.get("maxExp"),
        "last_updated": p.get("lastUpdated"),
        "retrieved": time.strftime("%Y-%m-%d"),
    }
    if not rec["median"] or not rec["n"]:
        return {"miss": True, "reason": "empty salary data"}
    return rec


def fetch_all(max_slugs: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch every (slug × experience) cell not yet cached. Cache = checkpoint."""
    t0 = time.time()
    fetched = cached = misses = rows = 0
    consec_fail = 0
    slugs = SLUGS[:max_slugs] if max_slugs else SLUGS
    for slug, role_id in slugs:
        # all-exp page first: a missing designation 404s here → skip its year cells
        slug_missing = False
        for exp in [None] + EXPERIENCE_YEARS:
            if time_cap_s is not None and (time.time() - t0) > time_cap_s:
                log.warning("ambitionbox: time cap %.0fs hit — landing partial", time_cap_s)
                return _summary(fetched, cached, misses, t0)
            if slug_missing:
                break
            path = _cell_path(slug, exp)
            if path.exists() and not force:
                cached += 1
                continue
            try:
                html = _fetch_html(slug, exp)
                consec_fail = 0
            except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked
                consec_fail += 1
                log.warning("ambitionbox: fetch failed %s e=%s (%s) [consec %d]",
                            slug, exp, str(e)[:80], consec_fail)
                if consec_fail >= MAX_CONSEC_FAIL:
                    log.error("ambitionbox: %d consecutive failures — site likely "
                              "blocking; stopping (resume later)", consec_fail)
                    return _summary(fetched, cached, misses, t0)
                time.sleep(THROTTLE * 2)
                continue
            rec = _parse_page(html, slug, exp, role_id)
            path.write_text(json.dumps(rec), encoding="utf-8")
            fetched += 1
            if rec.get("miss"):
                misses += 1
                if exp is None:
                    slug_missing = True    # designation doesn't exist — skip its years
                    log.info("ambitionbox: %s — no page (%s)", slug, rec.get("reason"))
            else:
                rows += 1
                log.info("ambitionbox: %s e=%s → median ₹%.0f (n=%s)",
                         slug, exp if exp is not None else "all",
                         rec["median"], rec["n"])
            time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("ambitionbox summary: %s", s)
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


if __name__ == "__main__":  # pragma: no cover — manual smoke: 2 slugs only
    print(json.dumps(run(max_slugs=2), indent=2))
