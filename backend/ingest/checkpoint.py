"""Resumable checkpoints — so long scans (Common Crawl especially) never restart
from zero (brief §6/§10). One JSON file per source under data/checkpoints/.
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.core.config import settings


def _path(source: str) -> Path:
    d = settings.data_path / "checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{source}.json"


def load(source: str) -> dict:
    p = _path(source)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save(source: str, state: dict) -> None:
    _path(source).write_text(json.dumps(state, indent=2), encoding="utf-8")


def mark_done(source: str, unit: str) -> None:
    st = load(source)
    done = set(st.get("done", []))
    done.add(unit)
    st["done"] = sorted(done)
    save(source, st)


def is_done(source: str, unit: str) -> bool:
    return unit in set(load(source).get("done", []))
