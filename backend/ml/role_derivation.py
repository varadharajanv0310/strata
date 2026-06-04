"""Role derivation (brief §4, GPU clustering).

Roles are **not** a fixed list. Cluster normalized titles per country into
canonical roles; a role enters the catalog only when it clears the **minimum
posting-volume floor** (`ROLE_VOLUME_FLOOR`, config) for the period. Different
countries surface different roles organically. Requires `requirements-ml`
(embeddings + hdbscan/scikit-learn) + ingested STAGING postings.
"""
from __future__ import annotations

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ml.role_derivation")


def _have_ml() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        import sklearn  # noqa: F401
        return True
    except Exception:
        return False


def run() -> None:
    staging = settings.staging_dir / "common_crawl" / "postings.parquet"
    if not staging.exists():
        raise NotImplementedError("role_derivation needs ingested STAGING postings (Phase 3).")
    if not _have_ml():
        raise NotImplementedError(
            "role_derivation needs the GPU extras (pip install -r backend/requirements-ml.txt)."
        )
    # ---- real path: embed titles → cluster per country → apply volume floor ----
    import numpy as np
    import pandas as pd
    from sentence_transformers import SentenceTransformer
    try:
        from hdbscan import HDBSCAN as Clusterer
        kw = {"min_cluster_size": max(10, settings.role_volume_floor // 10)}
    except Exception:
        from sklearn.cluster import AgglomerativeClustering as Clusterer
        kw = {"n_clusters": None, "distance_threshold": 0.35}

    posts = pd.read_parquet(staging)
    model = SentenceTransformer(settings.embed_model, device=settings.ml_device)
    derived = 0
    for code, grp in posts.groupby("country", dropna=True):
        titles = grp["title"].fillna("").tolist()
        if len(titles) < settings.role_volume_floor:
            continue
        emb = model.encode(titles, batch_size=settings.embed_batch_size, normalize_embeddings=True)
        labels = Clusterer(**kw).fit_predict(np.asarray(emb, dtype="float32"))
        for lbl in set(labels):
            if lbl == -1:
                continue
            if int((labels == lbl).sum()) >= settings.role_volume_floor:
                derived += 1
    log.info("role_derivation: derived %d roles across countries (floor=%d)", derived, settings.role_volume_floor)
    # → write dim_role (with cluster lineage) for roles clearing the floor.
