"""Never-dead-end role resolver — the search box that always lands somewhere real.

The old ``list_roles`` did ``if not any(ql in h for h in hay): continue`` — a raw
substring filter that returns ``[]`` the instant a real title isn't a literal
substring of a name/skill. One dead-end on a searched role ("SDET", "RoR dev",
"k8s guy") silently breaks the whole premise. This replaces it with a three-tier
cascade that **structurally cannot return empty**:

  1. **exact / normalized** lookup over the alias graph (sub-ms; ~most queries),
  2. **fuzzy** — word-level: each word matches exactly, as a short inflection
     ("engineering" ~ "engineer") or as a typo ("scientsit"), and words count by how
     distinctive they are across the catalog ("android" says far more than "engineer"),
     so "Project Engineer" is not "Product Engineer" and a lone generic word can't
     claim a role,
  3. **embedding ANN** over canonical role centroids, reusing the MiniLM/5080 path
     ("person who keeps servers from falling over" → SRE). Optional + lazy: if
     sentence-transformers/faiss aren't importable the resolver still works — tiers
     1-2 always return the nearest node.

Confidence drives honest copy (brief): high → "Showing X"; medium → "Closest
match: X"; low → "We don't track 'X' exactly — nearest roles we cover". The lowest
tier still returns the top-k nearest, never a blank.

Not every surface says as much about a role. A role's own name outranks its generated
titles, which outrank the legacy curated seed and the corpus-materialized table, so a
role with its own catalog entry ("Business Analyst") is never swallowed by an older,
broader alias. Skills are weak: a skill-only hit can never read as high confidence.

Posting titles carry noise a role name never has ("Data Analyst Intern - Summer 2027",
"Werkstudent Data Engineering (m/w/d)"); it is stripped before matching, and each
" - " / " | " segment is tried on its own so a location or team suffix can't drown
the role. Classification targets the catalog: derived corpus clusters are skipped.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.marts import models as M
from backend.warehouse.taxonomy import _NONWORD, CURATED_ALIASES, normalize_surface

log = get_logger("app.resolver")

# confidence thresholds (blended fuzzy / cosine in [0,1])
_HIGH = 0.82
_MED = 0.55
_EMBED_MED = 0.55

# how much an exact hit on each kind of surface says about the role (see module doc)
_W_NAME, _W_TITLE, _W_FIELD, _W_CURATED, _W_MATERIALIZED = 1.0, 0.97, 0.93, 0.9, 0.88
_W_SKILL = 0.75

# posting-title noise: internship/season/contract/location-mode words, German posting markers
_POSTING_NOISE = re.compile(
    r"\b(?:internship program(?:me)?|internships?|interns?|co-?op|trainee(?:ship)?|apprentice(?:ship)?|"
    r"industrial placement|placement|year in industry|new grad|working student|"
    r"werkstudent(?:in)?|praktik(?:um|antin|ant)|abschlussarbeit|thesis|"
    r"summer|winter|spring|fall|autumn|20\d\d|remote|hybrid|on-?site|"
    r"full[- ]?time|part[- ]?time|\d+[- ]?months?|months?|"
    r"[mwfd]/[mwfd]/[mwfd])\b",
    re.IGNORECASE,
)
_SEGMENT_SPLIT = re.compile(r"\s+[-–—|]\s+|:\s+|,\s+|\s*[()\[\]]\s*")
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")

# words that carry no role meaning (English + German posting filler)
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "of", "for", "in", "at", "on", "to", "with", "or", "&",
    "und", "oder", "der", "die", "das", "den", "dem", "des", "im", "fuer", "fur", "ueber", "als",
    "bzw", "mit", "von", "zur", "zum", "bei", "bereich", "gn",
})
_W_UNKNOWN = 0.6      # weight of a word no role uses (a company, a place, an unrelated field)
_SPEC_FULL = 0.3      # summed word weight at which a surface counts as fully specific


_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})


def _fold(text: str) -> str:
    """Transliterate German letters before normalizing, which would otherwise split 'für' into
    'f r' (and that stray 'r' then matches the R skill)."""
    return (text or "").translate(_FOLD)


def _plain_norm(text: str) -> str:
    """Lowercase + punctuation only — no seniority stripping."""
    return " ".join(t for t in _NONWORD.sub(" ", _fold(text).lower()).split()
                    if any(ch.isalnum() for ch in t))


def _surface_norm(text: str) -> str:
    """normalize_surface, except a title that collapses to one meaningful word keeps its level
    words: 'Staff Engineer' stays 'staff engineer' and 'VP of Engineering' stays whole, instead of
    claiming every bare 'engineer' / 'engineering'."""
    n = normalize_surface(_fold(text))
    if len(_toks(n)) <= 1:
        full = _plain_norm(text)
        if len(_toks(full)) > len(_toks(n)):
            return full
    return n


def _query_variants(query: str) -> list[str]:
    """Normalized forms of a (possibly posting-style) title to try, best guess first:
    the whole cleaned title, the title without bracketed asides, then each segment."""
    raw = (query or "").strip()
    parts = [raw, _PARENS.sub(" ", raw)]
    if _SEGMENT_SPLIT.search(raw):
        parts += _SEGMENT_SPLIT.split(raw)
    out: list[str] = []
    for part in parts:
        cleaned = _POSTING_NOISE.sub(" ", _fold(part))
        for n in (normalize_surface(cleaned), _surface_norm(cleaned)):
            if len(n) >= 2 and n not in out:     # a 1-letter scrap of a cut-off title says nothing
                out.append(n)
    return out or [normalize_surface(raw)]


def _toks(norm: str) -> tuple[str, ...]:
    out: list[str] = []
    for t in re.split(r"[\s/]+", norm):
        if t and t not in _STOPWORDS and t not in out:
            out.append(t)
    return tuple(out)


# word endings that keep the meaning ('design' ~ 'designer'); anything else is a different word
# ('design' is not 'designops', 'program' is not 'programmer')
_INFLECTIONS = frozenset({"s", "es", "er", "ers", "ing", "ed", "al", "ist", "ists",
                          "ian", "ians", "ment", "or", "ors", "ics", "ion", "ions", "e", "en"})


def _compact(norm: str) -> str:
    """A surface's words run together, filler dropped: the exact key that ignores spacing."""
    return "".join(_toks(norm))


def _tok_match(q: str, v: str) -> float:
    """How well query word ``q`` stands for vocabulary word ``v``."""
    if q == v:
        return 1.0
    short, long_ = (q, v) if len(q) <= len(v) else (v, q)
    if len(short) >= 4 and long_.startswith(short) and long_[len(short):] in _INFLECTIONS:
        return 0.85                      # inflection: engineer/engineering, design/designer
    if len(short) >= 5 and q[0] == v[0] and len(long_) - len(short) <= 2:
        r = SequenceMatcher(None, q, v).ratio()
        if r >= 0.85:
            return 0.8 * r               # typo: counts, but less than the real word
    return 0.0


@dataclass
class RoleNode:
    id: str
    name: str
    family: dict
    blurb: str = ""
    skills: list[str] = field(default_factory=list)
    # normalized title surface -> weight (the best weight when a surface comes from several kinds)
    alias_norms: dict[str, float] = field(default_factory=dict)
    skill_norms: set[str] = field(default_factory=set)
    demand: int = 0     # tie-break: an ambiguous title goes to the broader, in-demand role

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
    short, long_ = (q, a) if len(q) <= len(a) else (a, q)
    if len(short) >= 4 and f" {short} " in f" {long_} ":   # whole words only: 'r' is not in 'cybersecurity'
        score = max(score, 0.8)
    return score


class RoleResolver:
    """In-memory resolver built from the served marts + the curated alias seed."""

    def __init__(self, nodes: list[RoleNode]):
        self.nodes = nodes
        self._by_id = {n.id: n for n in nodes}
        # exact index: normalized title surface -> role ids holding it at the strongest weight,
        # most in-demand first (an alias can still fan out across equals)
        best: dict[str, float] = {}
        for n in nodes:
            for norm, w in n.alias_norms.items():
                best[norm] = max(w, best.get(norm, 0.0))
        self._exact: dict[str, list[str]] = {}
        for n in sorted(nodes, key=lambda n: -n.demand):
            for norm, w in n.alias_norms.items():
                if w == best[norm]:
                    self._exact.setdefault(norm, []).append(n.id)
        # the same surfaces with spaces removed: "front end" = "frontend", "dev ops" = "devops",
        # "Software-Entwicklung" = "Softwareentwicklung"
        self._compact: dict[str, str] = {}
        for norm in self._exact:
            self._compact.setdefault(_compact(norm), norm)
        # flat (norm, role_id, weight) list for the fuzzy sweep: titles, then weak skills
        self._alias_pairs: list[tuple[str, str, float]] = [
            (norm, n.id, w) for n in nodes for norm, w in n.alias_norms.items()
        ] + [(norm, n.id, _W_SKILL) for n in nodes for norm in n.skill_norms]
        # word weights: inverse role frequency, so a word many roles share counts for little
        df: dict[str, int] = {}
        for n in nodes:
            for t in {t for norm in [*n.alias_norms, *n.skill_norms] for t in _toks(norm)}:
                df[t] = df.get(t, 0) + 1
        top = math.log(len(nodes) + 1)
        self._w = {t: max(math.log((len(nodes) + 1) / (d + 1)) / top, 0.02) for t, d in df.items()}
        # surfaces with their words + an inverted index word -> surfaces for the fuzzy sweep
        self._surfaces: list[tuple[str, str, float, tuple[str, ...], tuple[float, ...], float]] = []
        self._inv: dict[str, list[int]] = {}
        for norm, rid, w in self._alias_pairs:
            toks = _toks(norm)
            if not toks:
                continue
            ws = tuple(self._w[t] for t in toks)
            for t in toks:
                self._inv.setdefault(t, []).append(len(self._surfaces))
            self._surfaces.append((rid, norm, w, toks, ws, sum(ws)))
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

        roles = [r for r in roles if not r.id.startswith("derived:")]   # corpus clusters aren't catalog roles
        return cls._build([(r.id, r.name, {"id": r.family_id, "name": r.family_name, "hue": r.family_hue},
                            r.blurb or "") for r in roles], skills_by_role, materialized)

    @classmethod
    def from_catalog(cls) -> "RoleResolver":
        """Built from the generated catalog file alone (no marts): the full 346-role universe,
        for offline classification and for tests whose seeded marts hold only a few roles."""
        from backend.warehouse import role_content
        from backend.warehouse.seed import FAMILIES
        fams = {f["id"]: f for f in FAMILIES}
        cat = role_content._catalog_roles()
        rows = [(rid, r["name"], dict(fams.get(r["family"], {"id": r["family"], "name": r["family"], "hue": 0})),
                 r.get("blurb", "")) for rid, r in cat.items()]
        skills = {rid: [s["name"] for s in r.get("skills", [])] for rid, r in cat.items()}
        return cls._build(rows, skills, {})

    @classmethod
    def _build(cls, rows: list[tuple[str, str, dict, str]], skills_by_role: dict[str, list[str]],
               materialized: dict[str, list[str]]) -> "RoleResolver":
        from backend.warehouse import role_content
        generated = role_content.load("aliases")["roles"]
        try:
            demand = {rid: int(r.get("demand") or 0) for rid, r in role_content._catalog_roles().items()}
        except FileNotFoundError:
            demand = {}

        nodes: list[RoleNode] = []
        for rid, name, family, blurb in rows:
            gen = generated.get(rid, {})
            weighted = [(_W_MATERIALIZED, materialized.get(rid, [])),
                        (_W_CURATED, CURATED_ALIASES.get(rid, [])),
                        (_W_FIELD, gen.get("fields", [])),
                        (_W_TITLE, gen.get("titles", [])),
                        (_W_NAME, [name])]
            norms: dict[str, float] = {}
            for w, surfaces in weighted:
                for sf in surfaces:
                    n = _surface_norm(sf)
                    if n:
                        norms[n] = max(w, norms.get(n, 0.0))
            sk = skills_by_role.get(rid, [])
            skill_norms = {normalize_surface(x) for x in sk} - set(norms) - {""}
            nodes.append(RoleNode(id=rid, name=name, family=family, blurb=blurb, skills=sk,
                                  alias_norms=norms, skill_norms=skill_norms,
                                  demand=demand.get(rid, 0)))
        log.info("resolver built: %d roles, %d title surfaces (%d with generated aliases)",
                 len(nodes), sum(len(n.alias_norms) for n in nodes), len(generated))
        return cls(nodes)

    # ---- public API ------------------------------------------------------ #
    def resolve(self, query: str, limit: int = 8, use_embedding: bool = True) -> dict:
        """Resolve any string to ranked canonical nodes. Never returns empty."""
        q = (query or "").strip()
        if not q:
            return {"query": query, "confidence": "high", "tier": "all",
                    "message": "All roles", "matched": None,
                    "results": [self._payload(n, 1.0, "all", n.name) for n in self.nodes[:limit]]}
        variants = _query_variants(q)

        # TIER 1 — exact / normalized over every variant; the most specific hit wins, so
        # "Developer, DevOps" lands on DevOps rather than on the generic "developer"
        hits = [v if v in self._exact else self._compact[_compact(v)] for v in variants
                if v in self._exact or _compact(v) in self._compact]
        if hits:
            nq = max(hits, key=lambda v: sum(self._w.get(t, _W_UNKNOWN) for t in _toks(v)))
            ranked = [(self._by_id[i], 1.0, "exact", nq) for i in self._exact[nq]]
            return self._respond(query, ranked, "high", limit)

        # TIER 2 — weighted fuzzy sweep over every surface; the best variant wins
        ranked, top_fuzzy, nq = [], -1.0, variants[0]
        for v in variants:
            r = self._fuzzy_rank(v)
            if r and r[0][1] > top_fuzzy:
                ranked, top_fuzzy, nq = r, r[0][1], v
        top_fuzzy = max(top_fuzzy, 0.0)

        # TIER 3 — embedding ANN, only when fuzzy is weak (lazy model load)
        if use_embedding and top_fuzzy < _HIGH:
            emb = self._embed_rank(nq)
            if emb:
                e_node, e_score = emb
                if e_score >= _EMBED_MED and e_score > top_fuzzy:
                    ranked = [(e_node, e_score, "embedding", e_node.name)] + \
                             [r for r in ranked if r[0].id != e_node.id]
                    top_fuzzy = e_score

        conf = "high" if top_fuzzy >= _HIGH else "med" if top_fuzzy >= _MED else "low"
        return self._respond(query, ranked, conf, limit)

    def classify(self, title: str, alternatives: int = 2) -> dict:
        """One posting title -> its catalog role, confidence and runner-ups (for InternshipOS)."""
        if not (title or "").strip():
            return {"title": title, "role_id": None, "name": None, "family": None,
                    "confidence": "none", "score": 0.0, "via": None, "alternatives": []}
        r = self.resolve(title, limit=alternatives + 1, use_embedding=False)
        results = list(r["results"])
        if len(results) <= alternatives:      # an exact hit names one role; runner-ups come from fuzzy
            seen = {x["id"] for x in results}
            for node, sc, tier, via in self._fuzzy_rank(_query_variants(title)[0]):
                if len(results) > alternatives:
                    break
                if node.id not in seen:
                    results.append(self._payload(node, sc, tier, via))
        top = results[0]
        return {"title": title, "role_id": top["id"], "name": top["name"],
                "family": top["family"]["id"], "confidence": r["confidence"],
                "score": top["score"], "via": top["via"],
                "alternatives": [{"id": a["id"], "name": a["name"], "score": a["score"]}
                                 for a in results[1:]]}

    def typeahead(self, prefix: str, limit: int = 8) -> list[dict]:
        """Per-keystroke suggestions — prefix over aliases, then fuzzy backfill.

        Always non-empty for any non-trivial prefix (proves coverage before the
        user can be disappointed).
        """
        p = normalize_surface(prefix)
        if not p:
            return [self._payload(n, 1.0, "all", n.name) for n in self.nodes[:limit]]
        hits: dict[str, tuple[float, str]] = {}
        for norm, rid, w in self._alias_pairs:
            if norm.startswith(p) or any(tok.startswith(p) for tok in norm.split()):
                # earlier prefix match = stronger; full-string prefix beats token; titles beat skills
                sc = (1.0 if norm.startswith(p) else 0.9) * (1.0 if w > _W_SKILL else 0.8)
                if rid not in hits or sc > hits[rid][0]:
                    hits[rid] = (sc, norm)
        if not hits:  # backfill via fuzzy so type-ahead never blanks
            return self.resolve(prefix, limit)["results"]
        ranked = sorted(((self._by_id[r], sc, "prefix", via) for r, (sc, via) in hits.items()),
                        key=lambda t: t[1], reverse=True)
        return [self._payload(n, sc, tier, via) for n, sc, tier, via in ranked[:limit]]

    # ---- internals ------------------------------------------------------- #
    def _fuzzy_rank(self, nq: str) -> list[tuple]:
        """Roles scored by their best surface: how much of the surface the query covers
        (recall), how much of the query the surface explains (precision), how specific the
        surface is, times the surface kind's weight. Ties go to titles, then demand."""
        qt = self._join_compounds(_toks(nq))
        if not qt:
            return []
        m = {q: self._vocab_matches(q) for q in qt}
        qw = [self._w.get(q, _W_UNKNOWN) for q in qt]
        q_total = sum(qw)
        cand = {i for q in qt for v in m[q] for i in self._inv.get(v, ())}
        best: dict[str, tuple[float, str, float]] = {}
        for i in cand:
            rid, norm, w, toks, ws, w_total = self._surfaces[i]
            rec = sum(wt * max(m[q].get(t, 0.0) for q in qt) for t, wt in zip(toks, ws)) / w_total
            prec = sum(wq * max(m[q].get(t, 0.0) for t in toks) for q, wq in zip(qt, qw)) / q_total
            # partial cover of a surface is weak evidence ('industrial engineer' is not
            # 'industrial cybersecurity'), so recall counts more than linearly
            sc = rec ** 1.5 * (0.5 + 0.5 * prec) * min(1.0, w_total / _SPEC_FULL) * w
            if rid not in best or sc > best[rid][0]:
                best[rid] = (sc, norm, w)
        order = sorted(best.items(), reverse=True,
                       key=lambda kv: (kv[1][0], kv[1][2] > _W_SKILL, self._by_id[kv[0]].demand))
        return [(self._by_id[rid], sc, "fuzzy", via) for rid, (sc, via, _w) in order]

    def _join_compounds(self, toks: tuple[str, ...]) -> tuple[str, ...]:
        """Rejoin a split compound the vocabulary knows whole: 'software entwicklung' ->
        'softwareentwicklung', 'dev ops' -> 'devops'."""
        out, i = [], 0
        while i < len(toks):
            if i + 1 < len(toks) and toks[i] + toks[i + 1] in self._w:
                out.append(toks[i] + toks[i + 1])
                i += 2
            else:
                out.append(toks[i])
                i += 1
        return tuple(out)

    def _vocab_matches(self, q: str) -> dict[str, float]:
        """Vocabulary words query word ``q`` can stand for, with how well."""
        out = {}
        for v in self._w:
            sc = _tok_match(q, v)
            if sc:
                out[v] = sc
        return out

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
