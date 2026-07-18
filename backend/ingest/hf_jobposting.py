"""Hugging Face pre-extracted job-posting corpus — the volume unlock (2.6M postings).

The extraction pipeline is validated but starved: 1,128 postings, 258:1 US-skewed. This
connector lands the single largest *download*-able corpus found in the data-expansion
research: ``2024-mcm-everitt-ryan/job-postings-raw`` on the HF Hub — **2,597,092 rows**
across 21 parquet shards, every row carrying the FULL posting body (``job_posting_html``),
a ``country`` tag, ``position_name`` (title), and — critically for the honesty layer —
per-row provenance: ``dataset_source`` / ``dataset_title`` / ``dataset_url`` /
``dataset_license`` (the corpus is a cleaned aggregation of Techmap.io's Kaggle dumps;
most rows are CC BY-NC-ND 4.0). Live-verified country distribution (measured, not the
dataset card): US ~1.45M, DE ~664K, UK ~210K, CA ~8K, IN ~6K, AU ~1K, SG ~0.6K — a
massive US+DE+UK+EU unlock; India/SG/AU still lean on the ATS + Adzuna levers.

VINTAGE: ~2021-2023 (Techmap snapshots). This lands as a clearly-labelled HISTORICAL
posting layer, not "current" — ``kind='historical'`` + ``retrieved`` per row — so the
downstream role catalog / skill spine gets breadth without pretending the postings are
live. Salary is NOT taken from here (postings rarely disclose; the salary lenses stay
their own sources).

METHOD: DuckDB's httpfs reads each remote parquet shard directly with predicate + column
pushdown — we SELECT only our 7 countries and only the columns we need, so the network
cost is a fraction of the 3.5GB total. Bodies are HTML → stripped to text; tech-filtered
at ingest via the shared ``tech_filter``; deduped on ``document_id``. Output is one
``staging/hf_jobposting/postings.parquet`` with the SAME (title, description) shape
``llm_extract`` consumes, plus provenance columns, so it slots straight into the
extraction corpus (description-gated there, so body-less rows never enter).

ROLES-ONLY: ``companyName`` is landed ONLY as an internal dedup aid (like every other
connector's ``employer``); it is never a product axis. NO FABRICATION: every row is
source + license + vintage labelled; nothing is invented.

Bounded + resumable: ``run(max_shards=...)`` caps shards for a proof sample; each shard's
filtered output is cached (a shard already materialized is skipped) so a capped run makes
cumulative progress and the full pull is just ``max_shards=None``.
"""
from __future__ import annotations

import re

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.ingest.tech_filter import classify as is_tech

log = get_logger("ingest.hf_jobposting")

HF_DATASET = "2024-mcm-everitt-ryan/job-postings-raw"
N_SHARDS = 21
SHARD_URL = ("https://huggingface.co/datasets/2024-mcm-everitt-ryan/job-postings-raw/"
             "resolve/main/data/train-{i:05d}-of-00021.parquet")

# strata's seven charter countries (ISO-2, upper). The corpus uses upper-case ISO-2.
STRATA_COUNTRIES = ("US", "GB", "IN", "DE", "CA", "AU", "SG")

MAX_DESC_CHARS = 8000        # keep bodies bounded for the extractor prompt budget
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _staging_dir():
    d = settings.staging_dir / "hf_jobposting"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _shard_file(i: int):
    return _staging_dir() / f"shard_{i:05d}.parquet"


def _consolidated():
    return _staging_dir() / "postings.parquet"


def _strip_html(html) -> str:
    """schema.org/Techmap bodies are HTML — flatten to readable text, collapse space."""
    if not html:
        return ""
    txt = _TAG.sub(" ", str(html))
    txt = (txt.replace("&amp;", "&").replace("&nbsp;", " ").replace("&lt;", "<")
              .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    txt = _WS.sub(" ", txt).strip()
    return txt[:MAX_DESC_CHARS]


def _duck():
    import duckdb
    con = duckdb.connect()
    try:
        con.execute("SET enable_progress_bar=false;")
        con.execute("INSTALL httpfs; LOAD httpfs;")
    except Exception as e:  # noqa: BLE001 — httpfs may already be present
        log.info("hf_jobposting: duckdb httpfs setup note (%s)", e)
    return con


def _fetch_shard(i: int, con, timeout_note: str = "") -> int:
    """Pull ONE remote shard, filter to our 7 countries server-side, tech-filter +
    HTML-strip locally, and materialize ``shard_XXX.parquet``. Returns kept-row count.

    Predicate + column pushdown means DuckDB only transfers rows whose ``country`` is in
    our set and only the columns we read — a fraction of the shard's bytes.
    """
    import pandas as pd

    out = _shard_file(i)
    if out.exists():
        try:
            n = con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
            log.info("hf_jobposting: shard %d already materialized (%d rows) — skip", i, n)
            return int(n)
        except Exception:  # noqa: BLE001 — corrupt cache → re-fetch
            pass

    url = SHARD_URL.format(i=i)
    cc = ", ".join(f"'{c}'" for c in STRATA_COUNTRIES)
    # only our columns, only our countries, only rows that actually carry a body
    q = f"""
        SELECT document_id, position_name AS title, job_posting_html AS body,
               country AS country_code, detected_language AS language,
               companyName AS employer, city,
               dataset_source, dataset_title, dataset_url, dataset_license, source
        FROM read_parquet('{url}')
        WHERE country IN ({cc})
          AND job_posting_html IS NOT NULL AND length(job_posting_html) > 40
    """
    df = con.execute(q).df()
    if df.empty:
        log.info("hf_jobposting: shard %d — 0 rows in strata countries", i)
        return 0

    # local pass: strip HTML, tech-filter, dedup on document_id.
    # TITLE-ONLY tech gate: classifying on the body too is ~50% precision — non-tech
    # postings (a German gov clerk, a medical scheduler) mention 'PC'/'Software'/'Computer'
    # incidentally in their descriptions and slip through. The title alone is ~85%+
    # precise, and the LLM extractor's roles-only abstain is the real final gate anyway,
    # so a permissive-but-cleaner pre-filter just saves GPU on obvious non-tech rows.
    df["description"] = df["body"].map(_strip_html)
    df = df[df["description"].str.len() >= 80]                 # need a real body to extract
    keep = df["title"].fillna("").astype(str)
    df = df[[is_tech(t) for t in keep]]
    df = df.drop_duplicates(subset=["document_id"])
    if df.empty:
        log.info("hf_jobposting: shard %d — 0 tech rows after filter", i)
        return 0

    landed = pd.DataFrame({
        "title": df["title"].fillna("").astype(str),
        "description": df["description"].astype(str),
        "country_code": df["country_code"].fillna("").astype(str),
        "language": df["language"].fillna("").astype(str),
        "employer": df["employer"].fillna("").astype(str),   # internal dedup only
        "city": df["city"].fillna("").astype(str),
        "kind": "historical",
        "source_dataset": df["dataset_title"].fillna("").astype(str),
        "source_url": df["dataset_url"].fillna("").astype(str),
        "license": df["dataset_license"].fillna("").astype(str),
        "retrieved": "hf:2024-mcm-everitt-ryan/job-postings-raw",
    })
    landed.to_parquet(out, index=False)
    log.info("hf_jobposting: shard %d → %d tech postings %s", i, len(landed), timeout_note)
    return len(landed)


def build_staging(max_shards: int | None = None, max_rows: int | None = None) -> dict:
    """Stream shards → per-shard parquet, then consolidate to postings.parquet.

    ``max_shards`` caps how many of the 21 shards to pull (a proof sample = 1). ``max_rows``
    stops once the consolidated corpus reaches that many kept rows (bound the extractor).
    """
    con = _duck()
    n_shards = N_SHARDS if max_shards is None else min(max_shards, N_SHARDS)
    total = 0
    by_country: dict[str, int] = {}
    for i in range(n_shards):
        try:
            total += _fetch_shard(i, con)
        except Exception as e:  # noqa: BLE001 — one shard must not sink the pull
            log.warning("hf_jobposting: shard %d failed (%s) — skip", i, e)
        if max_rows is not None and total >= max_rows:
            log.info("hf_jobposting: hit max_rows=%d at shard %d — stop", max_rows, i)
            break

    # consolidate every materialized shard → one corpus parquet
    shards = sorted(_staging_dir().glob("shard_*.parquet"))
    if shards:
        globpat = (_staging_dir() / "shard_*.parquet").as_posix()
        con.execute(
            f"COPY (SELECT * FROM read_parquet('{globpat}')) "
            f"TO '{_consolidated().as_posix()}' (FORMAT parquet)")
        n = con.execute(
            f"SELECT count(*) FROM read_parquet('{_consolidated().as_posix()}')").fetchone()[0]
        rows = con.execute(
            f"SELECT country_code, count(*) FROM read_parquet('{_consolidated().as_posix()}') "
            f"GROUP BY 1 ORDER BY 2 DESC").fetchall()
        by_country = {c: int(k) for c, k in rows}
        total = int(n)
    con.close()
    summary = {"shards_pulled": n_shards, "postings": total, "by_country": by_country}
    log.info("hf_jobposting staging: %s", summary)
    return summary


def load_postings():
    """Read the consolidated corpus back (pandas DataFrame) or None if not built."""
    p = _consolidated()
    if not p.exists():
        return None
    import pandas as pd
    return pd.read_parquet(p)


def run(max_shards: int | None = None, max_rows: int | None = None, **kw) -> dict:
    """Land the HF corpus into staging/hf_jobposting/postings.parquet. Connector entrypoint.

    Default pulls all 21 shards; pass ``max_shards=1`` for a bounded proof sample.
    """
    return build_staging(max_shards=max_shards, max_rows=max_rows)


if __name__ == "__main__":  # pragma: no cover
    import json
    print(json.dumps(run(max_shards=1, max_rows=8000), indent=2))
