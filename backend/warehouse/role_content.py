"""Shared per-role content — generated once per role and shared by every user.

This is the base every paid personalization is assembled from (docs/ECOSYSTEM_BUILD_SPEC.md §5):

* **Trait profile** per role, on the SAME dimensions the quiz measures, so a student's quiz result
  and a role can be compared directly (spec §4.1, §4.2).
* **Roadmap template** per role: ordered phases of steps built from the role's graded skills,
  each phase ending in something built (spec §4.3). Country-agnostic: the skills a role needs do
  not change by country; pay and demand (which do) come from the catalog.
* **Title aliases** per role: the job titles people post and hold for it, plus the discipline
  names internship postings use ("Machine Learning Intern"), so the resolver can classify any
  posting title onto a catalog role (spec §7 Phase 1a).

Everything here is stamped ``generated`` (model knowledge), following the catalog's honesty rules.
Generators write batch files; ``validate`` checks a batch before it is accepted and ``merge``
folds validated batches into the tracked files under ``backend/warehouse/generated/``.

CLI::

    python -m backend.warehouse.role_content validate traits   <batch.json>
    python -m backend.warehouse.role_content validate roadmaps <batch.json>
    python -m backend.warehouse.role_content merge    traits   <batch.json> [...]
    python -m backend.warehouse.role_content merge    roadmaps <batch.json> [...]
    python -m backend.warehouse.role_content validate aliases  <batch.json>
    python -m backend.warehouse.role_content merge    aliases  <batch.json> [...]
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

GENERATED_DIR = Path(__file__).resolve().parent / "generated"
TRAITS_FILE = GENERATED_DIR / "role_traits.json"
ROADMAPS_FILE = GENERATED_DIR / "role_roadmaps.json"
ALIASES_FILE = GENERATED_DIR / "role_aliases.json"
KINDS = ("traits", "roadmaps", "aliases")
CATALOG_FILE = Path(__file__).resolve().parent / "generated_catalog.json"
GENERATED_SOURCE = "Generated (model knowledge)"

# --------------------------------------------------------------------------- #
#  Trait dimensions — shared by role profiles and the quiz                     #
# --------------------------------------------------------------------------- #
# Scores are 0-100 and describe a typical EARLY-CAREER version of the role, day to day.
TRAIT_DIMENSIONS: list[tuple[str, str, str]] = [
    # interests — how central this kind of work is to the role
    ("build", "interest", "Designing and writing software or products that ship"),
    ("analyze", "interest", "Investigating data or systems to answer questions"),
    ("math", "interest", "Depth of mathematics or statistics the work relies on"),
    ("design", "interest", "Shaping user experience, interfaces and visuals"),
    ("operate", "interest", "Keeping production systems running, reliable and secure"),
    ("people", "interest", "Working through people (stakeholders, teammates, users) as core work"),
    # work style
    ("ambiguity", "work_style", "0 = well-specified tasks, 100 = open-ended problems you define"),
    ("interruptions", "work_style", "0 = protected focus, 100 = on-call, incidents, constant switching"),
    ("collaboration", "work_style", "0 = mostly solo deep work, 100 = constant coordination"),
    ("research", "work_style", "0 = practical and ship fast, 100 = exploration with uncertain outcomes"),
    ("customer_contact", "work_style", "0 = internal only, 100 = with external customers or users daily"),
]
DIMENSION_IDS = [d[0] for d in TRAIT_DIMENSIONS]

# Calibration anchors: every generator sees these, and they override whatever a batch says for
# these roles, so all 346 profiles share one scale.
TRAIT_ANCHORS: dict[str, dict[str, int]] = {
    "swe": dict(build=90, analyze=40, math=25, design=20, operate=30, people=35, ambiguity=45,
                interruptions=35, collaboration=55, research=25, customer_contact=10),
    "sre": dict(build=50, analyze=55, math=20, design=5, operate=95, people=30, ambiguity=50,
                interruptions=90, collaboration=55, research=20, customer_contact=10),
    "data-sci": dict(build=40, analyze=95, math=80, design=10, operate=10, people=45, ambiguity=70,
                     interruptions=20, collaboration=50, research=65, customer_contact=15),
    "ux": dict(build=30, analyze=35, math=5, design=95, operate=5, people=70, ambiguity=60,
               interruptions=25, collaboration=75, research=50, customer_contact=45),
    "pm": dict(build=25, analyze=55, math=15, design=40, operate=15, people=95, ambiguity=85,
               interruptions=60, collaboration=95, research=35, customer_contact=60),
    "pentester": dict(build=35, analyze=80, math=20, design=5, operate=30, people=30, ambiguity=70,
                      interruptions=30, collaboration=35, research=55, customer_contact=40),
    "research-scientist-ml": dict(build=50, analyze=80, math=95, design=5, operate=5, people=25,
                                  ambiguity=90, interruptions=10, collaboration=40, research=95,
                                  customer_contact=5),
    "support-engineer": dict(build=20, analyze=55, math=5, design=5, operate=50, people=75,
                             ambiguity=35, interruptions=85, collaboration=60, research=10,
                             customer_contact=95),
}

STEP_GRADES = {"A", "I", "B", "prereq"}


def _catalog_roles() -> dict[str, dict]:
    cat = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    return {r["id"]: r for r in cat["roles"]}


# --------------------------------------------------------------------------- #
#  Validation                                                                  #
# --------------------------------------------------------------------------- #
def validate_trait_record(role_id: str, rec: dict) -> list[str]:
    errs = []
    scores = rec.get("scores") if isinstance(rec, dict) else None
    if not isinstance(scores, dict):
        return [f"{role_id}: missing 'scores' object"]
    missing = [d for d in DIMENSION_IDS if d not in scores]
    extra = [k for k in scores if k not in DIMENSION_IDS]
    if missing:
        errs.append(f"{role_id}: missing dimensions {missing}")
    if extra:
        errs.append(f"{role_id}: unknown dimensions {extra}")
    for d in DIMENSION_IDS:
        v = scores.get(d)
        if d in scores and not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100):
            errs.append(f"{role_id}: {d} must be an integer 0-100, got {v!r}")
    summary = rec.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errs.append(f"{role_id}: missing 'summary'")
    elif len(summary) > 300:
        errs.append(f"{role_id}: summary longer than 300 characters")
    return errs


def validate_roadmap_record(role_id: str, rec: dict, role: dict | None) -> list[str]:
    errs = []
    if not isinstance(rec, dict):
        return [f"{role_id}: record must be an object"]
    if not isinstance(rec.get("summary"), str) or not rec["summary"].strip():
        errs.append(f"{role_id}: missing 'summary'")
    phases = rec.get("phases")
    if not isinstance(phases, list) or not 3 <= len(phases) <= 6:
        return errs + [f"{role_id}: 'phases' must be a list of 3-6 phases"]
    seen, total = set(), 0
    for pi, ph in enumerate(phases):
        where = f"{role_id} phase {pi + 1}"
        if not isinstance(ph, dict):
            errs.append(f"{where}: must be an object")
            continue
        if not isinstance(ph.get("title"), str) or not ph["title"].strip():
            errs.append(f"{where}: missing 'title'")
        if not isinstance(ph.get("build"), str) or not ph["build"].strip():
            errs.append(f"{where}: missing 'build' (every phase ends with something built)")
        elif len(ph["build"]) > 300:
            errs.append(f"{where}: 'build' longer than 300 characters")
        steps = ph.get("steps")
        if not isinstance(steps, list) or not 2 <= len(steps) <= 6:
            errs.append(f"{where}: 'steps' must be a list of 2-6 steps")
            continue
        for si, st in enumerate(steps):
            sw = f"{where} step {si + 1}"
            if not isinstance(st, dict):
                errs.append(f"{sw}: must be an object")
                continue
            skill = st.get("skill")
            if not isinstance(skill, str) or not skill.strip():
                errs.append(f"{sw}: missing 'skill'")
            elif skill.strip().lower() in seen:
                errs.append(f"{sw}: duplicate skill {skill!r}")
            else:
                seen.add(skill.strip().lower())
            if not isinstance(st.get("what"), str) or not st["what"].strip():
                errs.append(f"{sw}: missing 'what'")
            elif len(st["what"]) > 200:
                errs.append(f"{sw}: 'what' longer than 200 characters")
            tools = st.get("tools")
            if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                errs.append(f"{sw}: 'tools' must be a list of strings")
            hours = st.get("hours")
            if not (isinstance(hours, int) and not isinstance(hours, bool) and 4 <= hours <= 150):
                errs.append(f"{sw}: 'hours' must be an integer 4-150, got {hours!r}")
            else:
                total += hours
            if st.get("grade") not in STEP_GRADES:
                errs.append(f"{sw}: 'grade' must be one of {sorted(STEP_GRADES)}")
    if not 80 <= total <= 900:
        errs.append(f"{role_id}: total hours {total} outside 80-900")
    if role:
        core = [s["name"] for s in role.get("skills", []) if s.get("importance") == "A"]
        uncovered = [c for c in core if c.strip().lower() not in seen]
        if uncovered:
            errs.append(f"{role_id}: core skills not covered by any step (use the exact name): {uncovered}")
    return errs


def _norm(text: str) -> str:
    from backend.warehouse.taxonomy import normalize_surface
    return normalize_surface(text)


def validate_alias_record(role_id: str, rec: dict, names: dict[str, list[str]]) -> list[str]:
    """``names`` maps each catalog role's normalized name to the role ids carrying it: a role may
    not claim another role's own name as its alias."""
    errs = []
    if not isinstance(rec, dict):
        return [f"{role_id}: record must be an object"]
    titles, fields = rec.get("titles"), rec.get("fields", [])
    if not isinstance(titles, list) or not 3 <= len(titles) <= 15:
        errs.append(f"{role_id}: 'titles' must be a list of 3-15 job titles")
        titles = titles if isinstance(titles, list) else []
    if not isinstance(fields, list) or len(fields) > 6:
        errs.append(f"{role_id}: 'fields' must be a list of at most 6 discipline names")
        fields = fields if isinstance(fields, list) else []
    seen = set()
    for key, items, cap in (("titles", titles, 60), ("fields", fields, 40)):
        for t in items:
            if not isinstance(t, str) or not t.strip() or len(t) > cap:
                errs.append(f"{role_id}: {key} entries must be non-empty strings up to {cap} characters, got {t!r}")
                continue
            n = _norm(t)
            if not n:
                errs.append(f"{role_id}: {key} entry {t!r} is empty after normalization")
            elif n in seen:
                errs.append(f"{role_id}: duplicate alias {t!r}")
            elif n in names and role_id not in names[n]:
                errs.append(f"{role_id}: {t!r} is the name of another role ({', '.join(names[n])}); leave it to that role")
            seen.add(n)
    return errs


def validate_batch(kind: str, batch: dict) -> list[str]:
    roles = batch.get("roles") if isinstance(batch, dict) else None
    if not isinstance(roles, dict) or not roles:
        return ["batch must be an object with a non-empty 'roles' object keyed by role id"]
    catalog = _catalog_roles()
    names: dict[str, list[str]] = {}
    if kind == "aliases":
        for rid, r in catalog.items():
            names.setdefault(_norm(r["name"]), []).append(rid)
    errs = []
    for rid, rec in roles.items():
        if rid not in catalog:
            errs.append(f"{rid}: not a role id in the catalog")
            continue
        if kind == "traits":
            errs += validate_trait_record(rid, rec)
        elif kind == "aliases":
            errs += validate_alias_record(rid, rec, names)
        else:
            errs += validate_roadmap_record(rid, rec, catalog[rid])
    return errs


# --------------------------------------------------------------------------- #
#  Merge into the tracked files                                                 #
# --------------------------------------------------------------------------- #
def _tracked_path(kind: str) -> Path:
    return {"traits": TRAITS_FILE, "roadmaps": ROADMAPS_FILE, "aliases": ALIASES_FILE}[kind]


def load(kind: str) -> dict:
    """The tracked file for ``kind`` ('traits', 'roadmaps' or 'aliases'); empty shell if absent."""
    p = _tracked_path(kind)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"source": GENERATED_SOURCE, "kind": "generated", "roles": {}}


def merge(kind: str, batch_files: list[Path]) -> dict:
    """Validate each batch and fold the valid ones into the tracked file. Invalid batches are
    reported and skipped whole, so a bad batch never half-lands. Trait anchors always win."""
    data = load(kind)
    report = {"merged": [], "rejected": {}}
    for f in batch_files:
        batch = json.loads(Path(f).read_text(encoding="utf-8"))
        errs = validate_batch(kind, batch)
        if errs:
            report["rejected"][str(f)] = errs[:20]
            continue
        data["roles"].update(batch["roles"])
        report["merged"].append(str(f))
    if kind == "traits":
        for rid, scores in TRAIT_ANCHORS.items():
            rec = data["roles"].get(rid, {"summary": ""})
            rec["scores"] = dict(scores)
            data["roles"][rid] = rec
        data["dimensions"] = [{"id": i, "group": g, "meaning": m} for i, g, m in TRAIT_DIMENSIONS]
    data["source"], data["kind"] = GENERATED_SOURCE, "generated"
    data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    _tracked_path(kind).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    report["total_roles"] = len(data["roles"])
    return report


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="role_content")
    ap.add_argument("action", choices=["validate", "merge"])
    ap.add_argument("kind", choices=list(KINDS))
    ap.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    if a.action == "validate":
        batch = json.loads(Path(a.files[0]).read_text(encoding="utf-8"))
        errs = validate_batch(a.kind, batch)
        if errs:
            print(f"INVALID - {len(errs)} problem(s):")
            for e in errs[:40]:
                print("  -", e)
            return 1
        print(f"VALID - {len(batch['roles'])} roles")
        return 0
    rep = merge(a.kind, [Path(f) for f in a.files])
    print(json.dumps(rep, indent=1))
    return 0 if not rep["rejected"] else 1


if __name__ == "__main__":
    sys.exit(_main())
