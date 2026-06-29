"""Role derivation (brief §4, GPU clustering).

Roles are **not** a fixed list. Cluster postings per country into canonical roles;
a role enters the catalog only when it clears the **minimum posting-volume floor**
(``ROLE_VOLUME_FLOOR``, config) for the period. Different countries surface different
roles organically. Grouping per (country) also blocks the clusterer so n stays bounded.

The unit of clustering is the **composite fingerprint** — title ⊕ skills ⊕ department
⊕ salary-band (built by ``_composite_docs`` via ``fingerprint.composite_document``),
*not* the bare title. The MiniLM embedding of that fingerprint is **preprocessing**, not
the product. This is the SAME representation ``fingerprint.cluster_fingerprints`` uses at
scale, so role discovery and the scale path can't diverge (there is no longer a
title-only clustering path): "Data Engineer" (Spark) and "Analytics Engineer" (dbt) stay
distinct despite near-identical titles, and a title-less "MTS" still lands via its skills.

Writes ``staging/normalized/derived_roles.parquet`` — one row per derived role
that cleared the floor: (role_id, country, label_title, posting_count, member_titles).
The warehouse build promotes these into ``dim_role`` (is_seed=False) where present,
otherwise keeps the curated catalogue.

Clustering backend: ``_cluster_embed`` prefers hdbscan when installed, but hdbscan is
NOT installed in this environment, so at runtime it falls through to
``fingerprint.threshold_cluster`` — a sub-quadratic FAISS kNN-graph → connected-components
clusterer at the cosine-distance threshold (no fixed k, no O(n²) distance matrix). When
the embedding stack itself can't import, a deterministic normalized-title grouping is used
so the stage still writes real clusters.
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


_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(settings.embed_model, device=settings.ml_device)
    return _MODEL


def _cluster_embed(docs: list[str]) -> list[int]:
    """Embed (MiniLM, GPU) the COMPOSITE documents — this is PREPROCESSING, not the
    product — then group them with a SUB-QUADRATIC threshold clusterer so role
    discovery and ``fingerprint.cluster_fingerprints`` share one representation AND one
    scaling story.

    If hdbscan is installed it is used (density-based, already sub-quadratic); it is
    not in this environment, so the default is ``fingerprint.threshold_cluster`` — a
    FAISS kNN-graph → connected-components clusterer at the cosine-distance threshold
    (0.32). That preserves the old AgglomerativeClustering's no-fixed-k threshold
    semantics without its O(n²) distance matrix, which OOM'd past ~30-50k rows.
    """
    import numpy as np

    model = _get_model()
    emb = model.encode(docs, batch_size=settings.embed_batch_size,
                       normalize_embeddings=True, show_progress_bar=False)
    arr = np.asarray(emb, dtype="float32")
    try:
        from hdbscan import HDBSCAN
        kw = {"min_cluster_size": max(10, settings.role_volume_floor // 10)}
        labels = HDBSCAN(**kw).fit_predict(arr)
        return [int(x) for x in labels]
    except Exception:  # noqa: BLE001 — hdbscan absent (default) → FAISS threshold graph
        from backend.ml.fingerprint import threshold_cluster
        return threshold_cluster(arr, distance_threshold=0.32)


def _composite_docs(grp) -> list[str]:
    """Build one composite fingerprint (title⊕skills⊕dept⊕salary-band) per posting —
    reuses fingerprint.composite_document + extract_skills so role discovery and the
    scale-clustering path share an identical representation (no second clustering path).
    """
    from backend.ml.fingerprint import FingerprintInput, composite_document, extract_skills

    n = len(grp)
    titles = grp.get("title", "").fillna("").astype(str).tolist()
    descs = (grp["description"].fillna("").astype(str).tolist()
             if "description" in grp.columns else [""] * n)
    depts = (grp["department"].fillna("").astype(str).tolist()
             if "department" in grp.columns else [""] * n)
    smax = grp["salary_max"].tolist() if "salary_max" in grp.columns else [None] * n
    smin = grp["salary_min"].tolist() if "salary_min" in grp.columns else [None] * n
    docs = []
    for i, t in enumerate(titles):
        sk = extract_skills(f"{t}  {descs[i]}")
        sal = smax[i] or smin[i]
        docs.append(composite_document(FingerprintInput(
            title=t, skills=sk, department=depts[i], salary=sal)))
    return docs


def _cluster_lexical(titles: list[str]) -> list[int]:
    """Degraded fallback (no embedding stack): group by canonicalized title."""
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
        if "country" not in posts.columns:
            posts["country"] = posts.get("country_code")
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
            # cluster on the COMPOSITE fingerprint (title⊕skills⊕dept⊕salary-band), the
            # same representation fingerprint.cluster_fingerprints uses at scale, so the
            # two paths can't diverge. Bare-title clustering merged distinct roles that
            # happen to share a title (Data vs Analytics Engineer); composite keeps them
            # apart and rescues title-less reqs ("MTS") via their skills.
            try:
                docs = _composite_docs(grp) if mode == "embed" else titles
                labels = _cluster_embed(docs) if mode == "embed" else _cluster_lexical(titles)
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
