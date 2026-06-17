"""Role derivation (brief §4, GPU clustering).

Roles are **not** a fixed list. Cluster normalized titles per country into
canonical roles; a role enters the catalog only when it clears the **minimum
posting-volume floor** (``ROLE_VOLUME_FLOOR``, config) for the period. Different
countries surface different roles organically.

Writes ``staging/normalized/derived_roles.parquet`` — one row per derived role
that cleared the floor: (role_id, country, label_title, posting_count, member_titles).
The warehouse build promotes these into ``dim_role`` (is_seed=False) where present,
otherwise keeps the curated catalogue.

hdbscan is not installed, so the sklearn AgglomerativeClustering fallback is used
(matches the brief). When the embedding stack itself can't import, a deterministic
normalized-title grouping is used so the stage still writes real clusters.
"""
from __future__ import annotations

import json
import re

from backend.core.config import settings
from backend.core.logging import get_logger, stage_timer

log = get_logger("ml.role_derivation")

POSTINGS = "common_crawl/postings.parquet"
OUT_REL = "normalized/derived_roles.parquet"


def _have_embeddings() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        import sklearn  # noqa: F401
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("role_derivation: embedding stack unavailable (%s) — deterministic fallback", e)
        return False


def _staging():
    return settings.staging_dir / POSTINGS


def _out():
    p = settings.staging_dir / OUT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_NONWORD = re.compile(r"[^a-z0-9 ]+")
_SENIORITY = re.compile(r"\b(senior|sr|junior|jr|lead|principal|staff|i{1,3}|iv|1|2|3|4|entry|mid)\b")


def _canon_title(title: str) -> str:
    """Strip seniority/levels so 'Senior Data Engineer II' ~ 'Data Engineer'."""
    s = (title or "").lower()
    s = _NONWORD.sub(" ", s)
    s = _SENIORITY.sub(" ", s)
    return " ".join(s.split())


def _cluster_embed(titles: list[str]) -> list[int]:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    try:
        from hdbscan import HDBSCAN as Clusterer
        kw = {"min_cluster_size": max(10, settings.role_volume_floor // 10)}
    except Exception:
        from sklearn.cluster import AgglomerativeClustering as Clusterer
        kw = {"n_clusters": None, "distance_threshold": 0.35, "metric": "cosine", "linkage": "average"}

    model = SentenceTransformer(settings.embed_model, device=settings.ml_device)
    emb = model.encode(titles, batch_size=settings.embed_batch_size,
                       normalize_embeddings=True, show_progress_bar=False)
    labels = Clusterer(**kw).fit_predict(np.asarray(emb, dtype="float32"))
    return [int(x) for x in labels]


def _cluster_lexical(titles: list[str]) -> list[int]:
    """Fallback: group by canonicalized (seniority-stripped) title."""
    groups: dict[str, int] = {}
    return [groups.setdefault(_canon_title(t), len(groups)) for t in titles]


def run() -> dict:
    with stage_timer(log, "ml.role_derivation"):
        from backend.warehouse.build import slug

        path = _staging()
        if not path.exists():
            log.warning("role_derivation: no postings at %s — nothing to cluster", path)
            return {"postings": 0, "derived": 0, "written": False, "mode": "skipped"}

        import pandas as pd

        posts = pd.read_parquet(path)
        if posts.empty:
            log.warning("role_derivation: postings parquet is empty")
            return {"postings": 0, "derived": 0, "written": False, "mode": "skipped"}

        floor = int(settings.role_volume_floor)
        mode = "embed" if _have_embeddings() else "lexical"
        rows = []
        derived = 0
        for code, grp in posts.groupby("country", dropna=True):
            titles = grp.get("title", "").fillna("").astype(str).tolist()
            if len(titles) < floor:
                log.info("role_derivation: %s has %d postings < floor %d — skip", code, len(titles), floor)
                continue
            try:
                labels = _cluster_embed(titles) if mode == "embed" else _cluster_lexical(titles)
            except Exception as e:  # noqa: BLE001
                log.warning("role_derivation: embed cluster failed (%s) — lexical", e)
                mode = "lexical"
                labels = _cluster_lexical(titles)

            from collections import Counter, defaultdict
            members = defaultdict(list)
            for t, lbl in zip(titles, labels):
                if lbl == -1:  # noise (hdbscan)
                    continue
                members[lbl].append(t)
            for lbl, mtitles in members.items():
                if len(mtitles) < floor:
                    continue
                label_title = Counter(_canon_title(t) for t in mtitles).most_common(1)[0][0] or "role"
                rows.append({
                    "role_id": f"derived:{code.lower()}:{slug(label_title)}",
                    "country": code,
                    "label_title": label_title.title(),
                    "posting_count": len(mtitles),
                    "member_titles": json.dumps(sorted(set(mtitles))[:50]),
                    "method": mode,
                })
                derived += 1

        out = _out()
        df = pd.DataFrame(rows, columns=[
            "role_id", "country", "label_title", "posting_count", "member_titles", "method",
        ])
        df.to_parquet(out, index=False)
        summary = {
            "postings": len(posts),
            "derived": derived,
            "floor": floor,
            "written": True,
            "mode": mode,
            "out": str(out),
        }
        log.info("role_derivation: %s", summary)
        return summary
