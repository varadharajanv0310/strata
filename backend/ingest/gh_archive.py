"""GH Archive — real technology / skill **demand over time** from public GitHub events.

GH Archive (https://data.gharchive.org/YYYY-MM-DD-H.json.gz, ~80-120MB/hr gzipped)
records every public GitHub event. The whole corpus is ~700GB/yr, so we *sample*:
a few hours from one day per month across recent years (see ``_sample_days``). That
keeps the pull time-bounded yet statistically reasonable — event *volume by language*
is highly stationary within a month, so a handful of hours per month tracks the
yearly trend well enough for a demand signal.

For every event we read the repo's primary ``language`` (carried on most events) and
weight it by event type — a ``PushEvent`` / ``PullRequestEvent`` (someone *writing*
that language) counts more than a passive ``WatchEvent`` / ``ForkEvent``. Languages
(and a few topic-ish event signals) map to our skills, and skills fan out to the
roles that list them. We land per-(scope, key, year) event counts.

Cache (per-hour gzip downloads + per-day partial JSON + the aggregate) is the
checkpoint — a killed run resumes by skipping hours/days already on disk.
"""
from __future__ import annotations

import gzip
import io
import json
import time
import urllib.request
from collections import defaultdict

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.warehouse.seed import ROLE_DEFS, SK

log = get_logger("ingest.gh_archive")

_BASE = "https://data.gharchive.org/{day}-{hour}.json.gz"

# Hours sampled per chosen day (spread across the UTC clock to dodge tz skew).
SAMPLE_HOURS = [2, 9, 16, 22]

# Event-type weights: actively producing code in a language is a stronger demand
# signal than a passive star/fork. Unknown types fall through to 0 (ignored).
EVENT_WEIGHT = {
    "PushEvent": 3,
    "PullRequestEvent": 3,
    "CreateEvent": 2,
    "ReleaseEvent": 2,
    "IssuesEvent": 1,
    "PullRequestReviewEvent": 1,
    "ForkEvent": 1,
    "WatchEvent": 1,
}

# GitHub repo `language` string -> our SK skill key. Lower-cased lookup.
LANG_SKILL = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "jupyter notebook": "Machine Learning",
    "hcl": "Terraform",          # HashiCorp Config Language ~ Terraform
    "dockerfile": "Docker",
    "shell": "Linux",
    "plpgsql": "SQL",
    "sql": "SQL",
    "tsql": "SQL",
    "plsql": "SQL",
    "vue": "JavaScript",
    "scala": "Spark",            # Spark's native language; rough but directional
    "c#": "Java",                # nearest durable backend skill we track
    "tex": "Statistics",
    "r": "Statistics",
}

# Build skill -> [role_id, ...] once, from the curated taxonomy.
def _skill_to_roles() -> dict[str, list[str]]:
    m: dict[str, list[str]] = defaultdict(list)
    for d in ROLE_DEFS:
        for sk_name, _level in d.get("sk", []):
            m[sk_name].append(d["id"])
    return m


SKILL_ROLES = _skill_to_roles()


def _staging_dir():
    d = settings.staging_dir / "gh_archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hour_cache(day: str, hour: int):
    return _staging_dir() / f"{day}-{hour}.json.gz"


def _day_partial(day: str):
    """Per-day aggregate (lang_weight + lang_events) — the resume checkpoint."""
    return _staging_dir() / f"day_{day}.json"


def _download_hour(day: str, hour: int) -> bytes | None:
    """Return gzip bytes for one hour file, caching to disk. Bounded retry then give up."""
    p = _hour_cache(day, hour)
    if p.exists() and p.stat().st_size > 1000:
        return p.read_bytes()
    url = _BASE.format(day=day, hour=hour)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "strata/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            p.write_bytes(data)
            log.info("gh_archive downloaded %s-%s (%d bytes)", day, hour, len(data))
            return data
        except Exception as e:  # noqa: BLE001 — connector must be resilient
            code = getattr(e, "code", None)
            transient = code in (429, 500, 502, 503, 504) or code is None
            if attempt == 3 or not transient:
                log.error("gh_archive %s-%s download failed (give up): %s", day, hour, e)
                return None
            wait = 4 * (2 ** attempt)
            log.warning("gh_archive %s-%s transient (%s) — backoff %ss", day, hour, code, wait)
            time.sleep(wait)
    return None


def _parse_hour(raw: bytes, lang_weight: dict[str, int], lang_events: dict[str, int]) -> int:
    """Stream-parse one gz hour, accumulate weighted language demand. Returns events read."""
    n = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gz:
            for line in gz:
                n += 1
                try:
                    ev = json.loads(line)
                except Exception:  # noqa: BLE001 — skip malformed line, keep going
                    continue
                w = EVENT_WEIGHT.get(ev.get("type"))
                if not w:
                    continue
                repo = ev.get("repo") or {}
                # `language` lives on the payload's repo in older schemas; newer
                # archives omit it, so we fall back to topic-free repo language if present.
                lang = None
                payload = ev.get("payload") or {}
                pr = payload.get("pull_request") or {}
                base = (pr.get("base") or {}).get("repo") or {}
                lang = base.get("language") or repo.get("language") or payload.get("language")
                if not lang:
                    continue
                key = str(lang).strip().lower()
                lang_weight[key] += w
                lang_events[key] += 1
    except Exception as e:  # noqa: BLE001
        log.error("gh_archive parse error: %s", e)
    return n


def _aggregate_day(day: str, throttle: float) -> dict[str, dict[str, int]] | None:
    """Aggregate the sampled hours of one day -> {lang: {weight, events}}. Resumable."""
    partial = _day_partial(day)
    if partial.exists():
        try:
            return json.loads(partial.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    lang_weight: dict[str, int] = defaultdict(int)
    lang_events: dict[str, int] = defaultdict(int)
    got_any = False
    for hour in SAMPLE_HOURS:
        raw = _download_hour(day, hour)
        if raw is None:
            continue
        got_any = True
        ne = _parse_hour(raw, lang_weight, lang_events)
        log.info("gh_archive %s-%s parsed %d events", day, hour, ne)
        time.sleep(throttle)
    if not got_any:
        return None
    result = {k: {"weight": lang_weight[k], "events": lang_events[k]} for k in lang_weight}
    partial.write_text(json.dumps(result), encoding="utf-8")
    return result


def _sample_days(years: list[int]) -> list[str]:
    """One day (the 15th) of each month across the given years — 12 days/year.

    The 15th avoids month-boundary / weekend edge effects; the future is clamped
    so an unattended run never requests not-yet-existing archive files.
    """
    import datetime as _dt
    today = _dt.date.today()
    cutoff = today - _dt.timedelta(days=2)  # archive lags ~a day; stay safely behind
    days: list[str] = []
    for y in years:
        for mo in range(1, 13):
            d = _dt.date(y, mo, 15)
            if d <= cutoff:
                days.append(d.strftime("%Y-%m-%d"))
    return days


def run(days: list[str] | None = None, years: list[int] | None = None,
        throttle: float = 1.0) -> dict:
    """Pull sampled GH Archive hours, aggregate language demand → role/skill events/year.

    Args:
        days:  explicit ``YYYY-MM-DD`` days to sample (overrides ``years``).
        years: sample the 15th of every month of these years. Default 2020-2025.
        throttle: seconds between hour downloads.

    Lands ``staging/gh_archive/demand.json`` :=
        ``[{scope:'skill'|'role', key, year, events}]`` and returns a summary.
    """
    if days is None:
        years = years or [2020, 2021, 2022, 2023, 2024, 2025]
        days = _sample_days(years)
    if not days:
        log.warning("gh_archive: no sample days resolved")
        return {"days": 0, "rows": 0}

    # year -> lang -> {weight, events}
    by_year: dict[int, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"weight": 0, "events": 0}))
    done_days = failed_days = 0
    for day in days:
        year = int(day[:4])
        agg = _aggregate_day(day, throttle)
        if agg is None:
            failed_days += 1
            log.warning("gh_archive: day %s yielded nothing", day)
            continue
        done_days += 1
        for lang, v in agg.items():
            slot = by_year[year][lang]
            slot["weight"] += v.get("weight", 0)
            slot["events"] += v.get("events", 0)

    # Fan languages out to skills, then skills to roles. We carry the weighted
    # event count as the demand magnitude (`events` field of each record).
    records: list[dict] = []
    skill_year: dict[tuple[str, int], int] = defaultdict(int)
    role_year: dict[tuple[str, int], int] = defaultdict(int)
    for year, langs in by_year.items():
        for lang, v in langs.items():
            skill = LANG_SKILL.get(lang)
            if not skill:
                continue
            w = v["weight"]
            skill_year[(skill, year)] += w
    for (skill, year), w in skill_year.items():
        records.append({"scope": "skill", "key": skill, "year": year, "events": w})
        for rid in SKILL_ROLES.get(skill, []):
            role_year[(rid, year)] += w
    for (rid, year), w in role_year.items():
        records.append({"scope": "role", "key": rid, "year": year, "events": w})

    out = _staging_dir() / "demand.json"
    out.write_text(json.dumps(records), encoding="utf-8")

    # top skills overall (sum across years) for the smoke report
    top: dict[str, int] = defaultdict(int)
    for r in records:
        if r["scope"] == "skill":
            top[r["key"]] += r["events"]
    top_skills = sorted(top.items(), key=lambda kv: kv[1], reverse=True)[:10]
    summary = {
        "days_requested": len(days), "days_done": done_days, "days_failed": failed_days,
        "years": sorted(by_year),
        "skill_rows": sum(1 for r in records if r["scope"] == "skill"),
        "role_rows": sum(1 for r in records if r["scope"] == "role"),
        "rows": len(records),
        "top_skills": top_skills,
    }
    log.info("gh_archive aggregated: %s", summary)
    return summary


def load_demand() -> list:
    p = _staging_dir() / "demand.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
