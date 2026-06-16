"""Fingerprint clustering + cross-board dedup — STUB (interface + buildable parts).

Two jobs from the brainstorm, at millions-of-postings scale:

  1. **Cluster fingerprints, not titles.** The unit of embedding is a *composite
     document* — normalized title ⊕ skill-bag ⊕ department ⊕ salary-band ⊕
     seniority-stripped tokens — so "Data Engineer" (Spark/Airflow) and "Analytics
     Engineer" (dbt/Looker) stay distinct despite near-identical titles, and "MTS"
     (zero title signal) still lands via its skills. Dense clusters above a volume
     floor with no canonical match auto-promote to emerging roles.

  2. **Cross-board dedup so "millions" is honest.** Block on (employer, country,
     month) → MinHash-LSH over description shingles → confirm with embedding cosine.
     The dup-group becomes an N-source provenance badge, not a deletion.

The composite-document builder (NON data-dependent) is implemented + unit-testable.
The clustering thresholds, the embedding pass (reuse role_derivation's MiniLM/5080
path), and the MinHash-LSH tuning are **data-dependent** and stubbed — they must be
tuned against the real corpus (a future run), not guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from backend.core.logging import get_logger
from backend.warehouse.taxonomy import normalize_surface

log = get_logger("ml.fingerprint")

_WS = re.compile(r"\s+")


def salary_band(value: float | None) -> str:
    """Coarse salary band token (so pay enters the fingerprint without leaking exact $)."""
    if not value or value <= 0:
        return "sal-unknown"
    # log-spaced bands; tuned later against the corpus distribution
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


def cluster_fingerprints(docs: list[str], *, min_cluster_size: int = 50,
                         distance: float = 0.18):
    """STUB: embed composite docs → cluster → canonical/emerging assignment.

    TODO(ingestion): embed via role_derivation's MiniLM/5080 path, HDBSCAN (or the
    sklearn fallback) at ``min_cluster_size``; a dense cluster >floor and >``distance``
    from every canonical centroid auto-promotes to an emerging node (append a
    dim_role_birth row). Needs the at-scale corpus to tune the floor + distance — a
    future run, not guessable on the current 344-posting CC sample.
    """
    raise NotImplementedError(
        "fingerprint clustering deferred — needs the at-scale posting corpus to tune "
        f"min_cluster_size/distance (stub called with {len(docs)} docs).")


def dedup_postings(postings: list[dict], *, jaccard: float = 0.7):
    """STUB: cross-board dedup → dup-groups with N source badges.

    TODO(ingestion): block on (employer_norm, country, month) → MinHash signatures
    over 5-shingles of the description → banded LSH at ``jaccard`` → confirm with
    description-embedding cosine ≥ 0.86 (mirror entity_resolution.py). Tune the
    threshold against a hand-labeled 1k-pair set so the collapse rate (~35%) is
    stateable. Deferred to a run with the real multi-board corpus.
    """
    raise NotImplementedError(
        "cross-board dedup deferred — needs multi-board corpus + a labeled pair set "
        f"to tune the LSH/cosine thresholds (stub called with {len(postings)} postings).")
