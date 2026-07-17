"""Detached launcher — parse the already-landed Stack Overflow Posts.7z.

The 23GB Posts.7z is already cached; this extracts its ~104GB Posts.xml to disk
(bounded memory), SAX-parses monthly per-tag question volume, and lands
tag_volume.json (the durability/emergence signal the fuse consumes). Adjacency is
skipped (RAM-tight box; no fuse reader yet). The extracted XML is deleted afterward.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] stack_exchange parse START", flush=True)
    from backend.ingest.stack_exchange import run
    summary = run(skip_download=True, build_adjacency=False)
    print(f"[{time.strftime('%H:%M:%S')}] DONE in {int(time.time()-t0)}s: "
          f"{json.dumps(summary)}", flush=True)


if __name__ == "__main__":
    main()
