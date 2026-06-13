"""Never-dead-end role resolver — the search box that always lands somewhere real.

The old ``list_roles`` did ``if not any(ql in h for h in hay): continue`` — a raw
substring filter that returns ``[]`` the instant a real title isn't a literal
substring of a name/skill. One dead-end on a searched role ("SDET", "RoR dev",
"k8s guy") silently breaks the whole premise. This replaces it with a three-tier
cascade that **structurally cannot return empty**:

  1. **exact / normalized** lookup over the alias graph (sub-ms; ~most queries),
  2. **fuzzy** — trigram + token-set + sequence-ratio blend (stdlib; typos,
     reorderings: "data scientsit", "engineer data"),
  3. **embedding ANN** over canonical role centroids, reusing the MiniLM/5080 path
     ("person who keeps servers from falling over" → SRE). Optional + lazy: if
     sentence-transformers/faiss aren't importable the resolver still works — tiers
     1-2 always return the nearest node.

Confidence drives honest copy (brief): high → "Showing X"; medium → "Closest
match: X"; low → "We don't track 'X' exactly — nearest roles we cover". The lowest
tier still returns the top-k nearest, never a blank.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.marts import models as M
from backend.warehouse.taxonomy import CURATED_ALIASES, normalize_surface

log = get_logger("app.resolver")

# confidence thresholds (blended fuzzy / cosine in [0,1])
_HIGH = 0.82
_MED = 0.55
_EMBED_MED = 0.55


@dataclass
class RoleNode:
    id: str
    name: str
    family: dict
    blurb: str = ""
    skills: list[str] = field(default_factory=list)
    alias_norms: set[str] = field(default_factory=set)

    @property
    def document(self) -> str:
        """Text fed to the embedding tier — name + blurb + a few skills."""
        return f"{self.name}. {self.blurb} {' '.join(self.skills[:6])}".strip()


def _trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _sim(q: str, a: str) -> float:
    """Forgiving similarity in [0,1] — max of three orthogonal signals.

    Sequence-ratio catches typos; trigram Jaccard catches partial overlap; token-set
    Jaccard catches word reordering. A containment bump handles partial queries
    ("react" → "react developer").
    """
    if not q or not a:
        return 0.0
    dr = SequenceMatcher(None, q, a).ratio()
    tq, ta = _trigrams(q), _trigrams(a)
    tj = len(tq & ta) / len(tq | ta) if (tq | ta) else 0.0
    sq, sa = set(q.split()), set(a.split())
    to = len(sq & sa) / len(sq | sa) if (sq | sa) else 0.0
    score = max(dr, tj, to)
    if len(q) >= 4 and (q in a or a in q):
        score = max(score, 0.8)
    return score


class RoleResolver:
    """In-memory resolver built from the served marts + the curated alias seed."""

    def __init__(self, nodes: list[RoleNode]):
        self.nodes = nodes
        self._by_id = {n.id: n for n in nodes}
        # exact index: normalized surface -> [role_id, ...] (an alias can fan out)
        self._exact: dict[str, list[str]] = {}
        for n in nodes:
            for norm in n.alias_norms:
                self._exact.setdefault(norm, [])
                if n.id not in self._exact[norm]:
                    self._exact[norm].append(n.id)
        # flat (norm, role_id) list for the fuzzy sweep
        self._alias_pairs: list[tuple[str, str]] = [
            (norm, n.id) for n in nodes for norm in n.alias_norms
        ]
        self._embed_index = None  # built lazily on first weak query
        self._embed_failed = False

    # ---- construction ---------------------------------------------------- #
    @classmethod
    def from_marts(cls, db: Session) -> "RoleResolver":
        roles = list(db.scalars(select(M.MartRole).order_by(M.MartRole.ord)))
        skills_by_role: dict[str, list[str]] = {}
        for s in db.scalars(select(M.MartRoleSkill).order_by(M.MartRoleSkill.role_id, M.MartRoleSkill.ord)):
            skills_by_role.setdefault(s.role_id, []).append(s.name)

        # optional: a materialized alias table (mart_role_alias) if it's been built
        materialized: dict[str, list[str]] = {}
        try:
            rows = db.execute(
                __import__("sqlalchemy").text("SELECT role_id, surface FROM mart_role_alias")
            ).fetchall()
            for rid, surface in rows:
                materialized.setdefault(rid, []).append(surface)
        except Exception:  # noqa: BLE001 — table simply may not exist yet
            pass

        nodes: list[RoleNode] = []
        for r in roles:
            surfaces = [r.name, *CURATED_ALIASES.get(r.id, []), *materialized.get(r.id, [])]
            sk = skills_by_role.get(r.id, [])
            surfaces += sk  # skills are weak aliases
            norms = {normalize_surface(s) for s in surfaces}
            norms.discard("")
            nodes.append(RoleNode(
                id=r.id, name=r.name,
                family={"id": r.family_id, "name": r.family_name, "hue": r.family_hue},
                blurb=r.blurb or "", skills=sk, alias_norms=norms,
            ))
        log.info("resolver built: %d roles, %d alias norms", len(nodes),
                 sum(len(n.alias_norms) for n in nodes))
        return cls(nodes)

    # ---- public API ------------------------------------------------------ #
    def resolve(self, query: str, limit: int = 8) -> dict:
        """Resolve any string to ranked canonical nodes. Never returns empty."""
        q = (query or "").strip()
        if not q:
            return {"query": query, "confidence": "high", "tier": "all",
                    "message": "All roles", "matched": None,
                    "results": [self._payload(n, 1.0, "all", n.name) for n in self.nodes[:limit]]}
        nq = normalize_surface(q)

        # TIER 1 — exact / normalized
        if nq in self._exact:
            ids = self._exact[nq]
            ranked = [(self._by_id[i], 1.0, "exact", nq) for i in ids]
            return self._respond(query, ranked, "high", limit)

        # TIER 2 — fuzzy sweep over every alias, best score per role
        best: dict[str, tuple[float, str]] = {}
        for norm, rid in self._alias_pairs:
            sc = _sim(nq, norm)
            if rid not in best or sc > best[rid][0]:
                best[rid] = (sc, norm)
        ranked = sorted(
            ((self._by_id[r], sc, "fuzzy", via) for r, (sc, via) in best.items()),
            key=lambda t: t[1], reverse=True,
        )
        top_fuzzy = ranked[0][1] if ranked else 0.0

        # TIER 3 — embedding ANN, only when fuzzy is weak (lazy model load)
        if top_fuzzy < _HIGH:
            emb = self._embed_rank(nq)
            if emb:
                e_node, e_score = emb
                if e_score >= _EMBED_MED and e_score > top_fuzzy:
                    ranked = [(e_node, e_score, "embedding", e_node.name)] + \
                             [r for r in ranked if r[0].id != e_node.id]
                    top_fuzzy = e_score

        conf = "high" if top_fuzzy >= _HIGH else "med" if top_fuzzy >= _MED else "low"
        return self._respond(query, ranked, conf, limit)

    def typeahead(self, prefix: str, limit: int = 8) -> list[dict]:
        """Per-keystroke suggestions — prefix over aliases, then fuzzy backfill.

        Always non-empty for any non-trivial prefix (proves coverage before the
        user can be disappointed).
        """
        p = normalize_surface(prefix)
        if not p:
            return [self._payload(n, 1.0, "all", n.name) for n in self.nodes[:limit]]
        hits: dict[str, tuple[float, str]] = {}
        for norm, rid in self._alias_pairs:
            if norm.startswith(p) or any(tok.startswith(p) for tok in norm.split()):
                # earlier prefix match = stronger; full-string prefix beats token
                sc = 1.0 if norm.startswith(p) else 0.9
                if rid not in hits or sc > hits[rid][0]:
                    hits[rid] = (sc, norm)
        if not hits:  # backfill via fuzzy so type-ahead never blanks
            return self.resolve(prefix, limit)["results"]
        ranked = sorted(((self._by_id[r], sc, "prefix", via) for r, (sc, via) in hits.items()),
                        key=lambda t: t[1], reverse=True)
        return [self._payload(n, sc, tier, via) for n, sc, tier, via in ranked[:limit]]

    # ---- internals ------------------------------------------------------- #
    def _embed_rank(self, nq: str):
        """Top-1 canonical node by embedding cosine. Returns None if unavailable."""
        if self._embed_failed:
            return None
        if self._embed_index is None and not self._build_embed_index():
            return None
        try:
            import numpy as np
            model, index = self._embed_index
            v = model.encode([nq], normalize_embeddings=True, show_progress_bar=False)
            sims, idx = index.search(np.asarray(v, dtype="float32"), 1)
            i = int(idx[0][0])
            return self.nodes[i], float(sims[0][0])
        except Exception as e:  # noqa: BLE001
            log.warning("embedding tier failed mid-query (%s) — degrading to fuzzy", e)
            self._embed_failed = True
            return None

    def _build_embed_index(self) -> bool:
        """Lazily build the FAISS centroid index, mirroring skill_norm's pattern."""
        try:
            import faiss
            import numpy as np
            from sentence_transformers import SentenceTransformer

            from backend.core.config import settings
            model = SentenceTransformer(settings.embed_model, device=settings.ml_device)
            docs = [n.document for n in self.nodes]
            emb = model.encode(docs, batch_size=settings.embed_batch_size,
                               normalize_embeddings=True, show_progress_bar=False)
            index = faiss.IndexFlatIP(int(emb.shape[1]))
            index.add(np.asarray(emb, dtype="float32"))
            self._embed_index = (model, index)
            log.info("resolver embedding tier ready (%d centroids, model=%s)",
                     len(docs), settings.embed_model)
            return True
        except Exception as e:  # noqa: BLE001 — GPU/lib absent: fuzzy still guarantees non-empty
            log.info("embedding tier unavailable (%s) — resolver uses exact+fuzzy only", e)
            self._embed_failed = True
            return False

    def _payload(self, node: RoleNode, score: float, tier: str, via: str) -> dict:
        return {"id": node.id, "name": node.name, "family": node.family,
                "blurb": node.blurb, "score": round(float(score), 3),
                "tier": tier, "via": via}

    def _respond(self, query: str, ranked: list, conf: str, limit: int) -> dict:
        results = [self._payload(n, sc, tier, via) for n, sc, tier, via in ranked[:limit]]
        if not results:  # last-resort guarantee — should never trigger
            results = [self._payload(n, 0.0, "fallback", n.name) for n in self.nodes[:limit]]
            conf = "low"
        top = results[0]
        if conf == "high":
            msg = f"Showing {top['name']}"
        elif conf == "med":
            msg = f"Closest match: {top['name']}"
        else:
            msg = f"We don’t track “{query}” exactly — nearest roles we cover"
        return {"query": query, "confidence": conf, "tier": top["tier"],
                "message": msg, "matched": top, "results": results}


# module-level cache (marts are static during serving; rebuild via invalidate())
_RESOLVER: RoleResolver | None = None


def get_resolver(db: Session) -> RoleResolver:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = RoleResolver.from_marts(db)
    return _RESOLVER


def invalidate_resolver() -> None:
    global _RESOLVER
    _RESOLVER = None
