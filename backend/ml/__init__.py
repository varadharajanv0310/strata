"""GPU / batch ML pipeline (brief §4).

Stages, in dependency order:
  skill_norm          — taxonomy normalization (sentence-transformers + FAISS)   [needs ML extras + real data]
  entity_resolution   — posting dedup via embeddings + blocking                   [needs ML extras + real data]
  role_derivation     — cluster normalized titles into roles (volume floor)       [needs ML extras + real data]
  forecasting         — back-tested demand forecast + confidence intervals        [needs ML extras]
  job_score           — Demand / inverse-Interest / Salary composite              [GPU-free, runnable now]

The heavy stages import-guard torch/faiss/darts so the API/warehouse never depend
on them; they raise a clear message if run without the extras or real data.
"""
from __future__ import annotations

# Force the transformers/sentence-transformers stack onto the torch-only path BEFORE any
# submodule lazily imports it. Without this, `import sentence_transformers` pulls in the
# TF/Keras-3 integration and fails ("Failed to import transformers.integrations…"), so
# _have_embeddings() returns False and entity_resolution/role_derivation silently fall back
# to non-embedding lexical grouping (lower-quality clustering). This __init__ runs before
# any `backend.ml.<submodule>` import, so the guard is always in place — no reliance on the
# caller (collect_all) setting it in the subprocess env.
import os as _os

for _k, _v in (("USE_TF", "0"), ("USE_FLAX", "0"), ("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1"),
               ("TOKENIZERS_PARALLELISM", "false")):
    _os.environ.setdefault(_k, _v)

from backend.core.logging import get_logger

log = get_logger("ml")

STAGES = ("skill_norm", "entity_resolution", "role_derivation", "forecasting", "job_score")


def run_stage(stage: str):
    if stage not in STAGES:
        raise ValueError(f"unknown ml stage '{stage}'. one of: {', '.join(STAGES)}")
    mod = __import__(f"backend.ml.{stage}", fromlist=["run"])
    log.info("running ml stage: %s", stage)
    return mod.run()
