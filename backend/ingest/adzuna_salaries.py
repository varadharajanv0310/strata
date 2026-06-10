"""Real per-role × per-country salary + demand from the **Adzuna jobs API**.

One `/search` call per (country, role) returns everything the headline numbers
need: ``count`` (postings → demand), ``mean`` (advertised salary, in the country's
native currency) and per-result ``salary_is_predicted`` flags (→ a real pay
*transparency* rate). Results are cached one file per unit under
``staging/adzuna`` — that cache **is** the checkpoint: a killed/rebooted run
resumes by skipping units already on disk (brief §10, never restart from zero).

The curated role catalogue (names, skills, ladders) stays the product taxonomy;
Adzuna supplies the live numbers we overlay onto it in ``warehouse.real_build``.
"""
from __future__ import annotations

import json
import time

import requests

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.seed import COUNTRIES, ROLE_DEFS

log = get_logger("ingest.adzuna_salaries")

# our 7 markets → Adzuna country endpoints (all supported, native currency each)
ADZUNA_CC = {"IN": "in", "US": "us", "GB": "gb", "CA": "ca", "AU": "au", "SG": "sg", "DE": "de"}
BASE = "https://api.adzuna.com/v1/api/jobs"
RESULTS_PER_PAGE = 50  # max page size — also our transparency sample


def _staging_dir():
    d = settings.staging_dir / "adzuna"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clean_what(name: str) -> str:
    # "QA / Test Engineer" → "QA Test Engineer"; keep it a plain keyword query
    return " ".join(name.replace("/", " ").split())


def fetch_all(throttle: float = 2.5, max_units: int | None = None) -> dict:
    """Pull salary+demand for every (country, role); cache + resume. Returns a summary."""
    if not (settings.adzuna_app_id and settings.adzuna_app_key):
        raise RuntimeError("Adzuna credentials missing — set ADZUNA_APP_ID / ADZUNA_APP_KEY in .env")
    auth = {"app_id": settings.adzuna_app_id, "app_key": settings.adzuna_app_key,
            "content-type": "application/json"}
    sd = _staging_dir()
    fetched = cached = failed = 0
    units = [(co, d) for co in COUNTRIES for d in ROLE_DEFS]
    for co, d in units:
        out = sd / f"{co['code']}_{d['id']}.json"
        if out.exists():
            cached += 1
            continue
        if max_units is not None and fetched >= max_units:
            break
        cc = ADZUNA_CC[co["code"]]
        params = {**auth, "what": _clean_what(d["name"]), "results_per_page": RESULTS_PER_PAGE}
        rec = None
        for attempt in range(5):
            try:
                r = requests.get(f"{BASE}/{cc}/search/1", params=params, timeout=40)
                if r.status_code == 429:
                    wait = 5 * (2 ** attempt)
                    log.warning("429 rate-limited on %s:%s — backoff %ss", co["code"], d["id"], wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                j = r.json()
                results = j.get("results") or []
                n_disclosed = sum(1 for x in results if str(x.get("salary_is_predicted")) == "0")
                rec = {
                    "country": co["code"], "role_id": d["id"], "what": _clean_what(d["name"]),
                    "count": j.get("count"), "mean": j.get("mean"),
                    "n_results": len(results), "n_disclosed": n_disclosed,
                    "currency": co["curCode"],
                }
                break
            except Exception as e:  # noqa: BLE001 — connector must be resilient
                if attempt == 4:
                    log.error("fetch failed %s:%s — %s", co["code"], d["id"], e)
                else:
                    time.sleep(2 ** attempt)
        if rec is None:
            failed += 1
            continue
        out.write_text(json.dumps(rec), encoding="utf-8")
        fetched += 1
        log.info("adzuna %s · %-26s count=%-7s mean=%s",
                 co["code"], d["name"], rec["count"], rec["mean"])
        time.sleep(throttle)
    summary = {"fetched": fetched, "cached": cached, "failed": failed, "total_units": len(units)}
    log.info("adzuna fetch summary: %s", summary)
    return summary


def load_cache() -> dict:
    """Return {(country_code, role_id): record} from the staging cache."""
    sd = _staging_dir()
    out: dict = {}
    for f in sd.glob("*.json"):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            out[(r["country"], r["role_id"])] = r
        except Exception:  # noqa: BLE001
            continue
    return out
