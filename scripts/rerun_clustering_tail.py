"""Corrective re-run — re-cluster with the fixed leader-clustering, then re-fuse + re-serve.

The overnight run populated everything correctly except breadth: role_derivation used the old
connected-components clusterer, which chained the 216k corpus into 7 mega-clusters. The fix
(fingerprint.leader_cluster) is committed. This re-runs ONLY the affected tail — gpu_normalize
(now leader-clustered) → fuse → materialize — on the GPU that the overnight extraction has
freed. Backs up the warehouse first; graceful + logged to the overnight report.
"""
from __future__ import annotations

import os

os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")
os.environ.setdefault("PYTHONUTF8", "1")

import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPORT = Path(__file__).resolve().parents[1] / "reports" / "OVERNIGHT_REPORT.md"


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def report(line: str) -> None:
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main() -> None:
    from backend.core.config import settings
    from backend.pipelines import collect_all

    report(f"\n## Corrective clustering re-run — {_stamp()}\n")
    # protect the warehouse
    wh = Path(settings.duckdb_path)
    if wh.exists():
        bak = wh.with_suffix(wh.suffix + f".bak-precluster-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(wh, bak)
        report(f"- `{_stamp()}` — 🛡 backed up → `{bak.name}`")
    # free VRAM for the MiniLM embedder
    for m in ("qwen3:8b", "gpt-oss:20b"):
        try:
            urllib.request.urlopen(urllib.request.Request(
                "http://localhost:11434/api/generate",
                data=json.dumps({"model": m, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"}), timeout=15).read()
        except Exception:  # noqa: BLE001
            pass

    t0 = time.time()
    for stage in ("gpu_normalize", "fuse", "materialize"):
        t = time.time()
        try:
            ok = collect_all.run_stage(stage)
        except Exception as e:  # noqa: BLE001
            ok = False
            report(f"- `{_stamp()}` — 💥 **{stage}** {type(e).__name__}: {str(e)[:120]}")
        report(f"- `{_stamp()}` — {'✅' if ok else '⚠️'} **{stage}** {int(time.time()-t)}s — "
               f"{collect_all.count_stage(stage)}")

    # final breadth + warehouse state
    try:
        from backend.core.db import duckdb_connect
        c = duckdb_connect(read_only=True)
        nrole = c.execute("select count(*) from dim_role").fetchone()[0]
        nderived = c.execute("select count(*) from dim_role where is_seed=false").fetchone()[0]
        c.close()
        report(f"- `{_stamp()}` — 🏁 corrective re-run done in {int(time.time()-t0)//60}m — "
               f"**catalog now {nrole} roles ({nderived} derived, was 7)**")
    except Exception as e:  # noqa: BLE001
        report(f"- `{_stamp()}` — catalog read failed: {e}")


if __name__ == "__main__":
    main()
