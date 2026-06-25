"""Package-registry download volume — **realized technology ADOPTION** across
language ecosystems, the demand-side counterpart to job postings and search interest.

A job posting says an employer *wants* a skill; a package download says a developer
*actually pulled the tool into a build*. This module lands monthly download counts
for ~60 curated, skill-bearing packages across five public registries and maps each
package to a strata skill, feeding **fact_skill_adoption** — the realized-usage signal
beside demand (postings) and interest (search).

Ecosystems + how each is obtained (no heavy deps; PyPI alone needs creds → graceful):
  (a) PyPI   — the BigQuery public dataset ``bigquery-public-data.pypi.file_downloads``.
               This is the ONLY ecosystem with a real per-download ``country_code``, so
               PyPI rows carry country (mapped to our ISO-2); every other ecosystem is
               geo-less and lands ``country=""`` (global). Needs ``google-cloud-bigquery``
               + GCP credentials; if either is absent we log a clear warning and skip
               PyPI cleanly (the other four still run).
  (b) npm     — ``https://api.npmjs.org/downloads/range/{period}/{package}`` (no auth).
  (c) crates  — ``https://crates.io/api/v1/crates/{crate}/downloads`` (no auth).
  (d) NuGet   — ``https://azuresearch-usnc.nuget.org/query?q=packageid:{id}`` for totals
               (per-version download counts; no public monthly series → landed as a
               single rolling total under the current period).
  (e) RubyGems— ``https://rubygems.org/api/v1/gems/{gem}.json`` (no auth; total downloads;
               same rolling-total treatment as NuGet).

GRAIN: ecosystem × package × skill × period(YYYY-MM) × downloads × country (PyPI only;
"" = global everywhere else). ROLES-ONLY: registries expose no employer data; nothing
company-shaped is landed. The cached ``adoption.json`` is the checkpoint. Network- and
credential-graceful throughout; coded for the later run, **not executed in this pass**.

Legitimacy: all five are the registries' own public, documented, rate-limited endpoints
(or, for PyPI, Google's published public BigQuery dataset). Counts are noisy — CI mirrors
and bots inflate them, NuGet/RubyGems give only lifetime totals, not a clean monthly
series — so adoption is a *relative momentum* signal, never an absolute headcount. Treated
as such downstream.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.package_registries")

HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}

# BigQuery country_code is ISO-2 already; keep only our 7, drop the rest to "" (global)
# so we never fabricate geography for countries outside scope.
OUR_COUNTRIES = {"IN", "US", "GB", "CA", "AU", "SG", "DE"}

# ---------------------------------------------------------------------------
# Curated package -> skill map. Each entry: (ecosystem, package_id, skill).
# ~60 high-signal, skill-bearing libraries/frameworks. Kept deliberately small and
# hand-picked: these are the packages whose adoption actually tracks a hireable skill.
# ---------------------------------------------------------------------------
PACKAGES: list[tuple[str, str, str]] = [
    # ---- PyPI (Python ecosystem) ----
    ("pypi", "pandas", "Data Analysis"),
    ("pypi", "numpy", "Numerical Computing"),
    ("pypi", "scikit-learn", "Machine Learning"),
    ("pypi", "torch", "PyTorch"),
    ("pypi", "tensorflow", "TensorFlow"),
    ("pypi", "transformers", "LLMs / Transformers"),
    ("pypi", "langchain", "LLM Orchestration"),
    ("pypi", "llama-index", "LLM Orchestration"),
    ("pypi", "openai", "LLM APIs"),
    ("pypi", "fastapi", "FastAPI"),
    ("pypi", "django", "Django"),
    ("pypi", "flask", "Flask"),
    ("pypi", "pydantic", "Python Typing / Validation"),
    ("pypi", "sqlalchemy", "SQL / ORM"),
    ("pypi", "airflow", "Data Orchestration"),
    ("pypi", "dbt-core", "Analytics Engineering"),
    ("pypi", "pyspark", "Apache Spark"),
    ("pypi", "polars", "Data Analysis"),
    ("pypi", "streamlit", "Data Apps"),
    ("pypi", "boto3", "AWS"),
    # ---- npm (JavaScript / TypeScript ecosystem) ----
    ("npm", "react", "React"),
    ("npm", "vue", "Vue"),
    ("npm", "@angular/core", "Angular"),
    ("npm", "svelte", "Svelte"),
    ("npm", "next", "Next.js"),
    ("npm", "nuxt", "Nuxt"),
    ("npm", "express", "Node.js / Express"),
    ("npm", "@nestjs/core", "NestJS"),
    ("npm", "typescript", "TypeScript"),
    ("npm", "vite", "Vite"),
    ("npm", "webpack", "Webpack"),
    ("npm", "tailwindcss", "Tailwind CSS"),
    ("npm", "redux", "Redux"),
    ("npm", "prisma", "Prisma / ORM"),
    ("npm", "graphql", "GraphQL"),
    ("npm", "playwright", "Test Automation"),
    ("npm", "jest", "JS Testing"),
    ("npm", "electron", "Electron"),
    # ---- crates.io (Rust ecosystem) ----
    ("crates", "tokio", "Rust Async"),
    ("crates", "serde", "Rust"),
    ("crates", "actix-web", "Rust Web"),
    ("crates", "axum", "Rust Web"),
    ("crates", "clap", "Rust CLI"),
    ("crates", "reqwest", "Rust"),
    ("crates", "polars", "Data Analysis"),
    # ---- NuGet (.NET ecosystem) ----
    ("nuget", "Newtonsoft.Json", ".NET"),
    ("nuget", "Microsoft.EntityFrameworkCore", "Entity Framework"),
    ("nuget", "Serilog", ".NET"),
    ("nuget", "AutoMapper", ".NET"),
    ("nuget", "Dapper", ".NET / SQL"),
    ("nuget", "xunit", ".NET Testing"),
    ("nuget", "MediatR", ".NET"),
    # ---- RubyGems (Ruby ecosystem) ----
    ("rubygems", "rails", "Ruby on Rails"),
    ("rubygems", "sidekiq", "Ruby Background Jobs"),
    ("rubygems", "devise", "Ruby on Rails"),
    ("rubygems", "rspec", "Ruby Testing"),
    ("rubygems", "puma", "Ruby on Rails"),
    ("rubygems", "sinatra", "Ruby Web"),
    # ---- Java (Maven Central via search.maven.org) ----
    ("maven", "org.springframework.boot:spring-boot", "Spring Boot"),
    ("maven", "org.apache.kafka:kafka-clients", "Apache Kafka"),
    ("maven", "io.quarkus:quarkus-core", "Quarkus"),
]


def _staging_dir():
    d = settings.staging_dir / "package_registries"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "adoption.json"


def _current_period() -> str:
    return date.today().strftime("%Y-%m")


def _last_full_month() -> tuple[str, str, str]:
    """(period, start_date, end_date) for the most recent fully-elapsed calendar month."""
    first_this = date.today().replace(day=1)
    last_prev = first_this - timedelta(days=1)
    start = last_prev.replace(day=1)
    return last_prev.strftime("%Y-%m"), start.isoformat(), last_prev.isoformat()


def _get_json(url: str, timeout: int = 30) -> dict | list | None:
    """One GET → parsed JSON, or None on any HTTP/network/parse error (caller skips)."""
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as e:
        log.debug("package_registries: GET failed %s (%s)", url, e)
        return None


# ---------------------------------------------------------------------------
# Per-ecosystem fetchers. Each returns a list of adoption rows (or [] on failure).
# ---------------------------------------------------------------------------
def _fetch_npm(package: str, skill: str, period: str, start: str, end: str) -> list[dict]:
    """npm: documented download-counts API, point range = one full month, geo-less."""
    url = f"https://api.npmjs.org/downloads/range/{start}:{end}/{package}"
    payload = _get_json(url)
    if not isinstance(payload, dict):
        return []
    total = sum(int(d.get("downloads") or 0) for d in (payload.get("downloads") or []))
    if total <= 0:
        return []
    return [{"ecosystem": "npm", "package": package, "skill": skill,
             "period": period, "downloads": total, "country": ""}]


def _fetch_crates(crate: str, skill: str, period: str) -> list[dict]:
    """crates.io: per-version daily download series → summed over the target month."""
    url = f"https://crates.io/api/v1/crates/{crate}/downloads"
    payload = _get_json(url)
    if not isinstance(payload, dict):
        return []
    series = payload.get("version_downloads") or []
    total = 0
    for pt in series:
        d = str(pt.get("date") or "")
        if d.startswith(period):                       # YYYY-MM prefix match
            total += int(pt.get("downloads") or 0)
    # crates.io only retains ~90 days of daily history; fall back to the meta total
    # (lifetime) under the current period if the target month yielded nothing.
    if total <= 0:
        meta = payload.get("meta") or {}
        extra = meta.get("extra_downloads") or []
        total = sum(int(p.get("downloads") or 0) for p in extra)
    if total <= 0:
        return []
    return [{"ecosystem": "crates", "package": crate, "skill": skill,
             "period": period, "downloads": total, "country": ""}]


def _fetch_nuget(package: str, skill: str, period: str) -> list[dict]:
    """NuGet: search index gives lifetime totalDownloads only → rolling total."""
    url = f"https://azuresearch-usnc.nuget.org/query?q=packageid:{package}&take=1"
    payload = _get_json(url)
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or []
    if not data:
        return []
    total = int(data[0].get("totalDownloads") or 0)
    if total <= 0:
        return []
    return [{"ecosystem": "nuget", "package": package, "skill": skill,
             "period": period, "downloads": total, "country": ""}]


def _fetch_rubygems(gem: str, skill: str, period: str) -> list[dict]:
    """RubyGems: gem JSON gives lifetime downloads only → rolling total."""
    url = f"https://rubygems.org/api/v1/gems/{gem}.json"
    payload = _get_json(url)
    if not isinstance(payload, dict):
        return []
    total = int(payload.get("downloads") or 0)
    if total <= 0:
        return []
    return [{"ecosystem": "rubygems", "package": gem, "skill": skill,
             "period": period, "downloads": total, "country": ""}]


def _fetch_maven(coord: str, skill: str, period: str) -> list[dict]:
    """Maven Central search: 'group:artifact' → indexed version count as a proxy.

    Maven Central publishes no download counts at all (the Sonatype stats API needs
    per-namespace auth), so we land the number of indexed published versions as a
    coarse activity proxy. Clearly weaker than a real count; flagged as such.
    """
    if ":" not in coord:
        return []
    group, artifact = coord.split(":", 1)
    url = ("https://search.maven.org/solrsearch/select"
           f"?q=g:%22{group}%22+AND+a:%22{artifact}%22&core=gav&rows=1&wt=json")
    payload = _get_json(url)
    if not isinstance(payload, dict):
        return []
    found = int(((payload.get("response") or {}).get("numFound")) or 0)
    if found <= 0:
        return []
    return [{"ecosystem": "maven", "package": coord, "skill": skill,
             "period": period, "downloads": found, "country": ""}]


def _fetch_pypi_bigquery(period: str, max_packages: int | None = None) -> list[dict]:
    """PyPI per-country monthly downloads via the public BigQuery dataset.

    The ONLY ecosystem with real geography: ``file_downloads.country_code`` is genuine
    per-request ISO-2. Needs ``google-cloud-bigquery`` + GCP credentials. If the package
    is missing or no credentials resolve, log a clear warning and return [] — PyPI is
    skipped cleanly while the no-auth registries still run. NOTE: this dataset is large
    and billed; the query is constrained to one month × our curated PyPI packages × our
    7 countries to keep the scan tiny.
    """
    try:
        from google.cloud import bigquery  # type: ignore
    except Exception:  # noqa: BLE001 — missing optional dep is an expected skip
        log.warning("package_registries: google-cloud-bigquery not installed — "
                    "skipping PyPI (the geo-bearing ecosystem); other registries continue")
        return []

    pypi_pkgs = [p for (eco, p, _s) in PACKAGES if eco == "pypi"]
    skill_of = {p: s for (eco, p, s) in PACKAGES if eco == "pypi"}
    if max_packages:
        pypi_pkgs = pypi_pkgs[:max_packages]
    if not pypi_pkgs:
        return []

    start = f"{period}-01"
    try:
        client = bigquery.Client()  # resolves ADC / GOOGLE_APPLICATION_CREDENTIALS
    except Exception as e:  # noqa: BLE001 — no/invalid credentials → graceful skip
        log.warning("package_registries: BigQuery client init failed (%s) — "
                    "skipping PyPI; set GOOGLE_APPLICATION_CREDENTIALS to enable", e)
        return []

    # Partition pruning on the ingestion-time partition keeps the scan to one month.
    query = """
        SELECT file.project AS package, country_code, COUNT(*) AS downloads
        FROM `bigquery-public-data.pypi.file_downloads`
        WHERE DATE(timestamp) >= DATE(@start)
          AND DATE(timestamp) < DATE_ADD(DATE(@start), INTERVAL 1 MONTH)
          AND file.project IN UNNEST(@packages)
          AND country_code IN UNNEST(@countries)
        GROUP BY package, country_code
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("start", "DATE", start),
        bigquery.ArrayQueryParameter("packages", "STRING", pypi_pkgs),
        bigquery.ArrayQueryParameter("countries", "STRING", sorted(OUR_COUNTRIES)),
    ])
    rows: list[dict] = []
    try:
        for r in client.query(query, job_config=job_config).result():
            cc = (r.get("country_code") or "").upper()
            if cc not in OUR_COUNTRIES:
                continue
            dl = int(r.get("downloads") or 0)
            if dl <= 0:
                continue
            pkg = r.get("package")
            rows.append({"ecosystem": "pypi", "package": pkg,
                         "skill": skill_of.get(pkg, pkg), "period": period,
                         "downloads": dl, "country": cc})
    except Exception as e:  # noqa: BLE001 — a query failure must not sink the run
        log.warning("package_registries: PyPI BigQuery query failed (%s) — skip", e)
        return []
    log.info("package_registries: PyPI BigQuery → %d country rows", len(rows))
    return rows


def fetch_adoption(force: bool = False, time_cap_s: float = 600.0,
                   include_pypi: bool = True, heartbeat_every: int = 10) -> list[dict]:
    """Fetch + cache adoption rows across all five+ ecosystems. Cache is the checkpoint.

    No-auth registries (npm/crates/NuGet/RubyGems/Maven) are fetched one package at a
    time with a polite per-call timeout, a flushing heartbeat every ``heartbeat_every``
    packages, and a ``time_cap_s`` wall. PyPI (BigQuery, geo-bearing) is fetched last and
    skipped gracefully when creds/deps are absent.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_adoption()

    period, start, end = _last_full_month()
    out: list[dict] = []
    t0 = time.time()
    done = 0

    no_geo = [(eco, pkg, skill) for (eco, pkg, skill) in PACKAGES if eco != "pypi"]
    for eco, pkg, skill in no_geo:
        if time.time() - t0 > time_cap_s:
            log.warning("package_registries: time cap %ss hit — landing partial", time_cap_s)
            break
        try:
            if eco == "npm":
                rows = _fetch_npm(pkg, skill, period, start, end)
            elif eco == "crates":
                rows = _fetch_crates(pkg, skill, period)
            elif eco == "nuget":
                rows = _fetch_nuget(pkg, skill, period)
            elif eco == "rubygems":
                rows = _fetch_rubygems(pkg, skill, period)
            elif eco == "maven":
                rows = _fetch_maven(pkg, skill, period)
            else:
                rows = []
            out.extend(rows)
        except Exception as e:  # noqa: BLE001 — one package must not sink the run
            log.warning("package_registries: %s/%s failed (%s) — skip", eco, pkg, e)
        done += 1
        if done % heartbeat_every == 0:
            print(f"[package_registries] {done}/{len(no_geo)} no-auth packages, "
                  f"{len(out)} rows so far", flush=True)
        time.sleep(0.2)                                # be polite to public endpoints

    if include_pypi:
        out.extend(_fetch_pypi_bigquery(period))

    if out:
        f.write_text(json.dumps(out), encoding="utf-8")
    log.info("package_registries: %d adoption rows across %d ecosystems (%d w/ country)",
             len(out), len({r["ecosystem"] for r in out}),
             sum(1 for r in out if r["country"]))
    return out


def load_adoption() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def build_staging(force: bool = False, **kw) -> list[dict]:
    """Ensure the cleaned adoption.json exists (fetch if missing) and return its rows."""
    return fetch_adoption(force=force, **kw)


def run(**kw) -> dict:
    """Land + cache package-registry adoption volume. collect_all entrypoint."""
    rows = fetch_adoption(**kw)
    return {
        "rows": len(rows),
        "ecosystems": sorted({r["ecosystem"] for r in rows}),
        "skills": len({r["skill"] for r in rows}),
        "with_country": sum(1 for r in rows if r["country"]),
        "countries": sorted({r["country"] for r in rows if r["country"]}),
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
