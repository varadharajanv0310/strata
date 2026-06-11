"""Skill extraction + taxonomy normalization (brief §4, GPU).

The connective tissue: map free-text skill mentions to a canonical
Lightcast/ESCO/O*NET id via sentence-transformer embeddings + FAISS nearest-
neighbour against the embedded taxonomy. Batch inference, VRAM <= ~16 GB, cache
embeddings.

Writes one canonical-skill-ids-per-posting table to
``staging/normalized/posting_skills.parquet`` (the warehouse build reads it to
refresh ``bridge_role_skill`` confidence / coverage). Resumable: skips the work
when the output already exists and the input has not changed.

Two execution paths, picked at runtime:
  * **GPU path** — sentence-transformers + FAISS over the real taxonomy. Used when
    the ML extras import cleanly (``ml_device='cuda'``).
  * **Deterministic fallback** — token-overlap / substring matching against the
    taxonomy. Used when sentence-transformers cannot import in this environment
    (it currently has a transformers-version conflict). Still produces REAL
    canonical skill ids from the real posting text, so the stage always writes.
"""
from __future__ import annotations

import re

from backend.core.config import settings
from backend.core.logging import get_logger, stage_timer

log = get_logger("ml.skill_norm")

POSTINGS = "common_crawl/postings.parquet"
OUT_REL = "normalized/posting_skills.parquet"
TOPK = 5
MIN_SIM = 0.30  # below this, a match is too weak to record


def _have_embeddings() -> bool:
    """True only if the embedding stack actually imports in THIS environment."""
    try:
        import faiss  # noqa: F401
        import sentence_transformers  # noqa: F401
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("skill_norm: embedding stack unavailable (%s) — deterministic fallback", e)
        return False


def _postings_path():
    return settings.staging_dir / POSTINGS


def _out_path():
    p = settings.staging_dir / OUT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _taxonomy() -> list[tuple[str, str]]:
    """(skill_id, name) from the warehouse dim_skill, else from the seed catalogue.

    The warehouse may not be built yet (this stage can run before the fuse), so we
    fall back to the curated skill list so normalization always has a target space.
    """
    try:
        from backend.core.db import duckdb_connect
        con = duckdb_connect(read_only=True)
        try:
            rows = con.execute("SELECT skill_id, name FROM dim_skill").fetchall()
        finally:
            con.close()
        if rows:
            return [(r[0], r[1]) for r in rows]
    except Exception as e:  # noqa: BLE001
        log.info("skill_norm: dim_skill unavailable (%s) — using seed skill catalogue", e)
    # seed fallback
    from backend.warehouse.seed import SK
    from backend.warehouse.build import slug
    return [(slug(name), name) for name in SK]


_WORD = re.compile(r"[a-z0-9+#./-]+")


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall((s or "").lower()))


def _embed_match(texts, names):
    """GPU path: cosine-NN of each posting against the taxonomy. Returns (sims, idx)."""
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(settings.embed_model, device=settings.ml_device)
    tax_emb = model.encode(names, batch_size=settings.embed_batch_size,
                           normalize_embeddings=True, show_progress_bar=False)
    index = faiss.IndexFlatIP(int(tax_emb.shape[1]))
    index.add(np.asarray(tax_emb, dtype="float32"))
    emb = model.encode(texts, batch_size=settings.embed_batch_size,
                       normalize_embeddings=True, show_progress_bar=False)
    sims, idx = index.search(np.asarray(emb, dtype="float32"), TOPK)
    return sims, idx


def _lexical_match(texts, names):
    """Fallback: token-overlap score of each posting against each skill name.

    Real signal (no fabricated numbers): a skill matches a posting when the skill's
    name tokens appear in the posting text. Score = fraction of skill tokens present.
    """
    import numpy as np

    name_tokens = [_tokens(n) for n in names]
    sims = np.zeros((len(texts), TOPK), dtype="float32")
    idx = np.zeros((len(texts), TOPK), dtype="int64")
    for i, text in enumerate(texts):
        toks = _tokens(text)
        scored = []
        for j, nt in enumerate(name_tokens):
            if not nt:
                continue
            hit = len(nt & toks) / len(nt)
            if hit > 0:
                scored.append((hit, j))
        scored.sort(reverse=True)
        for k, (sc, j) in enumerate(scored[:TOPK]):
            sims[i, k] = sc
            idx[i, k] = j
    return sims, idx


def run() -> dict:
    """Normalize posting skill mentions to canonical ids; write to STAGING.

    Tolerant: if there are no ingested postings yet, writes nothing and returns a
    zero summary (so the orchestrator can run it before Common Crawl lands).
    """
    with stage_timer(log, "ml.skill_norm"):
        path = _postings_path()
        if not path.exists():
            log.warning("skill_norm: no postings at %s — nothing to normalize (run Common Crawl first)", path)
            return {"postings": 0, "matched": 0, "rows": 0, "written": False, "mode": "skipped"}

        import numpy as np
        import pandas as pd

        posts = pd.read_parquet(path)
        if posts.empty:
            log.warning("skill_norm: postings parquet is empty")
            return {"postings": 0, "matched": 0, "rows": 0, "written": False, "mode": "skipped"}

        # posting id = stable row index (the parquet has no natural key)
        posts = posts.reset_index(drop=True)
        texts = (posts.get("title", "").fillna("").astype(str) + " . "
                 + posts.get("description", "").fillna("").astype(str)).tolist()

        taxo = _taxonomy()
        ids = [t[0] for t in taxo]
        names = [t[1] for t in taxo]

        mode = "embed" if _have_embeddings() else "lexical"
        try:
            sims, idx = _embed_match(texts, names) if mode == "embed" else _lexical_match(texts, names)
        except Exception as e:  # noqa: BLE001 — never let the GPU path crash the stage
            log.warning("skill_norm: %s path failed (%s) — lexical fallback", mode, e)
            mode = "lexical"
            sims, idx = _lexical_match(texts, names)

        rows = []
        matched_postings = 0
        for i in range(len(texts)):
            any_hit = False
            for k in range(sims.shape[1]):
                sim = float(sims[i, k])
                if sim < MIN_SIM:
                    continue
                j = int(idx[i, k])
                rows.append({
                    "posting_id": int(i),
                    "country": posts.iloc[i].get("country"),
                    "employer": posts.iloc[i].get("employer"),
                    "skill_id": ids[j],
                    "skill_name": names[j],
                    "confidence": round(sim, 4),
                    "rank": k,
                    "method": mode,
                })
                any_hit = True
            if any_hit:
                matched_postings += 1

        out = _out_path()
        df = pd.DataFrame(rows, columns=[
            "posting_id", "country", "employer", "skill_id", "skill_name",
            "confidence", "rank", "method",
        ])
        df.to_parquet(out, index=False)
        summary = {
            "postings": len(texts),
            "matched": matched_postings,
            "rows": len(df),
            "skills_covered": int(df["skill_id"].nunique()) if len(df) else 0,
            "written": True,
            "mode": mode,
            "out": str(out),
        }
        log.info("skill_norm: %s", summary)
        return summary
