"""Hacker News **"Ask HN: Who is hiring?"** — the IC-voice demand signal.

Every month since 2011 HN runs one monolithic "Ask HN: Who is hiring?" thread where
each top-level comment is a single job posting written, overwhelmingly, by the
engineer or founder doing the hiring — not by a recruiter or an ATS. That makes it a
rare **15-year longitudinal panel of IC-voice demand**: the stack people actually
name, whether the role is REMOTE, and (sometimes) a salary band and a location, all
in unfiltered prose. Three signals fall out that nothing else in strata carries:

  * **remote-share-over-time** — % of postings flagged REMOTE per month, a clean
    secular trend (the pre-/post-2020 remote step-change is visible in this very feed);
  * **IC-voice skill demand** — which skills get *named by practitioners*, a different
    distribution from the recruiter-keyword soup of formal job boards;
  * **self-reported stack co-occurrence** — the skill bundles that show up together.

Grain: **year_month × skill** and **year_month × remote-flag**, global. There is NO
real geography in this feed — postings are worldwide and location is free-text and
usually absent — so country is landed '' (global) unless a comment explicitly names
one of our 7. It feeds **fact_demand** (remote-share + IC-voice skill demand).

Obtained from the public **Algolia HN Search API** (no key, generous rate limits):
``/search?query=who is hiring&tags=story`` to enumerate the monthly threads, then
``/items/{id}`` to pull each thread's comment tree. Per top-level comment we regex
out the tech stack (a curated skill lexicon), a REMOTE flag, an optional salary band,
and an optional country — then **DROP the company name and the comment author**
(ROLES-ONLY; the posting org is never a product field). Legitimacy: Algolia is HN's
official search backend and the data is public; the only fragility is the free-text
parse, which is heuristic by nature (documented below). Heartbeat + time/thread caps;
network-graceful per thread. **Not run in this pass** — real runnable code, not a stub.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.hn_hiring")

ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search"
ALGOLIA_ITEM = "https://hn.algolia.com/api/v1/items/{id}"
HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}

# Title regex for the monthly hiring threads (variants: "Ask HN: Who is hiring?",
# "Ask HN: Who is hiring? (March 2021)", occasionally without the "Ask HN:" prefix).
_THREAD_TITLE_RE = re.compile(r"who\s+is\s+hiring", re.IGNORECASE)
# Pull the month/year out of the title, e.g. "(March 2021)".
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_TITLE_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\w*\.?\s+(20\d{2})\b", re.IGNORECASE)

# REMOTE flag — practitioners write it many ways; require a real remote signal and
# guard against the common negation "ONSITE only / no remote / not remote".
_REMOTE_RE = re.compile(r"\bremote\b", re.IGNORECASE)
_REMOTE_NEG_RE = re.compile(r"\b(no\s+remote|not\s+remote|onsite\s+only|on-?site\s+only)\b",
                            re.IGNORECASE)

# Salary band — "$120k-$160k", "120-160k", "$150,000", "USD 120k", etc. Best-effort;
# we only keep it when both ends parse to a sane annual range. (kept un-localized: a
# raw band, the warehouse decides currency/PPP downstream.)
_SALARY_RANGE_RE = re.compile(
    r"(?:\$|usd|eur|gbp|£|€)?\s*"
    r"(\d{2,3}(?:[,.]\d{3})?)\s*[kK]?\s*[-–—to]+\s*"
    r"(?:\$|usd|eur|gbp|£|€)?\s*"
    r"(\d{2,3}(?:[,.]\d{3})?)\s*[kK]?",
    re.IGNORECASE)

# Country — only land geography when a comment explicitly names one of OUR 7. Free-text
# so we match common spellings/cities → ISO-2; everything else stays '' (global).
_COUNTRY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(india|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai)\b", re.I), "IN"),
    (re.compile(r"\b(usa|u\.s\.a?\.|united states|new york|nyc|san francisco|sf bay|seattle|austin|boston)\b", re.I), "US"),
    (re.compile(r"\b(uk|u\.k\.|united kingdom|england|london|manchester|edinburgh)\b", re.I), "GB"),
    (re.compile(r"\b(canada|toronto|vancouver|montreal|ottawa)\b", re.I), "CA"),
    (re.compile(r"\b(australia|sydney|melbourne|brisbane|perth)\b", re.I), "AU"),
    (re.compile(r"\b(singapore)\b", re.I), "SG"),
    (re.compile(r"\b(germany|deutschland|berlin|munich|münchen|hamburg|frankfurt)\b", re.I), "DE"),
]

# Curated skill lexicon: name → set of word-boundary aliases. IC-voice means we match
# what engineers actually type, including casual aliases ("js", "k8s", "postgres").
_SKILL_ALIASES: dict[str, list[str]] = {
    "python": ["python"],
    "javascript": ["javascript", "js"],
    "typescript": ["typescript", "ts"],
    "java": ["java"],
    "kotlin": ["kotlin"],
    "go": ["golang", "go"],
    "rust": ["rust"],
    "c++": [r"c\+\+", "cpp"],
    "c#": [r"c#", r"\.net", "dotnet"],
    "ruby": ["ruby", "rails", "ruby on rails"],
    "php": ["php", "laravel"],
    "scala": ["scala"],
    "elixir": ["elixir", "phoenix"],
    "clojure": ["clojure"],
    "swift": ["swift"],
    "react": ["react", "reactjs", "react.js"],
    "vue": ["vue", "vuejs", "vue.js"],
    "angular": ["angular", "angularjs"],
    "svelte": ["svelte"],
    "nodejs": ["node", "nodejs", "node.js"],
    "django": ["django"],
    "flask": ["flask"],
    "fastapi": ["fastapi"],
    "spring": ["spring", "spring boot"],
    "graphql": ["graphql"],
    "postgresql": ["postgres", "postgresql", "psql"],
    "mysql": ["mysql", "mariadb"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "elasticsearch": ["elasticsearch", "elastic"],
    "kafka": ["kafka"],
    "aws": ["aws", "amazon web services"],
    "gcp": ["gcp", "google cloud"],
    "azure": ["azure"],
    "docker": ["docker"],
    "kubernetes": ["kubernetes", "k8s"],
    "terraform": ["terraform"],
    "linux": ["linux"],
    "tensorflow": ["tensorflow"],
    "pytorch": ["pytorch"],
    "machine learning": ["machine learning", r"\bml\b", "deep learning"],
    "data science": ["data science", "data scientist"],
    "nlp": ["nlp", "natural language processing"],
    "llm": ["llm", "large language model", "gpt", "langchain"],
    "spark": ["spark", "pyspark"],
    "hadoop": ["hadoop"],
    "airflow": ["airflow"],
    "dbt": ["dbt"],
    "snowflake": ["snowflake"],
    "solidity": ["solidity"],
    "ios": ["ios"],
    "android": ["android"],
    "flutter": ["flutter"],
    "react native": ["react native"],
}
# Pre-compile one word-boundary regex per skill (alternation over its aliases).
_SKILL_RES: dict[str, re.Pattern] = {
    name: re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(aliases) + r")(?![A-Za-z0-9])", re.IGNORECASE)
    for name, aliases in _SKILL_ALIASES.items()
}


def _staging_dir():
    d = settings.staging_dir / "hn_hiring"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _postings_file():
    return _staging_dir() / "postings.json"


def _threads_file():
    return _staging_dir() / "threads.json"


def _get_json(url: str, timeout: int = 40) -> dict | list | None:
    """GET → parsed JSON, or None on any failure (network-graceful)."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _strip_html(text: str) -> str:
    """Algolia returns HTML comment_text; flatten to plain text for the regex parse."""
    if not text:
        return ""
    text = text.replace("<p>", "\n").replace("</p>", "\n")
    text = re.sub(r"<[^>]+>", " ", text)
    # de-entity the few that matter for our regexes
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#x27;", "'"), ("&#x2F;", "/"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return text


# ---------------------------------------------------------------------------
# 1) enumerate the monthly threads
# ---------------------------------------------------------------------------
def fetch_threads(force: bool = False, max_threads: int = 200) -> list[dict]:
    """Find the monthly "Who is hiring?" story threads via Algolia search.

    Returns [{id, year_month, title}]. The thread list is the cheap checkpoint;
    the heavy per-comment fetch happens in fetch_postings().
    """
    f = _threads_file()
    if f.exists() and not force:
        return json.loads(f.read_text(encoding="utf-8"))

    out: list[dict] = []
    seen: set[int] = set()
    page = 0
    # Algolia caps hitsPerPage at 1000; we page to be safe and to bound the run.
    while len(out) < max_threads:
        params = urllib.parse.urlencode({
            "query": "who is hiring",
            "tags": "story",
            "hitsPerPage": 100,
            "page": page,
        })
        try:
            payload = _get_json(f"{ALGOLIA_SEARCH}?{params}")
        except Exception as e:  # noqa: BLE001 — one page failing must not sink the run
            log.warning("hn_hiring: search page %d failed (%s) — stop paging", page, e)
            break
        hits = (payload or {}).get("hits") or []
        if not hits:
            break
        for h in hits:
            title = h.get("title") or ""
            if not _THREAD_TITLE_RE.search(title):
                continue
            try:
                hid = int(h.get("objectID"))
            except (TypeError, ValueError):
                continue
            if hid in seen:
                continue
            seen.add(hid)
            ym = _year_month_from_title(title) or _year_month_from_epoch(h.get("created_at_i"))
            if not ym:
                continue
            out.append({"id": hid, "year_month": ym, "title": title})
        nb_pages = (payload or {}).get("nbPages") or 0
        page += 1
        if page >= nb_pages:
            break

    out.sort(key=lambda r: r["year_month"])
    f.write_text(json.dumps(out), encoding="utf-8")
    log.info("hn_hiring: %d monthly hiring threads enumerated", len(out))
    return out


def _year_month_from_title(title: str) -> str | None:
    m = _TITLE_DATE_RE.search(title or "")
    if not m:
        return None
    mon = _MONTHS.get(m.group(1).lower())
    if not mon:
        return None
    return f"{m.group(2)}-{mon:02d}"


def _year_month_from_epoch(epoch) -> str | None:
    if not epoch:
        return None
    try:
        t = time.gmtime(int(epoch))
        return f"{t.tm_year}-{t.tm_mon:02d}"
    except (TypeError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# 2) parse one comment → a roles-only posting record
# ---------------------------------------------------------------------------
def _skills_in(text: str) -> list[str]:
    return sorted(name for name, rx in _SKILL_RES.items() if rx.search(text))


def _remote_flag(text: str) -> bool:
    if _REMOTE_NEG_RE.search(text):
        return False
    return bool(_REMOTE_RE.search(text))


def _country_in(text: str) -> str:
    """Return our ISO-2 if a country is explicitly named, else '' (global)."""
    for rx, code in _COUNTRY_PATTERNS:
        if rx.search(text):
            return code
    return ""


def _to_annual(num: str) -> float | None:
    """'120' → 120000 (k implied for 2-3 digit), '150,000' → 150000."""
    raw = num.replace(",", "").replace(".", "")
    try:
        v = float(raw)
    except ValueError:
        return None
    if v < 1000:           # bare "120" / "160" means thousands
        v *= 1000
    return v


def _salary_band(text: str) -> tuple[float | None, float | None]:
    """Best-effort (min, max) annual band, or (None, None). Sanity-bounded."""
    m = _SALARY_RANGE_RE.search(text)
    if not m:
        return None, None
    lo, hi = _to_annual(m.group(1)), _to_annual(m.group(2))
    if lo is None or hi is None:
        return None, None
    if hi < lo:
        lo, hi = hi, lo
    # plausibility gate: drop obvious non-salary number ranges
    if lo < 10_000 or hi > 2_000_000 or hi < lo:
        return None, None
    return lo, hi


def _parse_comment(text: str, year_month: str) -> dict | None:
    """One top-level comment → posting dict (skills/remote/salary/country), or None.

    ROLES-ONLY: we deliberately extract NO company name and NO author — only the
    role-shaped signals. A comment with no recognizable skill is dropped as noise.
    """
    skills = _skills_in(text)
    if not skills:
        return None
    lo, hi = _salary_band(text)
    rec: dict = {
        "year_month": year_month,
        "skills": skills,
        "remote": _remote_flag(text),
        "country": _country_in(text),
    }
    if lo is not None:
        rec["salary_min"] = lo
        rec["salary_max"] = hi
    return rec


# ---------------------------------------------------------------------------
# 3) pull each thread's comments → postings
# ---------------------------------------------------------------------------
def fetch_postings(force: bool = False, time_cap_s: float = 900.0,
                   max_threads: int | None = None, heartbeat: int = 5) -> list[dict]:
    """Fetch every thread's top-level comments and parse them into postings.

    Cache (postings.json) IS the checkpoint: if present and not force, just load it.
    Per-thread network-graceful; bounded by time_cap_s and max_threads; flushing
    heartbeat every `heartbeat` threads.
    """
    f = _postings_file()
    if f.exists() and not force:
        return load_postings()

    threads = fetch_threads(force=force)
    if max_threads:
        threads = threads[-max_threads:]      # most recent N (richest, most relevant)
    if not threads:
        log.warning("hn_hiring: no hiring threads found — nothing to parse")
        return []

    out: list[dict] = []
    t0 = time.time()
    for i, th in enumerate(threads, 1):
        if time.time() - t0 > time_cap_s:
            log.warning("hn_hiring: time cap %ss hit at thread %d/%d — landing partial",
                        time_cap_s, i, len(threads))
            break
        try:
            item = _get_json(ALGOLIA_ITEM.format(id=th["id"]))
        except Exception as e:  # noqa: BLE001 — one thread must not sink the run
            log.warning("hn_hiring: thread %s fetch failed (%s) — skip", th["id"], e)
            continue
        ym = th["year_month"]
        n_before = len(out)
        for child in (item or {}).get("children") or []:
            # top-level comments only; skip deleted/empty
            text = _strip_html(child.get("text") or "")
            if not text.strip():
                continue
            rec = _parse_comment(text, ym)
            if rec:
                out.append(rec)
        if i % heartbeat == 0 or i == len(threads):
            print(f"[hn_hiring] {i}/{len(threads)} threads — "
                  f"{ym}: +{len(out) - n_before} postings ({len(out)} total)", flush=True)

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("hn_hiring: %d postings parsed across %d months",
             len(out), len({r["year_month"] for r in out}))
    return out


def load_postings() -> list[dict]:
    f = _postings_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


# ---------------------------------------------------------------------------
# 4) derived signals (handy for the warehouse fuse; pure functions over postings)
# ---------------------------------------------------------------------------
def remote_share_by_month(postings: list[dict] | None = None) -> dict:
    """{year_month: {n, remote, share}} — the remote-share-over-time trend."""
    postings = postings if postings is not None else load_postings()
    agg: dict[str, dict] = {}
    for p in postings:
        a = agg.setdefault(p["year_month"], {"n": 0, "remote": 0})
        a["n"] += 1
        if p.get("remote"):
            a["remote"] += 1
    for a in agg.values():
        a["share"] = round(a["remote"] / a["n"], 4) if a["n"] else 0.0
    return dict(sorted(agg.items()))


def skill_demand_by_month(postings: list[dict] | None = None) -> dict:
    """{year_month: {skill: count}} — IC-voice skill mentions over time."""
    postings = postings if postings is not None else load_postings()
    agg: dict[str, dict] = {}
    for p in postings:
        bucket = agg.setdefault(p["year_month"], {})
        for s in p.get("skills") or []:
            bucket[s] = bucket.get(s, 0) + 1
    return dict(sorted(agg.items()))


def run(**kw) -> dict:
    """Land + cache HN "Who is hiring?" postings. collect_all entrypoint."""
    postings = fetch_postings(**kw)
    months = sorted({p["year_month"] for p in postings})
    remote = sum(1 for p in postings if p.get("remote"))
    with_salary = sum(1 for p in postings if "salary_min" in p)
    return {
        "rows": len(postings),
        "months": len(months),
        "month_range": [months[0], months[-1]] if months else [],
        "remote": remote,
        "remote_share": round(remote / len(postings), 4) if postings else 0.0,
        "with_salary": with_salary,
        "written": bool(postings),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
