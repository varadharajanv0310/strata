"""Fingerprint clustering + cross-board dedup.

Two jobs from the brainstorm, at scale:

  1. **Cluster fingerprints, not titles.** The unit of embedding is a *composite
     document* — normalized title ⊕ skill-bag ⊕ department ⊕ salary-band — so "Data
     Engineer" (Spark/Airflow) and "Analytics Engineer" (dbt/Looker) stay distinct
     despite near-identical titles, and "MTS" (zero title signal) still lands via its
     skills. The MiniLM embedding is **preprocessing**, not the product: postings are
     embedded on the RTX 5080 (reusing role_derivation's MiniLM path), then grouped by
     a SUB-QUADRATIC threshold clusterer (FAISS kNN graph → connected components at the
     cosine threshold). Dense clusters become candidate role nodes.

  2. **Cross-board dedup so "millions" is honest.** Block on (employer, country) →
     MinHash signatures over shingles of (title+description) → collapse near-dups.
     The dup-group keeps N source badges, not a deletion.

The composite-document builder is deterministic + testable. The clustering + MinHash
dedup are implemented here and proven on a bounded sample before any scale run.

Scale note: the original ``AgglomerativeClustering(distance_threshold=…, linkage=
'average')`` materialized an O(n²) distance matrix and OOM'd past ~30-50k rows, which
capped the whole pipeline. ``threshold_cluster`` below preserves the same no-fixed-k,
cosine-distance-threshold semantics but builds only a sparse kNN graph (FAISS), so it
scales to the corpus. sklearn Agglomerative is kept ONLY as a small-n exact fallback
when FAISS is absent (guarded by a row cap).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from backend.core.logging import get_logger
from backend.warehouse.taxonomy import normalize_surface

log = get_logger("ml.fingerprint")

_WS = re.compile(r"\s+")


def salary_band(value: float | None) -> str:
    """Coarse salary band token (so pay enters the fingerprint without leaking exact $)."""
    if not value or value <= 0:
        return "sal-unknown"
    for hi, tag in ((40_000, "sal-0"), (75_000, "sal-1"), (120_000, "sal-2"),
                    (180_000, "sal-3"), (260_000, "sal-4")):
        if value < hi:
            return tag
    return "sal-5"


@dataclass
class FingerprintInput:
    title: str
    skills: list[str]
    department: str = ""
    salary: float | None = None


def composite_document(fp: FingerprintInput) -> str:
    """Build the text fed to the embedder. Implemented (deterministic, testable).

    title (normalized, seniority-stripped) + top skills + department + salary band.
    Two reqs merge only if they agree on *what the job does and what it pays*.
    """
    title = normalize_surface(fp.title)
    skills = " ".join(sorted({s.strip().lower() for s in fp.skills if s.strip()})[:12])
    dept = normalize_surface(fp.department)
    band = salary_band(fp.salary)
    doc = f"{title} || {skills} || {dept} || {band}"
    return _WS.sub(" ", doc).strip()


# --------------------------------------------------------------------------- #
#  skill extraction (lightweight, deterministic) — builds the skill-bag        #
# --------------------------------------------------------------------------- #
# tech-skill vocabulary; word-boundary matched against title+description. Not a
# taxonomy (skill_norm/FAISS is the production enrichment) — enough to make the
# fingerprint discriminative (Spark/Airflow vs dbt/Looker).
SKILL_VOCAB = [
    "python", "java", "javascript", "typescript", "golang", "rust", "c++", "c#",
    "ruby", "rails", "php", "scala", "kotlin", "swift", "objective-c", "elixir",
    "react", "angular", "vue", "svelte", "next.js", "node", "node.js", "django",
    "flask", "fastapi", "spring", "spring boot", ".net", "express", "graphql", "rest",
    "react native", "flutter", "android", "ios",
    "sql", "postgres", "postgresql", "mysql", "mongodb", "redis", "cassandra",
    "elasticsearch", "snowflake", "bigquery", "redshift", "databricks", "dbt",
    "spark", "hadoop", "kafka", "airflow", "flink", "hive", "presto", "looker",
    "tableau", "power bi", "etl",
    "aws", "azure", "gcp", "kubernetes", "k8s", "docker", "terraform", "ansible",
    "jenkins", "ci/cd", "prometheus", "grafana", "helm", "linux", "bash",
    "pytorch", "tensorflow", "keras", "scikit-learn", "pandas", "numpy", "mlflow",
    "machine learning", "deep learning", "nlp", "llm", "computer vision", "huggingface",
    "transformers", "langchain", "rag", "generative ai",
    "figma", "sketch", "ux", "ui", "user research", "design systems",
    "security", "penetration testing", "siem", "soc", "iam", "owasp",
    "selenium", "cypress", "playwright", "junit", "pytest", "qa automation",
    "git", "jira", "agile", "scrum", "microservices", "api", "grpc", "websocket",
    "embedded", "firmware", "fpga", "verilog", "rtos",
]
# longest-first so "spring boot" matches before "spring", "node.js" before "node"
_SKILL_PATTERNS = sorted(SKILL_VOCAB, key=len, reverse=True)
_SKILL_RE = re.compile(
    "|".join(rf"(?<![\w]){re.escape(s)}(?![\w])" for s in _SKILL_PATTERNS),
    re.IGNORECASE,
)


def extract_skills(text: str | None, limit: int = 12) -> list[str]:
    """Keyword-extract tech skills from a posting's title+description (dedup, capped)."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for m in _SKILL_RE.finditer(text):
        s = m.group(0).lower()
        if s not in seen:
            seen.add(s)
            found.append(s)
        if len(found) >= limit:
            break
    return found


# --------------------------------------------------------------------------- #
#  sub-quadratic threshold clusterer (FAISS kNN graph → connected components)   #
# --------------------------------------------------------------------------- #
# Default exact-fallback row cap: above this, an O(n²) agglomerative distance
# matrix is too large to materialize, so we refuse the exact path and require the
# FAISS graph (never silently OOM).
_AGGLO_MAX_N = 12_000


def threshold_cluster(emb, *, distance_threshold: float = 0.32, knn: int = 32) -> list[int]:
    """Group L2-normalized embeddings into clusters at a cosine-distance THRESHOLD,
    with NO fixed k — the same semantics the old AgglomerativeClustering carried, but
    sub-quadratic in memory.

    Strategy (preferred): build a sparse k-nearest-neighbour graph with FAISS
    (inner-product on normalized vectors = cosine similarity), keep only edges with
    cosine distance < ``distance_threshold`` (i.e. similarity > 1 - threshold), then
    take connected components via union-find. This touches only ``n·knn`` similarities
    instead of the full n² matrix, so it scales to the corpus.

    Fallback (small n only): sklearn AgglomerativeClustering with the identical
    distance_threshold / cosine / average-linkage settings, guarded by ``_AGGLO_MAX_N``
    so it can never materialize an OOM-sized distance matrix. Determinism: both paths
    are order-deterministic for a fixed embedding matrix.
    """
    import numpy as np

    x = np.ascontiguousarray(np.asarray(emb, dtype="float32"))
    n = x.shape[0]
    if n == 0:
        return []
    if n == 1:
        return [0]
    sim_floor = 1.0 - float(distance_threshold)

    try:
        import faiss

        # renormalize defensively so inner product == cosine similarity
        faiss.normalize_L2(x)
        index = faiss.IndexFlatIP(x.shape[1])
        index.add(x)
        k = min(knn + 1, n)  # +1: the first neighbour is the point itself
        sims, nbrs = index.search(x, k)

        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

        for i in range(n):
            for j_pos in range(k):
                j = int(nbrs[i, j_pos])
                if j == i or j < 0:
                    continue
                if float(sims[i, j_pos]) >= sim_floor:
                    union(i, j)

        # relabel roots to contiguous 0..m-1 in first-seen order (deterministic)
        remap: dict[int, int] = {}
        return [remap.setdefault(find(i), len(remap)) for i in range(n)]
    except ImportError:
        if n > _AGGLO_MAX_N:
            raise RuntimeError(
                f"threshold_cluster: FAISS unavailable and n={n} exceeds the exact "
                f"agglomerative cap ({_AGGLO_MAX_N}); install faiss for scale runs"
            )
        from sklearn.cluster import AgglomerativeClustering

        labels = AgglomerativeClustering(
            n_clusters=None, distance_threshold=distance_threshold,
            metric="cosine", linkage="average",
        ).fit_predict(x)
        return [int(v) for v in labels]


# --------------------------------------------------------------------------- #
#  clustering — composite fingerprint, embedded on the GPU                      #
# --------------------------------------------------------------------------- #
def cluster_fingerprints(
    postings: list[dict],
    *,
    min_cluster_size: int = 8,
    distance_threshold: float = 0.32,
) -> dict:
    """Cluster postings by composite fingerprint (title⊕skills⊕dept⊕band) on the 5080.

    ``postings`` = dicts with title / description / department / salary_* / country /
    arm. Returns {mode, device, n_postings, n_clusters, n_noise, clusters[...]}. Each
    cluster: label, size, countries, arms, sample_titles, top_skills. Reuses
    role_derivation's MiniLM model (embed path) + canon-title labeller.
    """
    if not postings:
        return {"mode": "empty", "n_postings": 0, "n_clusters": 0, "clusters": []}

    docs, skills_per = [], []
    for p in postings:
        sk = extract_skills(f"{p.get('title', '')}  {p.get('description', '')}")
        skills_per.append(sk)
        docs.append(composite_document(FingerprintInput(
            title=p.get("title", ""), skills=sk, department=p.get("department", ""),
            salary=p.get("salary_max") or p.get("salary_min"))))

    try:
        import numpy as np

        from backend.core.config import settings
        from backend.ml.role_derivation import _canon_title, _get_model

        model = _get_model()
        device = str(getattr(model, "device", "?"))
        emb = model.encode(docs, batch_size=settings.embed_batch_size,
                           normalize_embeddings=True, show_progress_bar=False)
        # sub-quadratic threshold clustering (FAISS kNN graph → connected components),
        # same no-fixed-k cosine-threshold semantics as the old agglomerative path but
        # without the O(n²) distance matrix that capped scale.
        labels = threshold_cluster(
            np.asarray(emb, dtype="float32"), distance_threshold=distance_threshold)
        mode = "embed"
    except Exception as e:  # noqa: BLE001 — never silently sell a lexical run as GPU
        log.warning("fingerprint: embed path unavailable (%s) — LEXICAL fallback", e)
        from backend.ml.role_derivation import _canon_title
        groups: dict[str, int] = {}
        labels = [groups.setdefault(_canon_title(p.get("title", "")), len(groups)) for p in postings]
        device, mode = "cpu-lexical", "lexical"

    members: dict[int, list[int]] = defaultdict(list)
    for i, lbl in enumerate(labels):
        members[int(lbl)].append(i)

    clusters, noise = [], 0
    for lbl, idxs in members.items():
        if len(idxs) < min_cluster_size:
            noise += len(idxs)
            continue
        titles = [postings[i].get("title", "") for i in idxs]
        label = Counter(_canon_title(t) for t in titles if t).most_common(1)
        cc = Counter(postings[i].get("country") for i in idxs if postings[i].get("country"))
        arms = Counter(postings[i].get("arm") for i in idxs)
        topsk = Counter(s for i in idxs for s in skills_per[i]).most_common(6)
        clusters.append({
            "label": (label[0][0].title() if label else "role"),
            "size": len(idxs),
            "countries": dict(cc.most_common()),
            "arms": dict(arms),
            "top_skills": [s for s, _ in topsk],
            "sample_titles": titles[:6],
        })
    clusters.sort(key=lambda c: c["size"], reverse=True)
    log.info("fingerprint cluster: %d postings → %d clusters (≥%d), %d noise [mode=%s dev=%s]",
             len(postings), len(clusters), min_cluster_size, noise, mode, device)
    return {"mode": mode, "device": device, "n_postings": len(postings),
            "n_clusters": len(clusters), "n_noise": noise, "clusters": clusters}


# --------------------------------------------------------------------------- #
#  cross-board dedup — MinHash over (title+description) shingles, blocked        #
# --------------------------------------------------------------------------- #
def _shingles(text: str, k: int = 5) -> set[str]:
    toks = normalize_surface(text).split()
    if len(toks) < k:
        return {" ".join(toks)} if toks else set()
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def _minhash(shings: set[str], n: int = 32) -> tuple[int, ...]:
    if not shings:
        return tuple([0] * n)
    sig = []
    for seed in range(n):
        mn = min(int(hashlib.blake2b(f"{seed}\x00{s}".encode(), digest_size=8).hexdigest(), 16)
                 for s in shings)
        sig.append(mn)
    return tuple(sig)


def _sig_jaccard(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(1 for x, y in zip(a, b) if x == y) / len(a) if a else 0.0


def dedup_postings(postings: list[dict], *, jaccard: float = 0.8) -> dict:
    """Cross-board dedup. Block on (employer, country) → MinHash near-dup collapse.

    Returns {n_input, n_unique, collapse_rate, dup_groups, deduped}. ``deduped`` keeps
    one representative per group with a ``sources`` badge list (N boards it appeared on).
    """
    # block on (employer, country, normalized-title): only same-role postings at the
    # same company+country are candidates, so distinct roles sharing company boilerplate
    # are never falsely merged. The description MinHash then collapses true reposts only.
    blocks: dict[tuple, list[int]] = defaultdict(list)
    for i, p in enumerate(postings):
        emp = normalize_surface(p.get("company", "") or p.get("board_slug", ""))
        nt = normalize_surface(p.get("title", ""))
        blocks[(emp, p.get("country"), nt)].append(i)

    parent = list(range(len(postings)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    sigs: dict[int, tuple] = {}
    for idxs in blocks.values():
        if len(idxs) < 2:
            continue
        for i in idxs:
            p = postings[i]
            sigs[i] = _minhash(_shingles(f"{p.get('title', '')} {p.get('description', '')[:400]}"))
        for a_pos in range(len(idxs)):
            for b_pos in range(a_pos + 1, len(idxs)):
                i, j = idxs[a_pos], idxs[b_pos]
                if _sig_jaccard(sigs[i], sigs[j]) >= jaccard:
                    union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(postings)):
        groups[find(i)].append(i)

    deduped, dup_groups = [], 0
    for root, members in groups.items():
        if len(members) > 1:
            dup_groups += 1
        rep = dict(postings[root])
        rep["sources"] = [postings[m].get("board_slug") for m in members]
        rep["dup_count"] = len(members)
        deduped.append(rep)

    n_in, n_uniq = len(postings), len(deduped)
    rate = round(100 * (n_in - n_uniq) / n_in, 1) if n_in else 0.0
    log.info("dedup: %d → %d unique (%.1f%% collapse, %d dup-groups)", n_in, n_uniq, rate, dup_groups)
    return {"n_input": n_in, "n_unique": n_uniq, "collapse_rate": rate,
            "dup_groups": dup_groups, "deduped": deduped}
