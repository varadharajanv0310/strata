"""Wikipedia pageviews — the attention **NORMALIZER**: separate hype from adoption.

Job-posting counts and salary trends tell you what employers are *paying for*; they do
not tell you how much general *attention* a technology is getting independent of hiring.
A skill can be hyped (huge Wikipedia readership, blog churn, conference buzz) while
actual job demand lags — or the reverse, a quietly-load-bearing tech with steady jobs
and little public chatter. This connector lands the public-attention baseline so the
warehouse can normalize raw demand against it: monthly English-Wikipedia pageviews for a
CURATED set of ~80 technologies, which divides hype out of adoption.

Signal grain: skill(tech) × period(YYYY-MM) × views. GLOBAL — Wikipedia pageviews are
not resolvable to our 7 countries (the per-article endpoint is not split by geography),
and we deliberately use **en.wikipedia as a single global proxy** rather than fake a
country split, so every row carries country='' (global). It feeds
``fact_skill_adoption`` as the attention denominator beside the posting-derived demand.

Obtained from the public Wikimedia REST API (no key, polite UA required):
``GET /metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/{ARTICLE}/monthly/{start}/{end}``
The article titles are a hand-curated tech→article map; Wikimedia's pageviews API is a
first-class, documented, stable public endpoint (legitimate, ToS-friendly with a real
User-Agent + polite pacing). ROLES-ONLY: this is a skill/tech attention signal with no
employer data anywhere. Credential-free; network-graceful (one article failing logs +
skips, never sinks the run); flushing heartbeat + time/article caps for the long fetch.
**Not run in this pass** — real runnable code coded for the later run.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.wikipedia_pageviews")

# Wikimedia REST pageviews — per-article, monthly granularity, English Wikipedia.
BASE = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "en.wikipedia/all-access/all-agents/{article}/monthly/{start}/{end}")
# Wikimedia requires a descriptive User-Agent identifying the client + contact intent.
HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}
# Pageviews API data begins 2015-07; we ask from 2016-01 for clean whole years.
DEFAULT_START = "2016-01"

# CURATED tech → exact Wikipedia article title. Title is the canonical page; the API
# percent-encodes spaces/parens for us. Keys are the strata skill name we land.
TECH_ARTICLES: dict[str, str] = {
    # languages
    "Python": "Python (programming language)",
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "Java": "Java (programming language)",
    "C#": "C Sharp (programming language)",
    "C++": "C++",
    "Go": "Go (programming language)",
    "Rust": "Rust (programming language)",
    "Ruby": "Ruby (programming language)",
    "PHP": "PHP",
    "Swift": "Swift (programming language)",
    "Kotlin": "Kotlin (programming language)",
    "Scala": "Scala (programming language)",
    "R": "R (programming language)",
    "Dart": "Dart (programming language)",
    "Elixir": "Elixir (programming language)",
    "Perl": "Perl",
    "Haskell": "Haskell",
    "Julia": "Julia (programming language)",
    "Lua": "Lua (programming language)",
    "Clojure": "Clojure",
    "Zig": "Zig (programming language)",
    "SQL": "SQL",
    # frontend / UI
    "React": "React (software)",
    "Angular": "Angular (web framework)",
    "Vue.js": "Vue.js",
    "Svelte": "Svelte",
    "Next.js": "Next.js",
    "jQuery": "JQuery",
    "Tailwind CSS": "Tailwind CSS",
    "Bootstrap": "Bootstrap (front-end framework)",
    # backend / frameworks
    "Node.js": "Node.js",
    "Django": "Django (web framework)",
    "Flask": "Flask (web framework)",
    "Spring Framework": "Spring Framework",
    "Ruby on Rails": "Ruby on Rails",
    "Laravel": "Laravel",
    "ASP.NET Core": "ASP.NET Core",
    "FastAPI": "FastAPI",
    "Express.js": "Express.js",
    ".NET": ".NET",
    # data / ML
    "TensorFlow": "TensorFlow",
    "PyTorch": "PyTorch",
    "Keras": "Keras",
    "scikit-learn": "Scikit-learn",
    "Pandas": "Pandas (software)",
    "NumPy": "NumPy",
    "Apache Spark": "Apache Spark",
    "Apache Kafka": "Apache Kafka",
    "Apache Hadoop": "Apache Hadoop",
    "Apache Airflow": "Apache Airflow",
    "dbt": "Dbt (data build tool)",
    "Databricks": "Databricks",
    "Snowflake": "Snowflake Inc.",
    # databases
    "PostgreSQL": "PostgreSQL",
    "MySQL": "MySQL",
    "MongoDB": "MongoDB",
    "Redis": "Redis",
    "SQLite": "SQLite",
    "Elasticsearch": "Elasticsearch",
    "Cassandra": "Apache Cassandra",
    "MariaDB": "MariaDB",
    "Neo4j": "Neo4j",
    "DuckDB": "DuckDB",
    # cloud / infra / devops
    "Kubernetes": "Kubernetes",
    "Docker": "Docker (software)",
    "Terraform": "Terraform (software)",
    "Ansible": "Ansible (software)",
    "Jenkins": "Jenkins (software)",
    "Prometheus": "Prometheus (software)",
    "Grafana": "Grafana",
    "Amazon Web Services": "Amazon Web Services",
    "Microsoft Azure": "Microsoft Azure",
    "Google Cloud Platform": "Google Cloud Platform",
    "NGINX": "Nginx",
    "Helm": "Helm (package manager)",
    "Apache HTTP Server": "Apache HTTP Server",
    # AI / LLM era
    "GraphQL": "GraphQL",
    "WebAssembly": "WebAssembly",
    "Large language model": "Large language model",
    "Generative artificial intelligence": "Generative artificial intelligence",
    "Hugging Face": "Hugging Face",
    "LangChain": "LangChain",
    "OpenAI": "OpenAI",
    # other tooling
    "Git": "Git",
    "Linux": "Linux",
}


def _staging_dir():
    d = settings.staging_dir / "wikipedia"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "pageviews.json"


def _end_period() -> str:
    """Last fully-elapsed month as YYYY-MM (current month is incomplete)."""
    t = date.today()
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return f"{y:04d}-{m:02d}"


def _api_dates(period: str) -> str:
    """'YYYY-MM' → the API's required YYYYMMDD00 boundary token."""
    y, m = period.split("-")
    return f"{y}{m}0100"


def _fetch_article(skill: str, article: str, start: str, end: str,
                   timeout: int = 60) -> list[dict]:
    """Fetch one technology's monthly pageviews series → typed rows. Best-effort.

    Lands rows keyed on the strata skill name + period (YYYY-MM) + views, country=''
    (global / en proxy). Raises on transport error so the caller can log + skip.
    """
    encoded = urllib.parse.quote(article.replace(" ", "_"), safe="")
    url = BASE.format(article=encoded, start=_api_dates(start), end=_api_dates(end))
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8", errors="replace"))
    rows: list[dict] = []
    for item in payload.get("items", []):
        ts = item.get("timestamp") or ""          # e.g. '2016010100'
        if len(ts) < 6:
            continue
        period = f"{ts[0:4]}-{ts[4:6]}"
        views = item.get("views")
        try:
            views = int(views)
        except (TypeError, ValueError):
            continue
        rows.append({
            "skill": skill,
            "period": period,
            "views": views,
            "country": "",                        # global — en.wikipedia proxy
        })
    return rows


def fetch_pageviews(force: bool = False, start: str = DEFAULT_START,
                    end: str | None = None, max_units: int | None = None,
                    time_cap_s: float = 900.0, pace_s: float = 0.2) -> list[dict]:
    """Fetch + cache monthly pageviews for the curated tech map. Cache is the checkpoint.

    One article failing (404 for a renamed page, transient 5xx) logs + skips rather than
    sinking the run. Flushing heartbeat per article; honors ``max_units`` (cap article
    count) and ``time_cap_s`` (wall-clock budget) for the long multi-call fetch.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_pageviews()

    end = end or _end_period()
    items = list(TECH_ARTICLES.items())
    if max_units is not None:
        items = items[:max_units]

    out: list[dict] = []
    t0 = time.time()
    done = 0
    for skill, article in items:
        if time.time() - t0 > time_cap_s:
            log.warning("wikipedia_pageviews: time cap %ss hit — landing partial",
                        time_cap_s)
            break
        try:
            rows = _fetch_article(skill, article, start, end)
            out.extend(rows)
            done += 1
            print(f"[wikipedia] {skill}: {len(rows)} months "
                  f"({done}/{len(items)})", flush=True)
        except urllib.error.HTTPError as e:
            # 404 commonly means the article title needs fixing in TECH_ARTICLES.
            log.warning("wikipedia_pageviews: %s (%s) HTTP %s — skip",
                        skill, article, e.code)
        except Exception as e:  # noqa: BLE001 — one article must not sink the run
            log.warning("wikipedia_pageviews: %s (%s) fetch failed (%s) — skip",
                        skill, article, e)
        time.sleep(pace_s)                        # polite pacing for Wikimedia

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("Wikipedia pageviews: %d rows across %d technologies",
             len(out), len({r["skill"] for r in out}))
    return out


def load_pageviews() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache Wikipedia pageviews (attention normalizer). Connector entrypoint."""
    rows = fetch_pageviews(**kw)
    return {
        "rows": len(rows),
        "skills": sorted({r["skill"] for r in rows}),
        "periods": len({r["period"] for r in rows}),
        "country": "",                            # global signal
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
