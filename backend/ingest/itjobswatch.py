"""ITJobsWatch — UK job-advert salary ESTIMATES by role (percentile spread, no YoE axis).

The estimate lens (2026-07-04): no official UK table reaches role-level salary with a
full percentile spread, so strata ingests ITJobsWatch's pre-computed benchmarks. ITJW
mines UK permanent job *adverts* (not payroll) and, per normalized job title, publishes
the median plus the 10th/25th/75th/90th percentiles, the sample size ("number of
salaries quoted"), a median YoY %-change, and the top co-occurring skills. It is the UK
anchor of the estimate wave: broad title coverage, honest sample sizes, GBP base-annual.

Where the numbers live (recon-verified against the cached page): each title page is
STATIC server-rendered HTML — no __NEXT_DATA__, no JSON-LD. The salary block is the
FIRST ``<table class="summary">`` inside ``<section id="headline_statistics">``; its
rows are labelled (10th Percentile, 25th Percentile, Median annual salary…) and each
row's FIRST ``<td class="fig">`` is the current 6-month period (the next two columns are
the prior two years — ignored). A SECOND ``<table class="summary">`` on the same page is
the all-IT-jobs aggregate ("All Permanent IT Job Roles") — deliberately NOT parsed; it
is not this role. The canonical role surface is the ``<h2>`` in that section (ITJW's
normalized title, e.g. "Development Operations Engineer").

No experience axis exists on ITJW — the percentile spread *is* the distribution, so
``yoe=None`` and ``level_label=None`` on every row (never invent years from percentiles).
Values are advertised UK salaries → ``basis="base-annual"``, ``country="GB"``,
``currency="GBP"``.

Crawl posture: desktop Chrome UA, ~2.5s jittered throttle, consecutive-failure circuit
breaker, fetch-once semantics — each slug caches its PARSED record under
``staging/itjobswatch/pages/`` and is never refetched (a 404/miss caches too, so a bad
slug costs one request ever). The cache is the checkpoint; re-runs resume.

ESTIMATE lens rules: rows land source-labeled (source="itjobswatch", kind="estimate")
with sample sizes kept for confidence weighting. ROLES-ONLY: only the role-level salary
summary is parsed; the per-location median table, the co-occurring-skill %s, and every
company/employer figure on the page are deliberately NOT emitted as salary rows.
"""
from __future__ import annotations

import html as _html
import json
import random
import re
import time
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.taxonomy import match_title_to_role

log = get_logger("ingest.itjobswatch")

BASE = "https://www.itjobswatch.co.uk/jobs/uk/{slug}.do"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 5       # transport-failure circuit breaker (site blocking us → stop)

# Role/skill slugs. The URL slug is the title with spaces (ITJW canonicalizes to
# `%20` in the path); we pass a space-joined slug and let urllib quote it. Best-guess
# slugs — a miss is a clean one-request 404, cached, reported.
SLUGS: list[str] = [
    "devops engineer",
    "site reliability engineer",
    "software developer",
    "software engineer",
    "data scientist",
    "data engineer",
    "data analyst",
    "machine learning engineer",
    "ai engineer",
    "front end developer",
    "back end developer",
    "full stack developer",
    "cloud engineer",
    "cloud architect",
    "platform engineer",
    "security engineer",
    "mobile developer",
    "qa engineer",
    "product manager",
    "engineering manager",
    "solutions architect",
    "database administrator",
]

# Summary-table row label → the percentile key we emit. The first <td class="fig"> in
# each matched row is the current period; later columns (prior years) are ignored.
_PCT_ROWS = {
    "10": "p10",
    "25": "p25",
    "50": "median",   # "Median annual salary (50th Percentile)"
    "75": "p75",
    "90": "p90",
}


def _staging_dir():
    d = settings.staging_dir / "itjobswatch"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(slug: str):
    safe = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    return _staging_dir() / "pages" / f"{safe}.json"


def _fetch_html(slug: str, timeout: int = 30) -> str:
    # ITJW paths use %20 for spaces; urllib.request.quote via Request won't re-encode a
    # pre-quoted path, so quote the slug explicitly (keep '/' out — slugs have none).
    url = BASE.format(slug=urllib.request.quote(slug, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "en-GB,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _money(cell: str) -> float | None:
    """'&#163;54,468' / '£54,468' / '-' → 54468.0 or None."""
    digits = re.sub(r"[^\d.]", "", _html.unescape(cell))   # strip £, commas, tags
    if not digits or digits == ".":
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _int(cell: str) -> int | None:
    digits = re.sub(r"[^\d]", "", _html.unescape(cell))
    return int(digits) if digits else None


def _first_summary_table(html: str) -> str | None:
    """Return the innerHTML of the FIRST <table class="summary"> — the role's salary
    block. Anything after the second such table (all-IT aggregate) is never seen."""
    m = re.search(r'<table class="summary">(.*?)</table>', html, re.S)
    return m.group(1) if m else None


def _row_first_fig(table_html: str, label_regex: str) -> str | None:
    """In the summary table, find the <tr> whose first <td> label matches, and return
    that row's FIRST <td class="fig"> raw cell (current period)."""
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 2:
            continue
        label = re.sub(r"<[^>]+>", "", cells[0])          # strip <sup>, <a>, etc.
        label = _html.unescape(label).strip()
        if re.search(label_regex, label, re.I):
            return cells[1]                                # first data column
    return None


def _parse_page(html: str, slug: str) -> dict:
    """Static HTML → one estimate record, or {"miss": True} when the title page doesn't
    exist / carries no salary summary (a real signal — cache it)."""
    table = _first_summary_table(html)
    if not table:
        return {"miss": True, "reason": "no summary table"}

    # canonical role surface: <h2> inside #headline_statistics (fallback: slug title)
    surface = slug.title()
    hm = re.search(r'<section id="headline_statistics">.*?<h2>(.*?)</h2>', html, re.S)
    if hm:
        h = re.sub(r"<br\s*/?>", " ", hm.group(1))
        h = re.sub(r"<[^>]+>", "", h)
        surface = _html.unescape(h).replace(" UK", "").strip() or surface

    rec: dict = {
        "source": "itjobswatch", "kind": "estimate",
        "role_id": None, "slug": slug,
        "role_surface": surface,
        "country": "GB", "currency": "GBP", "basis": "base-annual",
        "yoe": None, "level_label": None,
        "median": None, "p25": None, "p75": None, "p10": None, "p90": None,
        "n": None, "retrieved": time.strftime("%Y-%m-%d"),
    }

    # percentiles: "10th Percentile", "25th Percentile",
    # "Median annual salary (50th Percentile)", "75th Percentile", "90th Percentile"
    for pctile, key in _PCT_ROWS.items():
        if key == "median":
            cell = _row_first_fig(table, r"Median annual salary")
        else:
            cell = _row_first_fig(table, rf"^\s*{pctile}\s*th\s+Percentile\s*$")
        if cell is not None:
            rec[key] = _money(cell)

    # sample size — "Number of salaries quoted"
    ncell = _row_first_fig(table, r"Number of salaries quoted")
    if ncell is not None:
        rec["n"] = _int(ncell)

    # YoY median change — "Median % change year-on-year" (informational, kept honest)
    ycell = _row_first_fig(table, r"^\s*Median % change year-on-year")
    if ycell is not None:
        ytxt = _html.unescape(re.sub(r"<[^>]+>", "", ycell)).strip()
        ym = re.search(r"[-+]?\d+(?:\.\d+)?", ytxt)
        rec["median_yoy_pct"] = float(ym.group()) if ym else None

    if not rec["median"]:
        return {"miss": True, "reason": "no median salary"}

    rec["role_id"] = match_title_to_role(surface)          # may be None — keep the row
    return rec


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch every slug not yet cached. Cache = checkpoint."""
    t0 = time.time()
    fetched = cached = misses = rows = 0
    consec_fail = 0
    slugs = SLUGS[:max_units] if max_units else SLUGS
    for slug in slugs:
        if time_cap_s is not None and (time.time() - t0) > time_cap_s:
            log.warning("itjobswatch: time cap %.0fs hit — landing partial", time_cap_s)
            return _summary(fetched, cached, misses, t0)
        path = _cell_path(slug)
        if path.exists() and not force:
            cached += 1
            continue
        try:
            html = _fetch_html(slug)
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked/404
            # a 404 is a real "no such title" signal — cache it as a miss, don't retry
            code = getattr(e, "code", None)
            if code == 404:
                path.write_text(json.dumps({"miss": True, "reason": "404"}),
                                encoding="utf-8")
                fetched += 1
                misses += 1
                log.info("itjobswatch: %s — 404 (no such title)", slug)
                time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
                continue
            consec_fail += 1
            log.warning("itjobswatch: fetch failed %s e=%s (%s) [consec %d]",
                        slug, code, str(e)[:80], consec_fail)
            if consec_fail >= MAX_CONSEC_FAIL:
                log.error("itjobswatch: %d consecutive failures — site likely "
                          "blocking; stopping (resume later)", consec_fail)
                return _summary(fetched, cached, misses, t0)
            time.sleep(THROTTLE * 2)
            continue
        rec = _parse_page(html, slug)
        path.write_text(json.dumps(rec), encoding="utf-8")
        fetched += 1
        if rec.get("miss"):
            misses += 1
            log.info("itjobswatch: %s — no salary (%s)", slug, rec.get("reason"))
        else:
            rows += 1
            log.info("itjobswatch: %s → median £%.0f (n=%s, role_id=%s)",
                     rec["role_surface"], rec["median"], rec["n"], rec["role_id"])
        time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("itjobswatch summary: %s", s)
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
    print(json.dumps(run(max_units=2), indent=2))
