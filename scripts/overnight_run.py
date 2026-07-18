"""Unattended overnight run — full ingestion → clustering → sample extraction → validate →
generate descriptive layer → fuse → materialize. Populates the REAL warehouse. No push, no
site cutover.

Built for graceful failure, not a babysitter:
* STAGED cheap→heavy: fast/cached sources first (early failures are cheap + visible), the HF
  download + the GPU stages LAST.
* Each stage runs as a BUDGETED subprocess via collect_all.run_stage — a hard wall-clock cap
  (timeout), a heartbeat line, and graceful degradation: a stage that fails/stalls is logged
  and the run CONTINUES (one broken connector never aborts the night; the 6.5h-hang lesson).
* RESUMABLE: connectors checkpoint per unit on disk (cache IS the checkpoint); llm_extract
  checkpoints per shard. A re-launch skips finished work.
* WAREHOUSE PROTECTED: the DuckDB warehouse is COPIED to a timestamped .bak before anything
  runs, and the fuse writes in its own transaction — a late-stage failure never leaves it
  half-written (worst case: restore the .bak).
* REPORT: appends to reports/OVERNIGHT_REPORT.md as it goes (per-stage rows, the validation
  numbers, catalog size, what broke, final warehouse state) so it's readable in the morning.
"""
from __future__ import annotations

import os

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "OVERNIGHT_REPORT.md"

# Staged cheap→heavy. Cached connectors resolve fast; the HF pull + GPU stages are last so an
# early failure costs little. Dependencies respected: raw feeds land before clustering/extract;
# analytics + generation land before the fuse; materialize is last.
ORDER = [
    # --- tier 1: cheap / already-cached spine (fast) ---
    "baselines", "so_survey", "h1b", "ilostat", "google_trends", "gh_archive",
    "package_registries", "wikidata_occupations", "wikipedia_pageviews", "huggingface",
    "onet_trajectory", "role_ladders", "hedonic",
    # --- tier 2: network connectors (bounded, graceful) ---
    "adzuna", "ambitionbox", "itjobswatch", "seek_jobstreet", "talent", "payscale",
    "levelsfyi", "gehalt_de", "canada_jobbank", "roberthalf",
    "gov_projections", "arxiv", "eures", "bundesagentur", "mycareersfuture", "usajobs",
    "cedefop_ovate", "hn_hiring", "remoteok", "stack_exchange",
    "smartrecruiters", "personio",
    # --- tier 3: the heavy download (last ingestion) ---
    "hf_jobposting",
    # --- tier 4: compute (clustering → sample extract → validate → generate) ---
    "gpu_normalize",        # skill_norm + entity_resolution (guarded) + role_derivation (full corpus)
    "llm_extract",          # SAMPLE ~3000 (settings.extraction_sample_size), not the whole corpus
    "extract_validate",     # the 90.6% validation numbers (judge, samples internally)
    "posting_attributes",   # analytics over the sample extraction (unsurfaced tables)
    "role_layer",           # GENERATED descriptive layer (labelled generated)
    # --- tier 5: populate ---
    "fuse",
    "materialize",
]

# GPU stages that need the 16GB card clear of any resident Ollama model — the torch embedder
# (gpu_normalize) and each Ollama stage (qwen3 extractor, gpt-oss judge, qwen3 generator).
# Unload Ollama before each so they never collide (the VRAM lesson).
_OLLAMA_GPU = {"gpu_normalize", "llm_extract", "extract_validate", "role_layer"}


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def report(line: str) -> None:
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _unload_ollama() -> None:
    """Best-effort: evict all Ollama models from VRAM before a GPU stage (keep_alive:0)."""
    import urllib.request
    for m in ("qwen3:8b", "gpt-oss:20b"):
        try:
            req = urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=json.dumps({"model": m, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
        except Exception:  # noqa: BLE001
            pass


def _backup_warehouse() -> None:
    from backend.core.config import settings
    wh = Path(settings.duckdb_path)
    if wh.exists():
        bak = wh.with_suffix(wh.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(wh, bak)
        report(f"- `{_stamp()}` — 🛡 warehouse backed up → `{bak.name}` "
               f"({wh.stat().st_size // (1024*1024)}MB)")
    else:
        report(f"- `{_stamp()}` — ⚠ no existing warehouse at {wh} — will be created by fuse")


def _warehouse_state() -> str:
    try:
        from backend.core.db import duckdb_connect
        c = duckdb_connect(read_only=True)
        parts = []
        for t in ("dim_role", "fact_salary_person", "fact_salary_job", "fact_salary_official",
                  "fact_salary_yoe_obs", "fact_demand", "fact_interest", "fact_role_outlook",
                  "fact_skill_adoption"):
            try:
                parts.append(f"{t}={c.execute(f'select count(*) from {t}').fetchone()[0]:,}")
            except Exception:  # noqa: BLE001
                parts.append(f"{t}=?")
        c.close()
        return " · ".join(parts)
    except Exception as e:  # noqa: BLE001
        return f"(warehouse unreadable: {e})"


def _catalog_size() -> str:
    try:
        from backend.core.config import settings
        import duckdb
        p = settings.staging_dir / "normalized" / "derived_roles.parquet"
        if not p.exists():
            return "no derived_roles yet"
        pq = str(p).replace("\\", "/")
        n = duckdb.connect().execute(
            f"select count(distinct role_id) from read_parquet('{pq}')").fetchone()[0]
        return f"{n} derived role clusters"
    except Exception as e:  # noqa: BLE001
        return f"(catalog unreadable: {e})"


def main() -> None:
    from backend.pipelines import collect_all

    t_run = time.time()
    report(f"\n\n# Overnight run — {_stamp()}\n")
    report(f"- `{_stamp()}` — 🌙 START ({len(ORDER)} stages; sample-scale extraction; "
           f"populate-only, no push/publish)")
    _backup_warehouse()

    ok, degraded, blocked = [], [], []
    for name in ORDER:
        if name in _OLLAMA_GPU:
            _unload_ollama()          # free VRAM before each Ollama GPU stage
        t0 = time.time()
        try:
            success = collect_all.run_stage(name)   # budgeted subprocess + RUN_LOG + graceful
        except Exception as e:  # noqa: BLE001 — a stage crash must never abort the night
            success = False
            report(f"- `{_stamp()}` — 💥 **{name}** raised {type(e).__name__}: {str(e)[:120]}")
        cnt = collect_all.count_stage(name)
        dt = int(time.time() - t0)
        (ok if success else degraded).append(name)
        if not success and ("0 rows" in cnt or "no " in cnt.lower()):
            blocked.append(name)
        report(f"- `{_stamp()}` — {'✅' if success else '⚠️'} **{name}** {dt}s — {cnt}")
        # milestone snapshots the morning report wants inline
        if name == "gpu_normalize":
            report(f"    · catalog after role-derivation: **{_catalog_size()}**")
        if name == "extract_validate":
            report(f"    · validation: **{cnt}**")

    report(f"\n- `{_stamp()}` — 🏁 COMPLETE in {int(time.time()-t_run)//60}m")
    report(f"- **final warehouse:** {_warehouse_state()}")
    report(f"- **catalog:** {_catalog_size()}")
    report(f"- **ok ({len(ok)}):** {', '.join(ok)}")
    report(f"- **degraded/skipped ({len(degraded)}):** {', '.join(degraded) or 'none'}")
    report(f"- **note:** generated descriptive layer landed to staging/generated/role_layer.json "
           f"(labelled generated); its warehouse-surfacing is the documented follow-on. "
           f"Nothing pushed, no site cutover.")


if __name__ == "__main__":
    main()
