"""Real **structured remote postings** from the RemoteOK public API
(https://remoteok.com/api).

NEW SIGNAL: live remote-job postings that carry BOTH explicit salary bounds
(``salary_min`` / ``salary_max``, USD-denominated) AND a free tag list (skills).
Most job-board feeds give us one or the other; RemoteOK gives demand + comp +
skills in a single row, which is exactly the pair strata needs to populate the
remote stratum of two facts at once.

GRAIN: one row per posting. There is NO real geography — RemoteOK is a
remote-only board with a global candidate pool, so we land ``country=''``
(global) and never fabricate geo. The skill/role signal is global; salary is
USD. The role/skill crosswalk (tags -> dim_skill, title -> dim_role) is done
later by the warehouse fuse — here we just land the source's native title + tag
strings + the salary numbers + the date.

HOW OBTAINED: one unauthenticated GET of https://remoteok.com/api returns a
single JSON array. The FIRST element is a legal/metadata object (RemoteOK's
attribution + terms notice) and is skipped; every remaining element is a
posting. No credentials; RemoteOK only asks for a descriptive User-Agent.
LEGITIMACY: public, documented API; the legal element explicitly grants reuse
with attribution. We respect it by sending an honest UA and skipping element 0.

SNAPSHOT, NOT HISTORY: the endpoint returns only the *current* set of live
postings (roughly the most recent few hundred). It is NOT a historical archive —
each fetch overwrites the snapshot. Treat the staging file as "remote postings
as of last fetch", and let downstream layers stamp the ingest date if they need
a time series.

ROLES-ONLY: each RemoteOK posting includes a ``company`` (and ``company_logo``,
``apply_url``) — these are DROPPED. strata never lands employer/org as a product
field; we keep only title, skills, salary, date.

WAREHOUSE DESTINATION: feeds ``fact_demand`` (a posting = a unit of demand for
the title/skills, remote stratum) and ``fact_salary_job`` (the explicit
salary_min/salary_max, remote stratum).
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.remoteok")

API_URL = "https://remoteok.com/api"
# RemoteOK blocks generic/empty agents; an honest descriptive UA is what they ask for.
USER_AGENT = "strata-job-market/1.0 (research; remote-postings ingest; +https://remoteok.com)"


def _staging_dir():
    d = settings.staging_dir / "remoteok"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "postings.json"


def _to_int(val: Any) -> int | None:
    """RemoteOK salary fields arrive as int, float, numeric-string, or junk — coerce
    safely, returning None for anything non-numeric or non-positive."""
    if val is None:
        return None
    try:
        n = int(float(str(val).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _skills(row: dict) -> list[str]:
    """Tags are the skill signal. RemoteOK puts them in ``tags`` (list); fall back to
    ``position``-adjacent fields only if tags is missing. De-dupe, strip, drop blanks."""
    raw = row.get("tags")
    if not isinstance(raw, list):
        raw = []
    seen: dict[str, None] = {}
    for t in raw:
        s = str(t).strip()
        if s:
            seen.setdefault(s, None)
    return list(seen.keys())


def fetch_postings(force: bool = False, timeout_s: int = 40) -> list[dict]:
    """Fetch + cache the current RemoteOK snapshot.

    The cached ``postings.json`` IS the checkpoint: if it exists and ``force`` is
    False, we just reload it. NETWORK-GRACEFUL: any HTTP/parse failure logs a clear
    warning and returns [] (or the prior cache) rather than crashing the run.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_postings()

    req = urllib.request.Request(API_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 — one source must never sink the run
        log.warning("RemoteOK fetch failed (%s: %s); keeping any prior cache",
                    type(exc).__name__, exc)
        return load_postings()

    if not isinstance(payload, list) or not payload:
        log.warning("RemoteOK returned no array / empty payload; nothing to land")
        return []

    # Element 0 is the legal/metadata object — skip it.
    raw_rows = payload[1:]

    out: list[dict] = []
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        title = (row.get("position") or row.get("title") or "").strip()
        if not title:
            continue
        smin = _to_int(row.get("salary_min"))
        smax = _to_int(row.get("salary_max"))
        # Land the posting even without salary — it's still a demand signal; salary
        # consumers (fact_salary_job) simply ignore rows where both bounds are None.
        out.append({
            "title": title,                       # role signal (native source title)
            "skills": _skills(row),               # skill signal (tags)
            "salary_min": smin,                   # USD, may be None
            "salary_max": smax,                   # USD, may be None
            "date": (row.get("date") or "").strip(),  # ISO8601 posting time
            "country": "",                        # GLOBAL — remote-only board, no real geo
            "remote": True,
        })
        # NOTE: company / company_logo / apply_url / id / url are intentionally DROPPED
        # (roles-only — never land employer as a product field).

    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    with_sal = sum(1 for p in out if p["salary_min"] or p["salary_max"])
    log.info("RemoteOK snapshot landed: %d postings (%d with salary)", len(out), with_sal)
    return out


def load_postings() -> list[dict]:
    """Read the staging snapshot back. Returns [] if absent/corrupt."""
    f = _staging_file()
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        log.warning("RemoteOK staging unreadable (%s); treating as empty", exc)
        return []
    return data if isinstance(data, list) else []


def run(force: bool = False, **kw) -> dict:
    """Orchestrate: fetch (or reuse cache) and summarize. collect_all calls this."""
    rows = fetch_postings(force=force)
    with_salary = sum(1 for p in rows if p.get("salary_min") or p.get("salary_max"))
    distinct_skills = len({s for p in rows for s in p.get("skills", [])})
    return {
        "source": "remoteok",
        "rows": len(rows),
        "with_salary": with_salary,
        "distinct_skills": distinct_skills,
        "country": "",  # global
        "remote": True,
        "snapshot": True,
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run(), indent=2))
