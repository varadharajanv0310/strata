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

from backend.core.logging import get_logger

log = get_logger("ml")

STAGES = ("skill_norm", "entity_resolution", "role_derivation", "forecasting", "job_score")


def run_stage(stage: str):
    if stage not in STAGES:
        raise ValueError(f"unknown ml stage '{stage}'. one of: {', '.join(STAGES)}")
    mod = __import__(f"backend.ml.{stage}", fromlist=["run"])
    log.info("running ml stage: %s", stage)
    return mod.run()
