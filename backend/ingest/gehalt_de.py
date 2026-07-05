"""Gehalt.de — Germany role-level gross-salary ESTIMATES with explicit YoE brackets.

The estimate lens (see ambitionbox.py) applied to the German market: Gehalt.de is a
large German compensation portal that publishes, per *Beruf* (occupation/designation),
a role-level gross-salary distribution AND — uniquely for a European source — a
"Gehalt nach Berufserfahrung" (salary by years-of-experience) breakdown split into the
four native brackets ``< 3 / 3–6 / 7–9 / > 9 Jahre``. That experience axis is exactly
the granularity strata wants and official German tables never reach.

Where the numbers live (recon-verified 2026-07-04 against a cached ``/beruf`` page):
each occupation page is server-rendered HTML carrying two payloads —
  1. a ``<script type="application/ld+json">`` schema.org **Occupation** whose
     ``estimatedSalary`` MonetaryAmountDistribution gives the OVERALL median +
     percentile25/75 (name="base", currency EUR, ``duration="P1M"`` — **monthly**
     gross; we annualise ×12 for the overall row), and
  2. an HTML ``<div class="gehaltsspanneBerufserfahrung">`` diagram whose heading is
     "Gehalt nach Berufserfahrung:" and whose rows each carry BOTH a
     ``<span class="year">`` (annual EUR, what we keep) and ``<span class="month">``.
     German number format: ``78.568`` = 78568 (dot is the thousands separator).

We emit one OVERALL row (yoe=null, level_label=null) plus one row PER YoE bracket
(yoe=null because a bracket is a range, level_label carries the native "< 3 Jahre"
string). basis="base-annual", country="DE", currency="EUR".

ROLES-ONLY charter (hard): the SAME pages also render "Gehalt nach Unternehmensgröße"
(by company size) and region diagrams — these are deliberately NOT parsed; we anchor
strictly on the diagram whose heading is "Gehalt nach Berufserfahrung" and only accept
rows labelled "… Jahre". No employer names, no company tables, no ratings.

Crawl posture (mirrors ambitionbox): desktop Chrome UA, ~2.5s jittered throttle,
consecutive-failure circuit breaker, fetch-once — each role page caches its PARSED
record under ``staging/gehalt_de/pages/`` and is never refetched (misses cache too, so
a wrong/redirected slug costs one request ever). The cache is the checkpoint.

GOTCHA — slugs: the URL is ``https://www.gehalt.de/beruf/{slug}`` with a German slug.
A wrong slug soft-redirects to a search/landing page that carries no Occupation
JSON-LD → parsed as a miss (cached). Slugs below are the recon'd German designations;
authoritative discovery is the sitemap (``/beruf/berufe-von-a-bis-z``), left to the
integrator to refresh — the seed list is deliberately conservative.
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

log = get_logger("ingest.gehalt_de")

BASE = "https://www.gehalt.de/beruf/{slug}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
THROTTLE = 2.5            # base seconds between requests (jittered ±40%)
MAX_CONSEC_FAIL = 5       # transport-failure circuit breaker (site blocking us → stop)

# (german_slug, english_surface_for_role_match). role_id is resolved live from the
# English surface via match_title_to_role (German titles won't match the taxonomy);
# a None result is kept — the row lands as an estimate-only surface. ~12 tech roles;
# a wrong slug is a clean one-request miss (cached, reported).
SLUGS: list[tuple[str, str]] = [
    ("softwareentwickler",              "Software Engineer"),
    ("data-scientist",                  "Data Scientist"),
    ("data-engineer",                   "Data Engineer"),
    ("machine-learning-engineer",       "Machine Learning Engineer"),
    ("devops-engineer",                 "DevOps Engineer"),
    ("frontend-entwickler",             "Frontend Developer"),
    ("backend-entwickler",              "Backend Developer"),
    ("fullstack-entwickler",            "Full Stack Developer"),
    ("it-sicherheitsexperte",           "Security Engineer"),
    ("cloud-architect",                 "Cloud Architect"),
    ("data-analyst",                    "Data Analyst"),
    ("mobile-entwickler",               "Mobile Developer"),
    ("product-manager",                 "Product Manager"),
    ("ux-designer",                     "UX Designer"),
]

# native YoE bracket labels we accept from the "Gehalt nach Berufserfahrung" diagram.
# Anything not ending in "Jahre" (e.g. "… Mitarbeiter" company-size rows) is rejected.
_BRACKET_RE = re.compile(r"Jahre?$")


def _staging_dir():
    d = settings.staging_dir / "gehalt_de"
    (d / "pages").mkdir(parents=True, exist_ok=True)
    return d


def _cell_path(slug: str):
    return _staging_dir() / "pages" / f"{slug}.json"


def _fetch_html(slug: str, timeout: int = 30) -> str:
    url = BASE.format(slug=slug)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "de-DE,de;q=0.9,en;q=0.6"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def _de_num(s: str) -> float | None:
    """German salary integer: '78.568' → 78568.0 ('.' is the thousands separator)."""
    s = s.strip().replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _occupation_ld(html_text: str) -> dict | None:
    """Return the schema.org Occupation JSON-LD block, or None (wrong/landing page)."""
    for m in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html_text, re.S,
    ):
        try:
            obj = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("@type") == "Occupation":
            return obj
    return None


def _overall_from_ld(occ: dict) -> dict | None:
    """Overall base distribution from Occupation.estimatedSalary (monthly EUR → ×12)."""
    dist = occ.get("estimatedSalary")
    if isinstance(dist, list):
        dist = next((d for d in dist if isinstance(d, dict)
                     and str(d.get("name", "")).lower() in ("base", "")), None) \
            or (dist[0] if dist else None)
    if not isinstance(dist, dict):
        return None
    # duration P1M (monthly) is what the site publishes; annualise to base-annual.
    factor = 12.0 if str(dist.get("duration", "")).upper() == "P1M" else 1.0

    def _f(key):
        v = dist.get(key)
        if v in (None, ""):
            return None
        try:
            return round(float(v) * factor)
        except (TypeError, ValueError):
            return None

    return {
        "median": _f("median"),
        "p25": _f("percentile25"), "p75": _f("percentile75"),
        "p10": _f("percentile10"), "p90": _f("percentile90"),
    }


def _brackets_from_html(html_text: str) -> list[dict]:
    """Parse ONLY the 'Gehalt nach Berufserfahrung' diagram → [{label, median}].

    Anchors on the heading, then reads the immediately-following diagram block's rows,
    keeping each row's annual <span class="year"> value. Company-size / region diagrams
    (which share the same row markup) are excluded by (a) slicing to this one diagram
    and (b) requiring the label to end in 'Jahre'."""
    h = html_text.find("Gehalt nach Berufserfahrung")
    if h == -1:
        return []
    # the experience diagram is the next <div class="diagram mb-10" ...> after the
    # heading; bound the slice at the FOLLOWING diagram container (the company-size
    # block, "diagram mb-10") so we never bleed into non-YoE rows. Note: individual
    # rows use class="diagram-row", so we must match the container class, not a bare
    # "diagram" prefix.
    dstart = html_text.find('class="diagram mb-10', h)
    if dstart == -1:
        return []
    dnext = html_text.find('class="diagram mb-10', dstart + 1)
    seg = html_text[dstart: dnext if dnext != -1 else dstart + 6000]

    out: list[dict] = []
    seen = set()
    for m in re.finditer(
        r'diagram-label"\s+data-value="([^"]+)"[\s\S]*?'
        r'<span class="year[^"]*">([\d.]+)\s*.\s*</span>',
        seg,
    ):
        label = _html.unescape(m.group(1)).replace("\xa0", " ").strip()
        # normalise the en-dash / stray chars in "3–6 Jahre" style labels
        label = re.sub(r"\s+", " ", label)
        if not _BRACKET_RE.search(label):
            continue                      # not a YoE bracket (e.g. Mitarbeiter) → skip
        val = _de_num(m.group(2))
        if val is None or label in seen:
            continue
        seen.add(label)
        out.append({"label": label, "median": round(val)})
    return out


def _parse_page(html_text: str, slug: str, surface: str) -> dict:
    """One occupation page → {rows: [...]} of estimate rows, or {"miss": True}.

    Rows: one OVERALL (level_label=null) from JSON-LD, plus one per YoE bracket.
    role_id resolved live from the English surface (kept even when None)."""
    occ = _occupation_ld(html_text)
    if occ is None:
        return {"miss": True, "reason": "no Occupation JSON-LD (wrong slug / landing)"}

    role_id = match_title_to_role(surface)
    role_surface = occ.get("name") or surface
    retrieved = time.strftime("%Y-%m-%d")

    def _row(*, yoe, level_label, median, p25=None, p75=None, p10=None, p90=None):
        return {
            "source": "gehalt_de", "kind": "estimate",
            "role_id": role_id, "role_surface": role_surface,
            "country": "DE", "currency": "EUR", "basis": "base-annual",
            "yoe": yoe, "level_label": level_label,
            "median": median, "p25": p25, "p75": p75, "p10": p10, "p90": p90,
            "n": None, "retrieved": retrieved,
        }

    rows: list[dict] = []
    overall = _overall_from_ld(occ)
    if overall and overall.get("median"):
        rows.append(_row(yoe=None, level_label=None, median=overall["median"],
                         p25=overall["p25"], p75=overall["p75"],
                         p10=overall["p10"], p90=overall["p90"]))
    for b in _brackets_from_html(html_text):
        # yoe stays null — a bracket is a RANGE, never a single year; the native
        # tier string lives in level_label (charter: don't invent years from a band).
        rows.append(_row(yoe=None, level_label=b["label"], median=b["median"]))

    if not rows:
        return {"miss": True, "reason": "Occupation present but no salary values"}
    return {"slug": slug, "rows": rows}


def fetch_all(max_units: int | None = None, time_cap_s: float | None = None,
              force: bool = False) -> dict:
    """Fetch every role page not yet cached. Cache = checkpoint (misses cached too)."""
    t0 = time.time()
    fetched = cached = misses = 0
    consec_fail = 0
    slugs = SLUGS[:max_units] if max_units else SLUGS
    for slug, surface in slugs:
        if time_cap_s is not None and (time.time() - t0) > time_cap_s:
            log.warning("gehalt_de: time cap %.0fs hit — landing partial", time_cap_s)
            break
        path = _cell_path(slug)
        if path.exists() and not force:
            cached += 1
            continue
        try:
            html_text = _fetch_html(slug)
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 — transport failure, maybe blocked
            consec_fail += 1
            log.warning("gehalt_de: fetch failed %s (%s) [consec %d]",
                        slug, str(e)[:80], consec_fail)
            if consec_fail >= MAX_CONSEC_FAIL:
                log.error("gehalt_de: %d consecutive failures — site likely blocking; "
                          "stopping (resume later)", consec_fail)
                break
            time.sleep(THROTTLE * 2)
            continue
        rec = _parse_page(html_text, slug, surface)
        path.write_text(json.dumps(rec), encoding="utf-8")
        fetched += 1
        if rec.get("miss"):
            misses += 1
            log.info("gehalt_de: %s — miss (%s)", slug, rec.get("reason"))
        else:
            n_rows = len(rec["rows"])
            med = rec["rows"][0]["median"]
            log.info("gehalt_de: %s → %d rows (overall median €%s)", slug, n_rows, med)
        time.sleep(THROTTLE + random.uniform(-1.0, 1.0))
    return _summary(fetched, cached, misses, t0)


def _summary(fetched: int, cached: int, misses: int, t0: float) -> dict:
    rows = _aggregate()
    s = {"fetched": fetched, "cached": cached, "misses": misses,
         "rows": len(rows), "elapsed_s": round(time.time() - t0, 1)}
    log.info("gehalt_de summary: %s", s)
    return s


def _aggregate() -> list[dict]:
    """Flatten all cached page records into the single aggregate the fusion step reads."""
    sd = _staging_dir()
    rows: list[dict] = []
    for f in sorted((sd / "pages").glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not rec.get("miss"):
            rows.extend(rec.get("rows", []))
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
