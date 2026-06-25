"""Hugging Face Hub — decompose the coarse "AI/ML" skill blob into **durable
sub-skills** by measuring artifact-creation velocity: how many models and datasets
get created per pipeline-tag / library-tag per month (council source: open-model
ecosystem).

A posting that says "AI/ML" tells you nothing about *which* AI/ML — is the demand for
``text-generation`` plumbers, ``image-segmentation`` people, ``sentence-similarity``
folks, ``peft``/``transformers`` engineers? The Hugging Face Hub is the public
clearing-house for open models and datasets, and every artifact carries a
``pipeline_tag`` (e.g. ``text-generation``, ``automatic-speech-recognition``), a list
of ``tags`` (libraries/tasks like ``transformers``, ``diffusers``, ``peft``,
``text-classification``), and a ``createdAt`` timestamp. Counting creations per tag per
month gives a leading-indicator **adoption velocity** for each AI/ML sub-skill — the
durable decomposition strata needs to replace the undifferentiated blob. It feeds
``fact_skill_adoption``.

GLOBAL signal: the Hub has no real per-country geography for artifact creation, so every
row lands ``country=''`` (global) — we never fabricate geo.

Obtained from the public Hugging Face Hub API
(https://huggingface.co/api/models and /api/datasets), sorted by ``createdAt``
descending and paginated. No key required; an optional ``HF_TOKEN`` (settings) only
raises rate limits — credential-graceful, so absence just logs and proceeds. The Hub API
is public and documented; pagination via the ``Link: rel="next"`` header. ROLES-ONLY:
we land tag × period × count only — model/dataset *authors* (which are orgs/users) are
DROPPED, never landed as a product field. Mirrors the worldbank_ppp / ilostat shape
(fetch+cache → build → load → run). Not run in this pass — coded for the later run.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import Counter

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.huggingface")

API_BASE = "https://huggingface.co/api"
# Ask for exactly the fields we need so payloads stay small and the parse is honest.
MODEL_PARAMS = "?sort=createdAt&direction=-1&limit={limit}&full=false&config=false"
DATASET_PARAMS = "?sort=createdAt&direction=-1&limit={limit}&full=false"
HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}

# Tags that are noise for a *skill* signal (licenses, regions, dataset-size buckets,
# arxiv refs, language codes …). We keep task/library tags; drop the rest by prefix.
_DROP_TAG_PREFIXES = (
    "license:", "region:", "size_categories:", "arxiv:", "dataset:", "base_model:",
    "doi:", "language:", "annotations_creators:", "language_creators:",
    "source_datasets:", "multilinguality:", "task_ids:", "modality:",
)


def _staging_dir():
    d = settings.staging_dir / "huggingface"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "velocity.json"


def _auth_headers() -> dict:
    """HF_TOKEN is optional — only lifts rate limits. Absent → anonymous (graceful)."""
    h = dict(HEADERS)
    token = getattr(settings, "HF_TOKEN", None)
    if token:
        h["Authorization"] = f"Bearer {token}"
    else:
        log.warning("huggingface: no HF_TOKEN in settings — proceeding anonymously "
                    "(lower rate limit, may fetch fewer pages)")
    return h


def _next_url(resp) -> str | None:
    """Parse the RFC-5988 Link header for rel="next" (Hub's cursor pagination)."""
    link = resp.headers.get("Link") or resp.headers.get("link")
    if not link:
        return None
    for part in link.split(","):
        seg = part.split(";")
        if len(seg) < 2:
            continue
        url = seg[0].strip().strip("<>")
        if any('rel="next"' in s or "rel=next" in s for s in seg[1:]):
            return url
    return None


def _period(created_at: str) -> str | None:
    """ISO ``createdAt`` (e.g. '2023-08-14T...') → 'YYYY-MM'. None if unparseable."""
    if not created_at or len(created_at) < 7:
        return None
    ym = created_at[:7]
    if len(ym) == 7 and ym[4] == "-" and ym[:4].isdigit() and ym[5:].isdigit():
        return ym
    return None


def _skills_from_item(item: dict) -> list[str]:
    """Extract the durable sub-skill tags from one model/dataset record.

    Uses ``pipeline_tag`` (the primary task) plus task/library ``tags``; drops the
    license/region/size/arxiv/language noise so only real skills survive.
    """
    skills: list[str] = []
    pt = item.get("pipeline_tag")
    if pt:
        skills.append(str(pt).strip().lower())
    for tag in item.get("tags") or []:
        t = str(tag).strip().lower()
        if not t or ":" in t and t.startswith(_DROP_TAG_PREFIXES):
            continue
        if t.startswith(_DROP_TAG_PREFIXES):
            continue
        # plain task/library tags only (no namespaced metadata, no language codes)
        if ":" in t:
            continue
        skills.append(t)
    # de-dup within an item so one artifact counts once per skill
    return sorted(set(skills))


def _fetch_kind(kind: str, params: str, max_pages: int, page_limit: int,
                time_cap_s: float, headers: dict) -> Counter:
    """Paginate one artifact kind ('models'|'datasets'); count (skill, period) pairs.

    Network-graceful: any page failure logs + stops paging this kind (we keep what we
    have) rather than sinking the run.
    """
    counts: Counter = Counter()
    url = API_BASE + f"/{kind}" + params.format(limit=page_limit)
    t0 = time.time()
    pages = 0
    while url and pages < max_pages:
        if time.time() - t0 > time_cap_s:
            log.warning("huggingface: %s time cap %ss hit at page %d — partial",
                        kind, time_cap_s, pages)
            break
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                nxt = _next_url(resp)
        except Exception as e:  # noqa: BLE001 — one page must not sink the run
            log.warning("huggingface: %s page %d failed (%s) — stop paging kind",
                        kind, pages, e)
            break
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            period = _period(item.get("createdAt") or item.get("created_at") or "")
            if not period:
                continue
            for skill in _skills_from_item(item):
                counts[(skill, period)] += 1
        pages += 1
        if pages % 5 == 0:
            print(f"[huggingface] {kind}: {pages} pages, "
                  f"{len(counts)} (skill,month) cells so far", flush=True)
        url = nxt
    log.info("huggingface: %s — %d pages, %d (skill,month) cells",
             kind, pages, len(counts))
    return counts


def fetch_velocity(force: bool = False, max_pages: int = 200, page_limit: int = 200,
                   time_cap_s: float = 600.0) -> list[dict]:
    """Fetch + cache per-tag monthly creation counts for models and datasets.

    The cache file IS the checkpoint: if present and not ``force``, return load.
    ``max_pages`` / ``page_limit`` / ``time_cap_s`` bound the (potentially huge) crawl.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_velocity()

    headers = _auth_headers()
    total: Counter = Counter()
    for kind, params in (("models", MODEL_PARAMS), ("datasets", DATASET_PARAMS)):
        try:
            total.update(_fetch_kind(kind, params, max_pages, page_limit,
                                     time_cap_s, headers))
        except Exception as e:  # noqa: BLE001 — one kind must not sink the run
            log.warning("huggingface: %s crawl failed (%s) — skip kind", kind, e)

    rows = [
        {"skill": skill, "period": period, "n": n, "country": ""}
        for (skill, period), n in sorted(total.items())
    ]
    if rows:
        f.write_text(json.dumps(rows), encoding="utf-8")
    log.info("Hugging Face velocity: %d (skill,month) rows across %d skills",
             len(rows), len({r["skill"] for r in rows}))
    return rows


def load_velocity() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache Hugging Face per-tag monthly creation velocity. Entrypoint."""
    rows = fetch_velocity(**kw)
    return {
        "rows": len(rows),
        "skills": len({r["skill"] for r in rows}),
        "periods": len({r["period"] for r in rows}),
        "country": "",  # global signal — no real geography
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
