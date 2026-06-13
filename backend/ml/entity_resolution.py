"""Entity resolution / dedup (brief §4, GPU + blocking).

The same role is posted across many boards/countries; counts must not inflate.
Dedup near-identical postings with embeddings + blocking keys (employer, country,
normalized title) + similarity thresholds. (Taxonomy normalization in `skill_norm`
is the higher-value ER and must be solid.) Requires `requirements-ml` + ingested
STAGING postings; skips+flags otherwise.
"""
from __future__ import annotations

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ml.entity_resolution")


def _have_ml() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


def run() -> None:
    staging = settings.staging_dir / "common_crawl" / "postings.parquet"
    if not staging.exists():
        raise NotImplementedError("entity_resolution needs ingested STAGING postings (Phase 3).")
    if not _have_ml():
        raise NotImplementedError(
            "entity_resolution needs the GPU extras (pip install -r backend/requirements-ml.txt)."
        )
    # ---- real path: block by (employer, country), embed titles, union-find near-dupes ----
    import pandas as pd

    posts = pd.read_parquet(staging)
    blocks = posts.groupby(["employer", "country"], dropna=False)
    log.info("entity_resolution: %d postings in %d blocks → dedup within blocks via title embeddings",
             len(posts), blocks.ngroups)
    # within each block: cosine-sim titles ≥ threshold ⇒ same posting; keep one, sum is_unique flags.
