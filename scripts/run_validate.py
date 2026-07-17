"""Detached launcher — a FRESH extract_validate run (the LLM-as-judge GPU pass).

The checkpoint + old report are cleared before this runs, so every strategy re-executes:
ground-truth (source-text join → real role agreement + O*NET skills), the gpt-oss:20b
judge (faithfulness of responsibilities_summary — now that fmt is fixed and the join
gives it real posting text), and self-consistency. Writes validation_report.json.
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
    print(f"[{time.strftime('%H:%M:%S')}] extract_validate (fresh) START", flush=True)
    from backend.ml.extract_validate import run
    summary = run()
    print(f"[{time.strftime('%H:%M:%S')}] DONE in {int(time.time()-t0)}s", flush=True)
    print(json.dumps(summary, default=str)[:2000], flush=True)


if __name__ == "__main__":
    main()
