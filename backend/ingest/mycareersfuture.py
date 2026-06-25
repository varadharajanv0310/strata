"""MyCareersFuture (Singapore) — the **SG government-board posting lens**: posted
salary range (min/max, SGD) + skills per advertised role, straight from Workforce
Singapore / the Government Technology Agency's public job-board API.

NEW SIGNAL strata doesn't otherwise have for SG: an *employer-stated* salary band on
a *live* posting (what a role is advertised to pay right now), distinct from the
official survey wage spine (ILOSTAT / MOM). MyCareersFuture is a government board, so
every posting carries a mandated salary range — a rare honest source of posted-pay
breadth, plus the role's required skills tags, which feed demand. The posting count
per title is itself the demand signal.

Grain: country=SG × job title (role) × posting date, carrying ``salary_min`` /
``salary_max`` (monthly SGD) + the posting's skills list. It feeds two facts:
- ``fact_salary_job`` — posted SG salary range per role (the posting lens).
- ``fact_demand``   — SG posting volume / skill frequency per role.

Obtained from the public, key-less search endpoint
``https://api.mycareersfuture.gov.sg/v2/search`` (POST, paginated). Legitimacy: this
is the official public API the mycareersfuture.gov.sg site itself calls; no auth, no
scraping — we read only the posted salary band + skills + title and DROP the employer
/ company / postedCompany blocks entirely (ROLES-ONLY). The endpoint shape is
unofficial (no published contract) so it is treated as fragile: every request is
wrapped, pagination is heartbeat-logged and capped, and any failure degrades to a
partial land rather than sinking the run. Not run in this pass — coded for the later
run. The cached ``postings.json`` is the checkpoint.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.mycareersfuture")

# Official public search API behind mycareersfuture.gov.sg. No key.
SEARCH_URL = "https://api.mycareersfuture.gov.sg/v2/search?limit={limit}&page={page}"
HEADERS = {
    "User-Agent": "strata/1.0 (+research; roles-only job-market explorer)",
    "Content-Type": "application/json",
    "Accept": "application/json",
}
PAGE_LIMIT = 100          # rows per page the API accepts
HEARTBEAT_EVERY = 5       # flush a progress line every N pages


def _staging_dir():
    d = settings.staging_dir / "mycareersfuture"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "postings.json"


def _post(url: str, body: dict, timeout: int = 45) -> dict:
    """POST a JSON search body → decoded dict. Raises on transport/HTTP error."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _skills_of(job: dict) -> list[str]:
    """Pull the posting's skill tags. The API returns ``skills`` as a list of
    objects ({skill: '...'}) or plain strings depending on shape — handle both."""
    out: list[str] = []
    for s in job.get("skills") or []:
        if isinstance(s, dict):
            name = s.get("skill") or s.get("name") or s.get("title")
        else:
            name = s
        name = (name or "").strip()
        if name:
            out.append(name)
    return out


def _parse_job(job: dict) -> dict | None:
    """One API job object → a roles-only posting row. Returns None if unusable.

    ROLES-ONLY: we deliberately read ONLY title / salary band / skills / date and
    never touch ``postedCompany`` / ``hiringCompany`` / employer fields.
    """
    title = ((job.get("title") or "")).strip()
    if not title:
        return None
    salary = job.get("salary") or {}
    try:
        smin = salary.get("minimum")
        smax = salary.get("maximum")
        salary_min = float(smin) if smin is not None else None
        salary_max = float(smax) if smax is not None else None
    except (TypeError, ValueError):
        salary_min = salary_max = None
    # posting date: prefer original posted date, fall back to new-posting date.
    metadata = job.get("metadata") or {}
    date = (metadata.get("originalPostingDate")
            or metadata.get("newPostingDate")
            or job.get("postedDate")
            or "")
    return {
        "country": "SG",
        "title": title,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_period": (salary.get("type") or {}).get("salaryType")
        if isinstance(salary.get("type"), dict) else salary.get("type"),
        "skills": _skills_of(job),
        "date": date,
    }


def fetch_postings(force: bool = False, max_pages: int = 200,
                   time_cap_s: float = 600.0, search_text: str = "") -> list[dict]:
    """Paginate the SG board search → cache roles-only posting rows.

    Cache file IS the checkpoint: if it exists and not ``force``, return load_postings().
    Network-graceful: every page is wrapped; a bad page logs + breaks to a partial land.
    Bounded by ``max_pages`` and ``time_cap_s`` with a flushing heartbeat.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_postings()

    out: list[dict] = []
    t0 = time.time()
    page = 0
    while page < max_pages:
        if time.time() - t0 > time_cap_s:
            log.warning("mycareersfuture: time cap %ss hit at page %d — landing partial",
                        time_cap_s, page)
            break
        url = SEARCH_URL.format(limit=PAGE_LIMIT, page=page)
        # Empty search returns all open postings, newest first. sessionId omitted (optional).
        body = {"search": search_text, "sessionId": ""}
        try:
            payload = _post(url, body)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            log.warning("mycareersfuture: page %d transport error (%s) — stop", page, e)
            break
        except Exception as e:  # noqa: BLE001 — one page must not sink the run
            log.warning("mycareersfuture: page %d failed (%s) — stop", page, e)
            break

        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            break
        for job in results:
            try:
                row = _parse_job(job)
            except Exception as e:  # noqa: BLE001 — one bad row must not sink the page
                log.debug("mycareersfuture: skip malformed job (%s)", e)
                continue
            if row:
                out.append(row)

        page += 1
        if page % HEARTBEAT_EVERY == 0:
            print(f"[mycareersfuture] page {page}: {len(out)} postings so far", flush=True)

        # Respect the total count if the API reports it — stop once exhausted.
        total = (payload.get("total") if isinstance(payload, dict) else None)
        if isinstance(total, int) and page * PAGE_LIMIT >= total:
            break

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("MyCareersFuture: %d SG postings across %d pages", len(out), page)
    return out


def build_staging() -> list[dict]:
    """Re-emit the cached postings as the clean staging file (idempotent).

    Postings are already roles-only + typed at fetch time, so this just guarantees
    the canonical file exists and returns the rows.
    """
    rows = load_postings()
    if rows:
        _staging_file().write_text(json.dumps(rows), encoding="utf-8")
    return rows


def load_postings() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache SG MyCareersFuture postings. Connector entrypoint (collect_all)."""
    rows = fetch_postings(**kw)
    with_salary = sum(1 for r in rows if r.get("salary_min") is not None
                      or r.get("salary_max") is not None)
    skill_tags = sum(len(r.get("skills") or []) for r in rows)
    return {
        "rows": len(rows),
        "countries": ["SG"] if rows else [],
        "with_salary": with_salary,
        "skill_tags": skill_tags,
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
