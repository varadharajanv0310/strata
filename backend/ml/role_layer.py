"""Generated role descriptive layer — canonical skills, description, seniority bands.

Per the sparse-LLM decision (reports/SPARSE_LLM_PLAN.md), the DESCRIPTIVE layer of a role
(its typical skills, its "what the work is like" blurb, and a seniority years-BAND per ladder
rung) is **GENERATED** — one LLM generation per role — rather than measured from per-posting
extraction over the whole corpus. These are stable, role-level constants any capable model
answers correctly in one shot; extracting them from ~870K postings would cost days for
outputs that are either generatable (this) or noise we deliberately drop.

HONESTY (the non-negotiable): generated content must NEVER masquerade as measured. Every row
this writes carries ``source="llm-generated"``, ``kind="generated"``, the model id, and a
timestamp. Downstream, the fuse layers it under ``dim_role.source_id="llm-generated"`` — NOT
``is_seed`` (wrong lifecycle: seed is retired when real data lands; generated is durable) — so
``services.provenance`` reports it as generated, distinguishable from anything read from data.

ROLES-ONLY: role_id + role name in, descriptive constants out. No employer/company anywhere.
NO EXACT YEARS as a product surface: seniority is emitted as bands (yoe_min/yoe_max per rung),
never a single number.

BUILD-ONLY: real + runnable, imports cleanly without a GPU, but is NOT executed in the
planning pass. ``run()`` generates the layer for the curated + derived roles and writes
``staging/generated/role_layer.json``; ``load_role_layer()`` reads it back for the fuse.
"""
from __future__ import annotations

import datetime
import json

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ml.role_layer")

OUT_REL = "generated/role_layer.json"
GENERATED_SOURCE = "llm-generated"     # dim_source.code for the generated layer

# Grammar for one role's generated descriptive record (constrained JSON output).
ROLE_LAYER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "description": {"type": "string"},
        "skills": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "importance": {"type": "string", "enum": ["A", "I", "B"]},
                },
                "required": ["name", "importance"],
            },
        },
        "rungs": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "yoe_min": {"type": "integer"},
                    "yoe_max": {"type": "integer"},
                },
                "required": ["title", "yoe_min", "yoe_max"],
            },
        },
    },
    "required": ["description", "skills", "rungs"],
}

_SYSTEM = (
    "You produce concise, accurate DESCRIPTIVE constants for a technology job ROLE — never "
    "about any company. Given the role name and its seniority ladder, return: a 1-2 sentence "
    "description of what the role is actually like; 6-10 canonical skills, each tagged A (core "
    "/ defines the role), I (important), or B (nice-to-have); and, for EACH ladder rung, a "
    "typical years-of-experience BAND (yoe_min, yoe_max) — a range, never a single number, "
    "because people take widely varying paths. Be honest and generic; do not invent narrow "
    "specifics. Return ONLY the JSON object."
)


def _roles_to_generate() -> list[dict]:
    """The roles to generate for: the curated ROLE_DEFS (id/name/ladder rung titles) plus any
    derived-role clusters that have landed (role_derivation output), deduped by role_id."""
    from backend.warehouse.seed import ROLE_DEFS
    out: list[dict] = []
    seen: set[str] = set()
    for d in ROLE_DEFS:
        rungs = [t for (t, _mult) in d.get("ladder", [])]
        out.append({"role_id": d["id"], "name": d["name"], "rungs": rungs})
        seen.add(d["id"])
    # derived roles (from clustering) — descriptive layer for the breadth catalog
    derived = settings.staging_dir / "normalized" / "derived_roles.parquet"
    if derived.exists():
        try:
            import pandas as pd
            dr = pd.read_parquet(derived)
            name_col = next((c for c in ("role_name", "name", "label", "canonical_title")
                             if c in dr.columns), None)
            id_col = next((c for c in ("role_id", "id", "cluster_id") if c in dr.columns), None)
            if name_col and id_col:
                for _, r in dr.iterrows():
                    rid = str(r[id_col])
                    if rid and rid not in seen:
                        seen.add(rid)
                        out.append({"role_id": rid, "name": str(r[name_col]), "rungs": []})
        except Exception as e:  # noqa: BLE001 — derived layer optional
            log.info("role_layer: derived_roles unreadable (%s) — curated only", e)
    return out


def _generate_one(role: dict, model: str) -> dict | None:
    """One LLM generation for a role → validated descriptive record, or None on failure."""
    from backend.ml.llm_extract import ollama_chat
    rungs = role.get("rungs") or ["Junior", "Mid", "Senior", "Staff", "Principal"]
    user = (f"ROLE: {role['name']}\nSENIORITY LADDER (rungs, low→high): {', '.join(rungs)}\n\n"
            "Return the JSON object (description, skills[], rungs[] with yoe bands).")
    try:
        raw = ollama_chat([{"role": "system", "content": _SYSTEM},
                           {"role": "user", "content": user}],
                          model=model, fmt=ROLE_LAYER_SCHEMA, num_ctx=settings.ollama_num_ctx)
        obj = json.loads(raw)
    except Exception as e:  # noqa: BLE001 — a bad generation must not sink the batch
        log.warning("role_layer: generation failed for %s (%s)", role["role_id"], e)
        return None
    if not obj.get("description") or not obj.get("skills"):
        return None
    return obj


def run(model: str | None = None, roles: list[dict] | None = None) -> dict:
    """Generate the descriptive layer for every role → staging/generated/role_layer.json.

    Each written record is provenance-stamped ``source='llm-generated'`` / ``kind='generated'``
    so it can never be mistaken for measured data. Idempotent: overwrites the layer file.
    NOT run in the planning pass — this is the ready-to-run generator.
    """
    model = model or settings.ollama_model
    roles = roles or _roles_to_generate()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    records: list[dict] = []
    for role in roles:
        obj = _generate_one(role, model)
        if obj is None:
            continue
        records.append({
            "role_id": role["role_id"], "name": role["name"],
            "description": obj["description"],
            "skills": obj["skills"],                 # [{name, importance A|I|B}]
            "rungs": obj.get("rungs", []),           # [{title, yoe_min, yoe_max}]
            "source": GENERATED_SOURCE, "kind": "generated",
            "model": model, "generated_at": now,
        })
    out = settings.staging_dir / OUT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")
    summary = {"roles_in": len(roles), "generated": len(records), "source": GENERATED_SOURCE,
               "written": bool(records), "out": str(out)}
    log.info("role_layer: %s", summary)
    return summary


def load_role_layer() -> list[dict]:
    """Read the generated descriptive layer back (for the fuse). [] if not generated yet."""
    p = settings.staging_dir / OUT_REL
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log.warning("role_layer: %s unreadable (%s)", OUT_REL, e)
        return []


if __name__ == "__main__":  # pragma: no cover — the ready-to-run generator (not run in-pass)
    print(json.dumps(run(), indent=2))
