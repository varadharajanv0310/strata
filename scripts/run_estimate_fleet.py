"""Detached launcher — the estimate-fleet + judge + fuse + materialize run.

Runs, in order:
  1. collect_all --only <8 estimate connectors, gov_projections, arxiv, extract_validate, fuse>
     (skips the 3h common_crawl / 28h llm_extract — nothing new for them to do)
  2. marts materialize — surface the freshly-fused warehouse into app.db so /api/dataset
     serves the new estimate curves / coverage / outlook.

Resumable: every connector caches per unit on disk (a re-launch skips done work);
extract_validate has its own stage checkpoint. Kicked off DETACHED (PowerShell
Start-Process) so it survives a Claude Code session restart. Progress: RUN_LOG.md +
this script's own stdout log.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# make the repo root importable no matter how this script is invoked (Start-Process
# puts scripts/ on sys.path[0], not the repo root)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# the curated subset — ORDER sequence is applied inside collect_all
ONLY = [
    "gov_projections", "arxiv",                       # zero-row axis fills (outlook/adoption)
    "itjobswatch", "seek_jobstreet", "talent", "payscale",
    "levelsfyi", "gehalt_de", "canada_jobbank", "roberthalf",   # the 8 estimate connectors
    "extract_validate",                               # the GPU judge (gpt-oss:20b)
    "fuse",                                           # rebuild the warehouse from staging
]


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    print(f"[{_stamp()}] === estimate-fleet run START ===", flush=True)
    print(f"[{_stamp()}] stages: {', '.join(ONLY)}", flush=True)

    # 1) collection + fuse (in-process, so we get one contiguous run)
    from backend.pipelines import collect_all
    t0 = time.time()
    for name in [s for s in collect_all.ORDER if s in set(ONLY)]:
        print(f"[{_stamp()}] ▶ {name}", flush=True)
        try:
            collect_all.run_stage(name)
        except Exception as e:  # noqa: BLE001 — one stage must never sink the run
            print(f"[{_stamp()}] !! {name} raised {e!r} — continuing", flush=True)
    print(f"[{_stamp()}] collection+fuse done in {int(time.time() - t0)}s", flush=True)

    # 2) materialize marts → app.db (surface the new data; no live site to cut over)
    print(f"[{_stamp()}] ▶ materialize marts", flush=True)
    try:
        from backend.marts.materialize import materialize_from_warehouse
        materialize_from_warehouse()
        print(f"[{_stamp()}] materialize done", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[{_stamp()}] !! materialize raised {e!r}", flush=True)

    print(f"[{_stamp()}] === estimate-fleet run COMPLETE ({int(time.time() - t0)}s) ===", flush=True)


if __name__ == "__main__":
    main()
