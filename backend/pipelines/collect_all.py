"""collect_all — overnight unattended data-collection orchestrator.

Runs each source connector sequentially, then the GPU normalization stages, then
the warehouse fusion. Each stage has a wall-clock BUDGET (enforced via a
subprocess timeout) and degrades gracefully: on failure or budget-exhaustion it
checkpoints whatever landed (connectors checkpoint per unit on disk), logs the
REAL row count to RUN_LOG.md, and moves on — one slow/failed source never hangs
the run. Fully resumable: re-running skips done units.

**STOPS at the warehouse** — never materializes marts / touches the live site.

    python -m backend.pipelines.collect_all --stage h1b   # one stage
    python -m backend.pipelines.collect_all               # all stages in order
"""
from __future__ import annotations

import os

# transformers: torch-only (avoid TF/Keras-3 + torchvision ABI conflicts) so the
# GPU sentence-embedding path imports cleanly in every subprocess.
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# force UTF-8 everywhere so the emoji/arrow log lines don't crash on Windows cp1252
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[2]
RUN_LOG = ROOT / "RUN_LOG.md"
STATE = ROOT / "backend" / "data" / "run_state.json"
PY = sys.executable

# name -> (python expr run as a budgeted subprocess, budget_seconds)
STAGES: dict[str, tuple[str, int]] = {
    "so_survey": ("from backend.ingest.so_survey import fetch_and_aggregate; print(fetch_and_aggregate())", 900),
    "h1b": ("from backend.ingest.h1b import run; print(run(years=['FY2025','FY2024','FY2023'], time_cap_s=2400))", 2700),
    "gh_archive": ("from backend.ingest.gh_archive import run; print(run(years=[2022,2023,2024,2025]))", 7200),
    "google_trends": ("from backend.ingest.google_trends import run; print(run(max_units=None))", 3000),
    "baselines": ("from backend.ingest.baselines import run; print(run())", 1200),
    # ILOSTAT cross-country wage spine → official salary lens (council source #1)
    "ilostat": ("from backend.ingest.ilostat import run; print(run())", 1200),
    # ---- council round-2 connectors (built, credential/network-graceful; run later) ----
    # demand-OUTLOOK axis → fact_role_outlook
    "gov_projections": ("from backend.ingest.gov_projections import run; print(run())", 2400),
    # skill-ADOPTION / durability axis → fact_skill_adoption
    "stack_exchange": ("from backend.ingest.stack_exchange import run; print(run())", 21600),
    "package_registries": ("from backend.ingest.package_registries import run; print(run())", 2400),
    "arxiv": ("from backend.ingest.arxiv import run; print(run())", 5400),
    "huggingface": ("from backend.ingest.huggingface import run; print(run())", 2400),
    "wikipedia_pageviews": ("from backend.ingest.wikipedia_pageviews import run; print(run())", 2400),
    # demand / vacancy feeds (DE/SG/US/EU/remote) → fact_demand / fact_salary_*
    "eures": ("from backend.ingest.eures import run; print(run())", 2400),
    "bundesagentur": ("from backend.ingest.bundesagentur import run; print(run())", 2400),
    "mycareersfuture": ("from backend.ingest.mycareersfuture import run; print(run())", 2400),
    "usajobs": ("from backend.ingest.usajobs import run; print(run())", 2400),
    "cedefop_ovate": ("from backend.ingest.cedefop_ovate import run; print(run())", 1800),
    "hn_hiring": ("from backend.ingest.hn_hiring import run; print(run())", 5400),
    "remoteok": ("from backend.ingest.remoteok import run; print(run())", 600),
    # roles-only occupation graph (employer-stripped) → bridge_role_adjacency
    "wikidata_occupations": ("from backend.ingest.wikidata_occupations import run; print(run())", 2400),
    "common_crawl": ("from backend.ingest.common_crawl import run; print(run(target_per_country=2000, time_cap_s=10500))", 10800),
    # LLM corpus extraction (the GPU differentiator) → staging/extracted; then validate it.
    # Long GPU budget (~15-28 GPU-hours for the full corpus). Build-only this pass; run later.
    "llm_extract": ("from backend.ml.llm_extract import run; print(run(shard_size=2000))", 100800),
    "extract_validate": ("from backend.ml.extract_validate import run; print(run())", 7200),
    "gpu_normalize": (
        "from backend.ml.skill_norm import run as s; from backend.ml.entity_resolution import run as e; "
        "from backend.ml.role_derivation import run as r; "
        "print('skill_norm', s()); print('entity_resolution', e()); print('role_derivation', r())",
        5400),
    # parse the cached O*NET zip → role adjacency + skill importance staging (pure
    # compute, no network). Runs before fuse so build_warehouse can read it.
    "onet_trajectory": ("from backend.warehouse.onet_trajectory import run; print(run())", 300),
    # cached-data analytics → staging the marts materialize reads (no network)
    "role_ladders": ("from backend.analytics.promotion_ladder import run; print(run())", 900),
    "hedonic": ("from backend.ml.hedonic import run; print(run())", 900),
    "fuse": ("from backend.warehouse.build import build_warehouse_from_staging as f; f(); print('fused')", 1200),
}
ORDER = ["so_survey", "h1b", "gh_archive", "google_trends", "baselines", "ilostat",
         "gov_projections", "stack_exchange", "package_registries", "arxiv", "huggingface",
         "wikipedia_pageviews", "eures", "bundesagentur", "mycareersfuture", "usajobs",
         "cedefop_ovate", "hn_hiring", "remoteok", "wikidata_occupations",
         "common_crawl", "llm_extract", "extract_validate", "gpu_normalize",
         "onet_trajectory", "role_ladders", "hedonic", "fuse"]


def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M")


def log(line: str) -> None:
    with open(RUN_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def set_state(**kw) -> None:
    try:
        prev = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        prev.update(kw)
        prev["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        STATE.write_text(json.dumps(prev, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def count_stage(name: str) -> str:
    """Real row count for a stage's landed output (in-process, fast)."""
    from backend.core.config import settings
    sd = settings.staging_dir
    try:
        if name == "so_survey":
            from backend.ingest.so_survey import load_agg
            return f"{len(load_agg())} cells"
        if name == "h1b":
            from backend.ingest.h1b import load_agg
            return f"{len(load_agg())} US wage cells"
        if name == "gh_archive":
            from backend.ingest.gh_archive import load_demand
            return f"{len(load_demand())} demand records"
        if name == "google_trends":
            from backend.ingest.google_trends import load_interest
            return f"{len(load_interest())} interest rows"
        if name == "baselines":
            from backend.ingest.baselines import load_all
            return f"{len(load_all())} anchors"
        if name == "common_crawl":
            import duckdb
            p = sd / "common_crawl" / "postings.parquet"
            n = 0
            if p.exists():
                pq = str(p).replace("\\", "/")
                n = duckdb.connect().execute(f"select count(*) from read_parquet('{pq}')").fetchone()[0]
            pr = {}
            probe = sd / "common_crawl" / "probe.json"
            if probe.exists():
                pr = json.loads(probe.read_text(encoding="utf-8"))
            return f"{n} postings, disclosure {pr.get('salary_disclosure_rate')}"
        if name == "gpu_normalize":
            import duckdb
            d = sd / "normalized"
            if not d.exists():
                return "no normalized output"
            parts = []
            for f in sorted(d.glob("*.parquet")):
                try:
                    pq = str(f).replace("\\", "/")
                    c = duckdb.connect().execute(f"select count(*) from read_parquet('{pq}')").fetchone()[0]
                except Exception:  # noqa: BLE001
                    c = "?"
                parts.append(f"{f.stem}:{c}")
            return ", ".join(parts) or "no normalized output"
        if name == "ilostat":
            from backend.ingest.ilostat import load_earnings
            rows = load_earnings()
            return f"{len(rows)} earnings rows, {len({r['country'] for r in rows})} countries"
        if name == "onet_trajectory":
            from backend.warehouse.onet_trajectory import load_adjacency, load_skill_importance
            return f"{len(load_adjacency())} adjacency edges, {len(load_skill_importance())} skill-importance rows"
        if name == "role_ladders":
            from backend.analytics.promotion_ladder import load_ladders
            return f"{len(load_ladders())} role pay ladders"
        if name == "hedonic":
            from backend.ml.hedonic import load_premiums
            return f"{len(load_premiums())} skill premiums"
        if name == "llm_extract":
            from backend.ml.llm_extract import load_extracted
            rows = load_extracted()
            n = len(rows) if rows is not None else 0
            ab = sum(1 for r in rows if r.get("abstain")) if rows is not None and n else 0
            return f"{n} postings extracted, {ab} abstained"
        if name == "extract_validate":
            import os
            p = sd / "extracted" / "validation_report.json"
            return "validated (report present)" if p.exists() else "no validation report yet"
        if name in {"gov_projections", "stack_exchange", "package_registries", "arxiv",
                    "huggingface", "wikipedia_pageviews", "eures", "bundesagentur",
                    "mycareersfuture", "usajobs", "cedefop_ovate", "hn_hiring", "remoteok",
                    "wikidata_occupations"}:
            # E4: a few connectors land to a shorter dir than their stage name.
            _DIR = {"cedefop_ovate": "cedefop", "wikipedia_pageviews": "wikipedia",
                    "wikidata_occupations": "wikidata"}
            d = sd / _DIR.get(name, name)
            if not d.exists():
                return "no staging yet"
            total = 0
            for f in d.glob("*.json"):
                try:
                    obj = json.loads(f.read_text(encoding="utf-8"))
                    total += len(obj) if isinstance(obj, list) else 1
                except Exception:  # noqa: BLE001
                    pass
            return f"{total} rows landed"
        if name == "fuse":
            from backend.core.db import duckdb_connect
            c = duckdb_connect(read_only=True)
            r = (f"salary_person {c.execute('select count(*) from fact_salary_person').fetchone()[0]}"
                 f", demand {c.execute('select count(*) from fact_demand').fetchone()[0]}"
                 f", interest {c.execute('select count(*) from fact_interest').fetchone()[0]}"
                 f", salary_job {c.execute('select count(*) from fact_salary_job').fetchone()[0]}"
                 f", dim_role {c.execute('select count(*) from dim_role').fetchone()[0]}")
            c.close()
            return r
    except Exception as ex:  # noqa: BLE001
        return f"count-error: {ex}"
    return "?"


def run_stage(name: str) -> bool:
    expr, budget = STAGES[name]
    log(f"- `{_ts()}` — ▶ **{name}** start (budget {budget // 60}m)")
    set_state(stage=name, status="running")
    t0 = time.time()
    ok, note = False, ""
    try:
        p = subprocess.run([PY, "-c", expr], cwd=str(ROOT), env={**os.environ},
                           timeout=budget, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        ok = p.returncode == 0
        if not ok:
            note = " | err: " + (p.stderr.strip().splitlines()[-1][:200] if p.stderr.strip() else "nonzero exit")
    except subprocess.TimeoutExpired:
        note = f" | hit {budget // 60}m budget — checkpointed partial"
    dt = int(time.time() - t0)
    cnt = count_stage(name)
    icon = "✅" if ok else "⚠️"
    log(f"- `{_ts()}` — {icon} **{name}** {dt}s — **{cnt}**{note}")
    set_state(stage=name, status=("done" if ok else "degraded"), count=cnt, seconds=dt)
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", help="run a single stage")
    ap.add_argument("--from", dest="from_stage", help="run from this stage onward")
    args = ap.parse_args()
    if args.stage:
        run_stage(args.stage)
        return
    start = ORDER.index(args.from_stage) if args.from_stage in ORDER else 0
    log(f"- `{_ts()}` — 🚀 **collect_all** started ({len(ORDER) - start} stages)")
    for name in ORDER[start:]:
        run_stage(name)
    log(f"- `{_ts()}` — 🏁 **collect_all complete** (stopped at warehouse; marts/site untouched)")
    set_state(status="complete")


if __name__ == "__main__":
    main()
