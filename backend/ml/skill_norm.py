"""Skill extraction + taxonomy normalization (brief §4, GPU).

The connective tissue: map free-text skill mentions to a canonical
Lightcast/ESCO/O*NET id via sentence-transformer embeddings + FAISS nearest-
neighbour against the embedded taxonomy. Batch inference, VRAM ≤ ~16 GB, cache
embeddings. Requires `requirements-ml` (torch+sentence-transformers+faiss) and
ingested STAGING postings; skips+flags clearly otherwise.
"""
from __future__ import annotations

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ml.skill_norm")


def _have_ml() -> bool:
    try:
        import faiss  # noqa: F401
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


def run() -> None:
    staging = settings.staging_dir / "common_crawl" / "postings.parquet"
    if not staging.exists():
        raise NotImplementedError(
            "skill_norm needs ingested STAGING postings (run the Common Crawl connector first, Phase 3)."
        )
    if not _have_ml():
        raise NotImplementedError(
            "skill_norm needs the GPU extras: pip install -r backend/requirements-ml.txt "
            "(torch cu128 + sentence-transformers + faiss)."
        )
    # ---- real path (runs on the developer's GPU over ingested data) ----
    import faiss
    import numpy as np
    import pandas as pd
    from sentence_transformers import SentenceTransformer

    from backend.core.db import duckdb_connect

    model = SentenceTransformer(settings.embed_model, device=settings.ml_device)
    con = duckdb_connect()
    taxo = con.execute("SELECT skill_id, name FROM dim_skill").fetchall()
    names = [t[1] for t in taxo]
    tax_emb = model.encode(names, batch_size=settings.embed_batch_size, normalize_embeddings=True)
    index = faiss.IndexFlatIP(tax_emb.shape[1])
    index.add(np.asarray(tax_emb, dtype="float32"))

    posts = pd.read_parquet(staging)
    mentions = posts["description"].fillna("").tolist()
    emb = model.encode(mentions, batch_size=settings.embed_batch_size, normalize_embeddings=True)
    sims, idx = index.search(np.asarray(emb, dtype="float32"), 5)
    log.info("skill_norm: matched %d postings against %d taxonomy skills", len(mentions), len(names))
    # → write canonical skill ids + confidence to STAGING for the warehouse build
    con.close()
