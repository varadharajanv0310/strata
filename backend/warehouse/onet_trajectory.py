"""O*NET trajectory + skill-importance — the roles-only *"where does this role lead?"*
and *"which skills matter most?"* layers, parsed from the O*NET database zip **already
cached** in ``staging/onet/onet_db.zip`` (no download, no ingestion run).

Three members of the same cached zip light up two axes strata promised but barely had:

* ``Related Occupations.txt`` → **role→role adjacency** (relatedness-ranked) →
  ``bridge_role_adjacency`` (``edge_type='similar'``). This is the trajectory primitive:
  given a role, what roles is it closest to.
* ``Technology Skills.txt`` (concrete tools + *Hot Technology* / *In Demand* flags) →
  per-role **tool importance** mapped onto strata's own skill vocabulary.
* ``Skills.txt`` + ``Knowledge.txt`` (IM importance / LV level scales) → per-role generic
  **skill + knowledge importance** (0–100).

All crosswalked **SOC → strata role** via the H-1B connector's ``_soc_to_role`` (employers
are irrelevant here). Output is two staging files —
``staging/onet/role_adjacency.json`` and ``role_skill_importance.json`` — which
``build_warehouse_from_staging`` fuses into ``bridge_role_adjacency`` and
``bridge_role_skill_importance``.

**ROLES-ONLY:** every edge is occupation→occupation or occupation→skill. No employer,
org, or company entity is read, derived, or written.
"""
from __future__ import annotations

import io
import json
import zipfile
from collections import defaultdict

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("warehouse.onet_trajectory")

ZIP_REL = "onet/onet_db.zip"
ADJ_OUT = "onet/role_adjacency.json"
SKILL_OUT = "onet/role_skill_importance.json"

# relatedness rank → similarity (1 = closest). Clamp so far-down edges still register.
_SIM_FLOOR = 0.30


def _zip_path():
    return settings.staging_dir / ZIP_REL


def _staging_dir():
    d = settings.staging_dir / "onet"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _base_soc(soc: str) -> str:
    """O*NET-SOC '15-1252.00' → base SOC '15-1252' (strip the detail suffix)."""
    return (soc or "").split(".")[0].strip()


def _find_member(names: list[str], leaf: str) -> str | None:
    for n in names:
        if n.endswith("/" + leaf) or n == leaf:
            return n
    return None


def _read_rows(z: zipfile.ZipFile, member: str):
    """Yield dict rows from a tab-delimited O*NET text member (header-mapped)."""
    with z.open(member) as f:
        txt = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
        header = txt.readline().rstrip("\n").split("\t")
        idx = {h.strip(): i for i, h in enumerate(header)}
        for line in txt:
            cells = line.rstrip("\n").split("\t")
            if len(cells) < len(header):
                continue
            yield idx, cells


def _soc_to_role():
    from backend.ingest.h1b import _soc_to_role as f
    return f


def _slug():
    from backend.warehouse.build import slug
    return slug


# ----------------------------------------------------------------------------- adjacency
def parse_adjacency(z: zipfile.ZipFile, names: list[str]) -> list[dict]:
    """Related Occupations → role→role 'similar' edges (best similarity per role pair)."""
    member = _find_member(names, "Related Occupations.txt")
    if not member:
        log.warning("onet_trajectory: no Related Occupations.txt in zip")
        return []
    soc_to_role = _soc_to_role()
    best: dict[tuple[str, str], float] = {}
    for idx, cells in _read_rows(z, member):
        soc_a = _base_soc(cells[idx["O*NET-SOC Code"]])
        soc_b = _base_soc(cells[idx["Related O*NET-SOC Code"]])
        ra, rb = soc_to_role(soc_a), soc_to_role(soc_b)
        if not ra or not rb or ra == rb:
            continue
        try:
            rank = int(cells[idx["Index"]])
        except (ValueError, KeyError):
            rank = 10
        sim = max(_SIM_FLOOR, round(1.0 - (rank - 1) * 0.05, 3))
        key = (ra, rb)
        if sim > best.get(key, 0.0):
            best[key] = sim
    edges = [{"from_role": a, "to_role": b, "similarity": s,
              "edge_type": "similar", "source_id": "onet"}
             for (a, b), s in best.items()]
    edges.sort(key=lambda e: e["similarity"], reverse=True)
    log.info("onet_trajectory: %d role→role adjacency edges", len(edges))
    return edges


# --------------------------------------------------------------------- skill importance
def _parse_im_lv(z: zipfile.ZipFile, member: str, role_acc: dict, source: str) -> None:
    """Accumulate IM (importance) + LV (level) scales from a Skills/Knowledge member."""
    soc_to_role = _soc_to_role()
    slug = _slug()
    # (role, skill_name) -> {"im":[..],"lv":[..]}
    for idx, cells in _read_rows(z, member):
        role = soc_to_role(_base_soc(cells[idx["O*NET-SOC Code"]]))
        if not role:
            continue
        name = cells[idx["Element Name"]].strip()
        scale = cells[idx["Scale ID"]].strip()
        try:
            val = float(cells[idx["Data Value"]])
        except (ValueError, KeyError):
            continue
        key = (role, slug(name), name, source)
        bucket = role_acc.setdefault(key, {"im": [], "lv": []})
        if scale == "IM":
            bucket["im"].append(val)          # 1–5
        elif scale == "LV":
            bucket["lv"].append(val)          # 0–7


def _parse_tech(z: zipfile.ZipFile, names: list[str], role_acc: dict) -> None:
    """Technology Skills → concrete tools mapped to strata's vocab; flags → importance."""
    member = _find_member(names, "Technology Skills.txt")
    if not member:
        return
    soc_to_role = _soc_to_role()
    slug = _slug()
    from backend.ml.fingerprint import extract_skills
    for idx, cells in _read_rows(z, member):
        role = soc_to_role(_base_soc(cells[idx["O*NET-SOC Code"]]))
        if not role:
            continue
        example = cells[idx["Example"]]
        title = cells[idx.get("Commodity Title", idx["Example"])]
        hot = cells[idx["Hot Technology"]].strip().upper() == "Y" if "Hot Technology" in idx else False
        indemand = cells[idx["In Demand"]].strip().upper() == "Y" if "In Demand" in idx else False
        skills = extract_skills(f"{example} {title}")     # → our canonical skill names
        if not skills:
            continue
        imp = 100.0 if indemand else (88.0 if hot else 62.0)
        for s in skills:
            key = (role, slug(s), s, "onet-tech")
            bucket = role_acc.setdefault(key, {"im": [], "lv": []})
            bucket.setdefault("tech", 0.0)
            bucket["tech"] = max(bucket["tech"], imp)


def parse_skill_importance(z: zipfile.ZipFile, names: list[str]) -> list[dict]:
    """Skills + Knowledge (IM/LV) and Technology Skills → per-role skill importance rows."""
    role_acc: dict = {}
    for leaf, src in (("Skills.txt", "onet"), ("Knowledge.txt", "onet")):
        m = _find_member(names, leaf)
        if m and not m.endswith("Technology Skills.txt"):
            _parse_im_lv(z, m, role_acc, src)
    _parse_tech(z, names, role_acc)

    rows = []
    for (role, skill_id, skill_name, source), b in role_acc.items():
        if "tech" in b:                                    # technology-tool importance
            importance = round(b["tech"], 1)
            level = None
        else:
            if not b["im"]:
                continue
            importance = round(sum(b["im"]) / len(b["im"]) * 20.0, 1)   # 1–5 → 0–100
            level = round(sum(b["lv"]) / len(b["lv"]) / 7.0 * 100.0, 1) if b["lv"] else None
        rows.append({"role_id": role, "skill_id": skill_id, "skill_name": skill_name,
                     "importance": importance, "level": level,
                     "essential": importance >= 75.0, "source_id": source})
    rows.sort(key=lambda r: (r["role_id"], -r["importance"]))
    log.info("onet_trajectory: %d role-skill importance rows (%d roles)",
             len(rows), len({r["role_id"] for r in rows}))
    return rows


# ------------------------------------------------------------------------------- driver
def build_staging() -> dict:
    """Parse the cached O*NET zip → two staging json files. No download, no run."""
    zp = _zip_path()
    if not zp.exists():
        log.warning("onet_trajectory: no cached zip at %s — fetch O*NET first", zp)
        return {"adjacency": 0, "skill_importance": 0, "written": False}
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        edges = parse_adjacency(z, names)
        skills = parse_skill_importance(z, names)
    d = _staging_dir()
    (d / "role_adjacency.json").write_text(json.dumps(edges), encoding="utf-8")
    (d / "role_skill_importance.json").write_text(json.dumps(skills), encoding="utf-8")
    return {"adjacency": len(edges), "skill_importance": len(skills), "written": True,
            "roles_with_adjacency": len({e["from_role"] for e in edges})}


def load_adjacency() -> list[dict]:
    p = _staging_dir() / "role_adjacency.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def load_skill_importance() -> list[dict]:
    p = _staging_dir() / "role_skill_importance.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def run() -> dict:
    """Build the staging files (parse-only; the warehouse fuse reads them)."""
    return build_staging()


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
