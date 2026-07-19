"""Role derivation (brief §4, GPU clustering).

Roles are **not** a fixed list. Cluster postings per country into canonical roles.
The corpus is a **contributor** to the catalog, never a **boundary**: a clustered role
enters regardless of posting volume — the old ``role_volume_floor`` market-volume gate is
RETIRED (it dropped every thin-corpus India/SG/CA role, letting the US-skewed corpus decide
the catalog). Only a tiny ``_DENOISE_MIN_MEMBERS`` singleton-noise filter remains. This is
one of several catalog contributors (union with the curated seed + the corpus-free O*NET
occupation nodes in ``role_universe.py``); see ``reports/CATALOG_DECOUPLE_PLAN.md``.
Grouping per (country) also blocks the clusterer so n stays bounded.

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

# The retired role_volume_floor is replaced by a tiny denoise threshold: a cluster with
# fewer than this many member titles is embedding noise, not a market signal. This is a
# data-quality filter, NOT a market-volume boundary — a small real role still enters.
_DENOISE_MIN_MEMBERS = 2


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


def _load_raw_corpus():
    """Concatenate EVERY raw posting feed (title/description/country + dept/salary where the
    feed has them) so the role catalog's breadth derives from the WHOLE corpus (HF ~870k +
    ATS + Common Crawl), not just common_crawl's ~1k. This is pure raw-posting clustering —
    it never reads the LLM extraction output — so per the sparse-LLM decision breadth scales
    with the corpus at NO LLM cost. Returns a DataFrame or None; callers fall back to the
    single common_crawl parquet when nothing is present."""
    import pandas as pd
    try:
        from backend.ml.llm_extract import POSTING_FEEDS, _read_feed
    except Exception:  # noqa: BLE001
        return None
    frames = []
    for rel in POSTING_FEEDS:
        p = settings.staging_dir / rel
        if not p.exists():
            continue
        df = _read_feed(p)
        if df is None or df.empty or df.get("description") is None:
            continue
        cc = df.get("country_code")
        cc = cc if cc is not None else df.get("country")
        keep = pd.DataFrame({
            "title": df.get("title", "").fillna("").astype(str),
            "description": df["description"].fillna("").astype(str),
            "country": (cc.fillna("").astype(str) if cc is not None else ""),
            "department": (df["department"].fillna("").astype(str)
                           if "department" in df.columns else ""),
        })
        keep = keep[keep["title"].str.strip() != ""]
        if not keep.empty:
            frames.append(keep)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


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

    Uses ``fingerprint.leader_cluster`` (non-chaining canopy). The old connected-components
    ``threshold_cluster`` CHAINED at scale — 216k postings collapsed to ~1 cluster/country
    (97% of US in a single "Software Engineer" blob) because dense kNN neighbourhoods link
    transitively into one component. HDBSCAN is worse here: title embeddings aren't
    density-shaped, so it noise-labels ~94% and finds a handful of clusters. Leader
    clustering attaches each point to ONE nearest region and never bridges two → real
    granular roles the volume floor can select from. (Verified on 6k US: connected-comp 97%
    in one cluster; leader@0.70 → 1,111 clusters, largest 3%.)
    """
    import numpy as np

    model = _get_model()
    emb = model.encode(docs, batch_size=settings.embed_batch_size,
                       normalize_embeddings=True, show_progress_bar=False)
    arr = np.asarray(emb, dtype="float32")
    from backend.ml.fingerprint import leader_cluster
    return leader_cluster(arr, sim_threshold=0.70)


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

        import pandas as pd

        # cluster the WHOLE raw corpus for breadth (HF + ATS + CC); fall back to the single
        # common_crawl parquet if the multi-feed loader finds nothing. LLM-free either way.
        posts = _load_raw_corpus()
        if posts is None:
            path = _staging()
            if not path.exists():
                log.warning("role_derivation: no postings at %s — nothing to cluster", path)
                return {"postings": 0, "derived": 0, "written": False, "mode": "skipped"}
            posts = pd.read_parquet(path)
        if "country" not in posts.columns:
            posts["country"] = posts.get("country_code")
        if posts.empty:
            log.warning("role_derivation: postings parquet is empty")
            return {"postings": 0, "derived": 0, "written": False, "mode": "skipped"}

        # DECOUPLED: the corpus is a CONTRIBUTOR, never a boundary. The old
        # role_volume_floor (settings.role_volume_floor=200, per country) decided which
        # clusters were allowed to EXIST — it dropped every India/Singapore/Canada role
        # that couldn't muster 200 postings on a US-skewed corpus, i.e. the corpus decided
        # the catalog. That gate is RETIRED (see reports/CATALOG_DECOUPLE_PLAN.md). What
        # survives is only a tiny DENOISE threshold: a singleton "cluster" is embedding
        # noise, not a market signal, so we drop clusters below _DENOISE_MIN_MEMBERS. This
        # is a data-quality filter, NOT a market-volume boundary — a real 3-posting role in
        # India now enters the catalog where a 199-posting one used to be silently dropped.
        min_members = _DENOISE_MIN_MEMBERS
        mode = "embed" if _have_embeddings() else "lexical"
        rows = []
        derived = 0
        for code, grp in posts.groupby("country", dropna=True):
            titles = grp.get("title", "").fillna("").astype(str).tolist()
            if not titles:
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
                if len(mtitles) < min_members:      # denoise singletons, NOT a volume gate
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
            "min_members": min_members,     # denoise threshold (floor retired — decoupled)
            "written": True,
            "mode": mode,
            "out": str(out),
        }
        log.info("role_derivation: %s", summary)
        return summary
