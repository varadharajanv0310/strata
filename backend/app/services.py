"""Read-side services: assemble `mock.js`-identical shapes from the serving marts.

The API routers are thin wrappers over these. `assemble_dataset` returns the full
bundle the frontend hydrates from; the granular helpers back the per-surface
endpoints (brief §7). Provenance + confidence travel on every figure.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.marts import models as M


def _country_payload(c: M.MartCountry) -> dict:
    return {
        "code": c.code, "name": c.name, "cur": c.cur, "curCode": c.cur_code,
        "natFactor": c.nat_factor, "pppRate": c.ppp_rate, "transparency": c.transparency,
        "c1": c.c1, "c2": c.c2,
    }


def _score(rc: M.MartRoleCountry) -> dict:
    return {
        "total": rc.score_total, "demand": rc.score_demand, "pay": rc.score_pay,
        "opp": rc.score_opp, "rank": rc.score_rank, "pctile": rc.score_pctile,
    }


def _role_country_payload(rc: M.MartRoleCountry) -> dict:
    return {
        "median": int(rc.median),
        "series": rc.series,
        "demandSeries": rc.demand_series,
        "forecast": rc.forecast,
        "demand": rc.demand,
        "interest": rc.interest,
        "score": _score(rc),
        "sample": rc.sample,
        "conf": rc.conf,
        "kind": rc.kind,
        "source": rc.source,
        "freshness": rc.freshness,
        "transparency": rc.transparency,
    }


def assemble_dataset(db: Session) -> dict:
    countries = list(db.scalars(select(M.MartCountry).order_by(M.MartCountry.ord)))
    families = list(db.scalars(select(M.MartFamily).order_by(M.MartFamily.ord)))
    roles = list(db.scalars(select(M.MartRole).order_by(M.MartRole.ord)))

    skills_by_role: dict[str, list] = defaultdict(list)
    for s in db.scalars(select(M.MartRoleSkill).order_by(M.MartRoleSkill.role_id, M.MartRoleSkill.ord)):
        skills_by_role[s.role_id].append({"name": s.name, "level": s.level, "dura": s.dura, "trend": s.trend})

    ladder_by_role: dict[str, list] = defaultdict(list)
    for l in db.scalars(select(M.MartRoleLadder).order_by(M.MartRoleLadder.role_id, M.MartRoleLadder.ord)):
        ladder_by_role[l.role_id].append([l.title, l.mult])

    rc_by_role: dict[str, dict] = defaultdict(dict)
    for rc in db.scalars(select(M.MartRoleCountry)):
        rc_by_role[rc.role_id][rc.country_code] = _role_country_payload(rc)

    roles_payload = []
    for r in roles:
        roles_payload.append({
            "id": r.id, "name": r.name,
            "family": {"id": r.family_id, "name": r.family_name, "hue": r.family_hue},
            "blurb": r.blurb,
            "skills": skills_by_role.get(r.id, []),
            "ladder": ladder_by_role.get(r.id, []),
            "countries": rc_by_role.get(r.id, {}),
        })

    pulse: dict[str, dict] = defaultdict(lambda: {"hottest": [], "topPay": [], "rising": [], "topScore": []})
    for p in db.scalars(select(M.MartMarketPulse).order_by(M.MartMarketPulse.country_code,
                                                           M.MartMarketPulse.kind, M.MartMarketPulse.ord)):
        pulse[p.country_code][p.kind].append(p.role_id)

    meta = db.get(M.MartMeta, "dataset")
    profiles = db.get(M.MartMeta, "profiles")
    meta_v = meta.value if meta else {"years": [], "fyears": [], "is_seed": True}
    prof_v = profiles.value if profiles else {"sample": {}, "b": {}}

    return {
        "countries": [_country_payload(c) for c in countries],
        "families": [{"id": f.id, "name": f.name, "hue": f.hue} for f in families],
        "roles": roles_payload,
        "years": meta_v.get("years", []),
        "fyears": meta_v.get("fyears", []),
        "marketPulse": {k: dict(v) for k, v in pulse.items()},
        "resume_sample": prof_v.get("sample", {}),
        "resume_b": prof_v.get("b", {}),
        "is_seed": meta_v.get("is_seed", True),
        "generated_at": meta_v.get("generated_at"),
    }


# ---------------- granular helpers ----------------
def get_meta(db: Session) -> dict:
    meta = db.get(M.MartMeta, "dataset")
    return meta.value if meta else {}


def list_roles(db: Session, q: str | None = None, family: str | None = None) -> list[dict]:
    """Search / browse roles. A query is routed through the never-dead-end resolver
    (exact → fuzzy → embedding) so a miss returns the *nearest* roles, never ``[]``."""
    if q and q.strip():
        from backend.app.resolver import get_resolver
        results = get_resolver(db).resolve(q, limit=50)["results"]
        if family and family != "all":
            results = [r for r in results if r["family"]["id"] == family]
        return [{"id": r["id"], "name": r["name"], "family": r["family"], "blurb": r["blurb"]}
                for r in results]
    roles = list(db.scalars(select(M.MartRole).order_by(M.MartRole.ord)))
    out = []
    for r in roles:
        if family and family != "all" and r.family_id != family:
            continue
        out.append({"id": r.id, "name": r.name,
                    "family": {"id": r.family_id, "name": r.family_name, "hue": r.family_hue},
                    "blurb": r.blurb})
    return out


def resolve_roles(db: Session, q: str, limit: int = 8) -> dict:
    """Full resolver payload (confidence + honest copy + ranked candidates)."""
    from backend.app.resolver import get_resolver
    return get_resolver(db).resolve(q, limit=limit)


def typeahead_roles(db: Session, q: str, limit: int = 8) -> list[dict]:
    """Per-keystroke suggestions; never blanks for a non-trivial prefix."""
    from backend.app.resolver import get_resolver
    return get_resolver(db).typeahead(q, limit=limit)


def get_role(db: Session, role_id: str) -> dict | None:
    r = db.get(M.MartRole, role_id)
    if not r:
        return None
    skills = [{"name": s.name, "level": s.level, "dura": s.dura, "trend": s.trend}
              for s in db.scalars(select(M.MartRoleSkill).where(M.MartRoleSkill.role_id == role_id).order_by(M.MartRoleSkill.ord))]
    ladder = [[l.title, l.mult]
              for l in db.scalars(select(M.MartRoleLadder).where(M.MartRoleLadder.role_id == role_id).order_by(M.MartRoleLadder.ord))]
    countries = {rc.country_code: _role_country_payload(rc)
                 for rc in db.scalars(select(M.MartRoleCountry).where(M.MartRoleCountry.role_id == role_id))}
    return {"id": r.id, "name": r.name,
            "family": {"id": r.family_id, "name": r.family_name, "hue": r.family_hue},
            "blurb": r.blurb, "skills": skills, "ladder": ladder, "countries": countries}


def jobscore_board(db: Session, country: str, limit: int | None = None) -> list[dict]:
    rows = list(db.scalars(
        select(M.MartRoleCountry).where(M.MartRoleCountry.country_code == country)
        .order_by(M.MartRoleCountry.score_total.desc())
    ))
    roles = {r.id: r for r in db.scalars(select(M.MartRole))}
    out = []
    for rc in rows:
        r = roles.get(rc.role_id)
        out.append({
            "id": rc.role_id, "name": r.name if r else rc.role_id,
            "family": {"id": r.family_id, "name": r.family_name, "hue": r.family_hue} if r else None,
            "median": int(rc.median), "demand": rc.demand, "interest": rc.interest,
            "score": _score(rc),
        })
    return out[:limit] if limit else out


def provenance(db: Session, role_id: str, country: str) -> dict | None:
    rc = db.get(M.MartRoleCountry, (role_id, country))
    if not rc:
        return None
    out = {
        "role_id": role_id, "country": country,
        "source": rc.source, "sample": rc.sample, "confidence": rc.conf,
        "kind": rc.kind, "freshness": rc.freshness, "transparency": rc.transparency,
        "is_seed": rc.is_seed,
    }
    # thread the full lineage tuple from the provenance manifest (best-effort join
    # on source name; the manifest table may not exist on older marts)
    try:
        prov = db.scalars(
            select(M.MartProvenance).where(M.MartProvenance.source_name == rc.source)
        ).first()
        if prov:
            out.update({
                "snapshot_hash": prov.snapshot_hash or None,
                "transform_version": prov.transform_version,
                "row_count": prov.row_count,
                "as_of": prov.as_of or None,
            })
    except Exception:  # noqa: BLE001 — mart_provenance absent on legacy marts
        pass
    return out


def list_provenance(db: Session) -> list[dict]:
    """The full per-source provenance manifest (the /data 'receipts' surface)."""
    try:
        rows = db.scalars(select(M.MartProvenance).order_by(M.MartProvenance.source_id)).all()
    except Exception:  # noqa: BLE001
        return []
    return [{"source_id": p.source_id, "source_name": p.source_name, "kind": p.kind,
             "snapshot_hash": p.snapshot_hash, "transform_version": p.transform_version,
             "row_count": p.row_count, "as_of": p.as_of} for p in rows]


def market_pulse(db: Session, country: str) -> dict:
    pulse = {"hottest": [], "topPay": [], "rising": [], "topScore": []}
    for p in db.scalars(select(M.MartMarketPulse).where(M.MartMarketPulse.country_code == country)
                        .order_by(M.MartMarketPulse.kind, M.MartMarketPulse.ord)):
        pulse[p.kind].append(p.role_id)
    return pulse


def list_countries(db: Session) -> list[dict]:
    return [_country_payload(c) for c in db.scalars(select(M.MartCountry).order_by(M.MartCountry.ord))]
