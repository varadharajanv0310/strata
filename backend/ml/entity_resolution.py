"""Entity resolution / dedup (brief §4, GPU + blocking).

The same role is posted across many boards/countries; counts must not inflate.
We (1) resolve employer name variants to a canonical **employer id**, and (2) flag
near-duplicate postings within an (employer, country) block so demand counts use
*unique* postings only.

Writes ONE staging table the warehouse build consumes:
  * ``normalized/posting_dedup.parquet`` — (posting_id, employer_id, country,
    title, is_unique, dup_group)

Employer is used **only as an in-memory dedup block key** — strata is ROLES-only, so
no company/employer registry is ever persisted (the old ``employers.parquet`` artifact
was removed; companies are never an entity here).

Two paths: a sentence-transformer title-similarity path (GPU) and a deterministic
normalized-title path (fallback). Both produce REAL dedup decisions from the real
postings, so the stage always writes.
"""
from __future__ import annotations

import re

from backend.core.config import settings
from backend.core.logging import get_logger, stage_timer

log = get_logger("ml.entity_resolution")

POSTINGS = "common_crawl/postings.parquet"
DEDUP_OUT = "normalized/posting_dedup.parquet"
SIM_THRESHOLD = 0.86  # cosine on title embeddings ⇒ same posting


def _have_embeddings() -> bool:
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("entity_resolution: embedding stack unavailable (%s) — deterministic fallback", e)
        return False


def _staging():
    return settings.staging_dir / POSTINGS


def _out(rel: str):
    p = settings.staging_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_NONWORD = re.compile(r"[^a-z0-9]+")
_EMP_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|gmbh|corp|co|company|plc|pvt|private|technologies|labs|group)\b")


def _norm_employer(name: str) -> str:
    s = (name or "").lower()
    s = _EMP_SUFFIX.sub(" ", s)
    s = _NONWORD.sub(" ", s).strip()
    return " ".join(s.split())


def _norm_title(title: str) -> str:
    s = (title or "").lower()
    s = _NONWORD.sub(" ", s)
    return " ".join(s.split())


_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(settings.embed_model, device=settings.ml_device)
    return _MODEL


def _dedup_block_embed(titles: list[str]) -> list[int]:
    """Return a dup-group label per title using title-embedding cosine + union-find."""
    import numpy as np

    model = _get_model()
    emb = model.encode(titles, batch_size=settings.embed_batch_size,
                       normalize_embeddings=True, show_progress_bar=False)
    emb = np.asarray(emb, dtype="float32")
    n = len(titles)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    sim = emb @ emb.T
    for a in range(n):
        for b in range(a + 1, n):
            if sim[a, b] >= SIM_THRESHOLD:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    return [find(i) for i in range(n)]


def _dedup_block_lexical(titles: list[str]) -> list[int]:
    """Fallback: identical normalized title ⇒ same group."""
    groups: dict[str, int] = {}
    labels = []
    for t in titles:
        key = _norm_title(t)
        labels.append(groups.setdefault(key, len(groups)))
    return labels


def run() -> dict:
    with stage_timer(log, "ml.entity_resolution"):
        path = _staging()
        if not path.exists():
            log.warning("entity_resolution: no postings at %s — nothing to dedup", path)
            return {"postings": 0, "unique": 0, "employer_blocks": 0, "written": False, "mode": "skipped"}

        import pandas as pd

        posts = pd.read_parquet(path).reset_index(drop=True)
        if "country" not in posts.columns:
            posts["country"] = posts.get("country_code")
        if posts.empty:
            log.warning("entity_resolution: postings parquet is empty")
            return {"postings": 0, "unique": 0, "employer_blocks": 0, "written": False, "mode": "skipped"}

        # ---- employer resolution: variants → canonical id, IN-MEMORY ONLY.
        #      This is purely the dedup BLOCK KEY (collapse a company's reposts);
        #      strata is roles-only, so nothing about the employer is persisted as an
        #      entity (no canonical_name / size / registry — that was the old creep). ----
        from backend.warehouse.build import slug
        posts["_emp_norm"] = posts.get("employer", "").fillna("").map(_norm_employer)
        emp_id_of = {norm: ("emp:unknown" if not norm else "emp:" + slug(norm))
                     for norm in posts["_emp_norm"].unique()}
        posts["employer_id"] = posts["_emp_norm"].map(emp_id_of)

        # ---- posting dedup within (employer_id, country) blocks ----
        mode = "embed" if _have_embeddings() else "lexical"
        posts["is_unique"] = True
        posts["dup_group"] = -1
        gid_offset = 0
        for (eid, country), block in posts.groupby(["employer_id", "country"], dropna=False):
            titles = block.get("title", "").fillna("").astype(str).tolist()
            if len(titles) == 1:
                posts.loc[block.index, "dup_group"] = gid_offset
                gid_offset += 1
                continue
            try:
                labels = (_dedup_block_embed(titles) if mode == "embed"
                          else _dedup_block_lexical(titles))
            except Exception as e:  # noqa: BLE001
                log.warning("entity_resolution: embed dedup failed (%s) — lexical", e)
                mode = "lexical"
                labels = _dedup_block_lexical(titles)
            seen: dict[int, int] = {}
            for pos, (ridx, lbl) in enumerate(zip(block.index, labels)):
                gid = seen.setdefault(lbl, gid_offset + lbl)
                posts.loc[ridx, "dup_group"] = gid
                posts.loc[ridx, "is_unique"] = lbl not in [l for l in labels[:pos]]
            gid_offset += (max(labels) + 1) if labels else 0

        dedup = posts.reset_index().rename(columns={"index": "posting_id"})[
            ["posting_id", "employer_id", "country", "title", "is_unique", "dup_group"]
        ]
        dedup.to_parquet(_out(DEDUP_OUT), index=False)

        n_unique = int(dedup["is_unique"].sum())
        summary = {
            "postings": len(dedup),
            "unique": n_unique,
            "duplicates": len(dedup) - n_unique,
            "employer_blocks": len(emp_id_of),   # in-memory dedup blocks, not a registry
            "written": True,
            "mode": mode,
        }
        log.info("entity_resolution: %s", summary)
        return summary
