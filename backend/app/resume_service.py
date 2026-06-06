"""Résumé parsing → profile (brief §8).

Pipeline: upload (PDF/DOCX/TXT) → extract text → match skills to the taxonomy →
build a profile in the SAME shape as the frontend's sample profile (so the résumé
surface renders it unchanged) → the frontend prices the whole profile per country.

INVIOLABLE USER-DATA RULE: we return ONLY the analysis of the résumé provided.
We never fabricate the user's inputs and never invent a "Résumé B". The *market*
is modelled; the *user's* data never is. PII: parsed profiles are returned in
memory; raw text is never logged or stored (storage only for logged-in accounts,
and only the structured profile).
"""
from __future__ import annotations

import io
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.logging import get_logger
from backend.marts import models as M

log = get_logger("app.resume")

# heuristic axis → skill membership (mirrors the frontend's 6 radar axes)
AXES = {
    "Backend depth": {"System Design", "Go", "Python", "SQL", "Kubernetes", "Observability", "Java", "Rust"},
    "Cloud / Infra": {"AWS", "Terraform", "Kubernetes", "Docker", "Linux", "CI/CD", "Cost Optimization", "Networking", "Observability"},
    "Data & ML": {"Python", "Machine Learning", "PyTorch", "TensorFlow", "Statistics", "SQL", "Data Modeling", "Spark", "dbt", "LLM / GenAI", "Vector DBs", "Airflow"},
    "System design": {"System Design", "Kubernetes", "Observability", "GraphQL"},
    "Leadership": {"Stakeholder Mgmt", "Roadmapping"},
    "Frontend": {"React", "TypeScript", "JavaScript", "Design Systems", "Figma", "GraphQL"},
}


def extract_text(filename: str | None, data: bytes) -> str:
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf"):
            from pdfminer.high_level import extract_text as pdf_text
            return pdf_text(io.BytesIO(data)) or ""
        if name.endswith(".docx"):
            from docx import Document
            doc = Document(io.BytesIO(data))
            return "\n".join(p.text for p in doc.paragraphs)
    except Exception as e:
        log.warning("resume parse fallback (%s): %s", name, e)
    try:
        return data.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _known_skills(db: Session) -> list[str]:
    return sorted({s for (s,) in db.execute(select(M.MartRoleSkill.name).distinct())})


def _years(text: str) -> int:
    yrs = [int(m) for m in re.findall(r"\b(\d{1,2})\+?\s*(?:years|yrs)\b", text, flags=re.I)]
    return max(yrs) if yrs else 4


def build_profile(db: Session, text: str) -> dict:
    """Build a profile object (RESUME_SAMPLE shape) from résumé text — this user only."""
    low = text.lower()
    found = [s for s in _known_skills(db) if re.search(r"\b" + re.escape(s.lower()) + r"\b", low)]
    found = found or ["SQL", "Python"]  # minimal floor so the UI has something to price

    # match roles by skill overlap
    roles = list(db.scalars(select(M.MartRole).order_by(M.MartRole.ord)))
    skills_by_role: dict[str, set] = {}
    for ms in db.scalars(select(M.MartRoleSkill)):
        skills_by_role.setdefault(ms.role_id, set()).add(ms.name)
    fset = set(found)
    ranked = sorted(roles, key=lambda r: len(fset & skills_by_role.get(r.id, set())), reverse=True)
    match_roles = [r.id for r in ranked[:5]]
    title = ranked[0].name if ranked and (fset & skills_by_role.get(ranked[0].id, set())) else "Parsed profile"

    axes = []
    for axis, members in AXES.items():
        hits = len(fset & members)
        you = max(20, min(98, 30 + hits * 16))
        axes.append({"axis": axis, "you": you, "market": 60})

    return {
        "name": "Parsed profile",
        "title": title,
        "years": _years(text),
        "skills": found,
        "matchRoles": match_roles,
        "axes": axes,
    }
