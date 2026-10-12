"""Fold duplicate catalog roles into one survivor, consistently across every generated file.

Generation can produce the same job twice under two ids (two batches named it independently:
``cloud-eng`` and ``cloud-engineer`` are both "Cloud Engineer"). ``merge_roles`` folds each
removed id into its survivor:

* **catalog** — the removed record is dropped; every career path (``leads_to``) that pointed at
  it points at the survivor instead (deduped, never at itself). The survivor keeps its own
  record. ``merged_ids`` records removed -> survivor so old ids stay traceable.
* **aliases** — the removed role's titles and fields join the survivor's (deduped, capped).
* **traits / roadmaps** — the survivor's are kept, the removed role's are dropped.

Survivor rule used for the 2026-10-12 merge: keep the id more career paths point at; higher
demand breaks a tie. Rebuild the served layer afterwards (``python -m backend.cli publish``).

CLI::

    python -m backend.warehouse.catalog_merge            # apply MERGES (idempotent)
    python -m backend.warehouse.catalog_merge --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.warehouse import role_content as rc

CATALOG_FILE = rc.CATALOG_FILE

# removed id -> survivor id. Each pair had the identical role name in the catalog.
MERGES: dict[str, str] = {
    "cloud-eng": "cloud-engineer",                         # "Cloud Engineer"
    "platform-engineer": "platform-eng",                   # "Platform Engineer"
    "php-developer": "php-engineer",                       # "PHP Developer"
    "network-security-eng": "network-security-engineer",   # "Network Security Engineer"
    "tpm": "technical-program-manager",                    # "Technical Program Manager"
    "staff-eng": "staff-engineer",                         # "Staff Engineer"
    "principal-eng": "principal-engineer",                 # "Principal Engineer"
}

_MAX_TITLES, _MAX_FIELDS = 15, 6


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _save(p: Path, data: dict, indent: int) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")


def _union(keep: list[str], add: list[str], cap: int) -> list[str]:
    out, seen = [], set()
    for s in [*keep, *add]:
        n = rc._norm(s)
        if n and n not in seen:
            seen.add(n)
            out.append(s)
    return out[:cap]


def merge_catalog(cat: dict, merges: dict[str, str]) -> dict:
    roles = {r["id"]: r for r in cat["roles"]}
    for gone, keep in merges.items():
        if keep not in roles and keep not in merges.values():
            raise ValueError(f"survivor '{keep}' is not in the catalog")
    todo = {g: k for g, k in merges.items() if g in roles}
    cat["roles"] = [r for r in cat["roles"] if r["id"] not in todo]
    for r in cat["roles"]:
        paths, seen = [], set()
        for t in r.get("leads_to", []):
            t = merges.get(t, t)
            if t != r["id"] and t not in seen:
                seen.add(t)
                paths.append(t)
        r["leads_to"] = paths
    cat["merged_ids"] = {**cat.get("merged_ids", {}), **merges}
    cat.setdefault("stats", {})["roles"] = len(cat["roles"])
    cat["stats"]["merged"] = len(cat["merged_ids"])
    return cat


def merge_aliases(data: dict, merges: dict[str, str]) -> dict:
    roles = data["roles"]
    for gone, keep in merges.items():
        if gone not in roles:
            continue
        a, b = roles.setdefault(keep, {"titles": [], "fields": []}), roles.pop(gone)
        a["titles"] = _union(a.get("titles", []), b.get("titles", []), _MAX_TITLES)
        a["fields"] = _union(a.get("fields", []), b.get("fields", []), _MAX_FIELDS)
    return data


def merge_drop(data: dict, merges: dict[str, str]) -> dict:
    for gone in merges:
        data["roles"].pop(gone, None)
    return data


def merge_roles(merges: dict[str, str] = MERGES, *, dry_run: bool = False) -> dict:
    cat = _load(CATALOG_FILE)
    before = len(cat["roles"])
    present = sorted(g for g in merges if g in {r["id"] for r in cat["roles"]})
    cat = merge_catalog(cat, merges)
    files = {"aliases": merge_aliases(rc.load("aliases"), merges),
             "traits": merge_drop(rc.load("traits"), merges),
             "roadmaps": merge_drop(rc.load("roadmaps"), merges)}
    report = {"roles_before": before, "roles_after": len(cat["roles"]), "merged_now": present}
    if dry_run:
        return report
    _save(CATALOG_FILE, cat, indent=1)
    for kind, data in files.items():
        _save(rc._tracked_path(kind), data, indent=1)
    return report


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="catalog_merge")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(merge_roles(dry_run=a.dry_run), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
