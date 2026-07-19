"""Corpus-free role **universe** — mint canonical role NODES from the O*NET occupation
tables already on disk, so the catalog is *search-defined* (taxonomy), not corpus-bounded.

This is the taxonomy node-admission contributor described in
``reports/CATALOG_DECOUPLE_PLAN.md`` (Move B). Today the O*NET zip in ``staging/onet/``
holds 1,016 occupations / 55,120 alternate titles, but the alias loader keeps only titles
whose SOC matches one of ~10 curated crosswalk anchors — so 867 occupations collapse onto
16 nodes *as aliases* and never become nodes. That is a corpus-as-boundary applied to a
taxonomy: our own 16-role crosswalk decides which occupations may exist.

This module lifts that boundary. It reads the O*NET **Occupation Data** + **Technology
Skills** tables (zero fetch, zero corpus, identical for all 7 markets incl. India), filters
to the tech occupations, DROPS any occupation a curated role already owns (that SOC is a
curated crosswalk anchor — the hand-curated node wins), and returns node candidates with
real O*NET skills, an official blurb, and a SOC crosswalk anchor.

The warehouse fuse (``build_warehouse_from_staging``) admits these **additively**
(``ON CONFLICT DO NOTHING``, under a disjoint ``onet:`` id namespace) alongside the derived
clusters — the curated 16 are never touched. Graceful: returns ``[]`` when the zip is
absent, so the build never fails on a missing reference file.

ROLES-ONLY: occupations, titles, occupation codes and skills — no employer/company axis.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from pathlib import Path

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("warehouse.role_universe")

# Id namespace for taxonomy-admitted occupation nodes. Kept disjoint from curated dim_role
# ids (bare slugs like 'swe'), role_derivation's 'derived:' ids, and the 'emerging:' miner
# ids — so an admitted occupation can never shadow a curated or mined node.
ONET_PREFIX = "onet:"
FAMILY_ID = "onet"
FAMILY_NAME = "Occupation (O*NET)"
FAMILY_HUE = 210

_ONET_ZIP_REL = ("onet", "onet_db.zip")


def _tech_soc(soc: str) -> bool:
    """The tech occupation filter: SOC major group 15 (Computer & Mathematical) plus
    Computer & Information Systems Managers (11-3021) and Computer Hardware Engineers
    (17-2061). Deliberately excludes generic electrical/electronics engineering and
    non-tech math-adjacent occupations to keep the universe a *tech* job-market catalog."""
    soc = (soc or "").strip()
    return soc.startswith("15-") or soc[:7] in ("11-3021", "17-2061")


# ---- skill-name normalization (O*NET tool labels → recognizable skill names) --------- #
_SKILL_RENAME = {
    "structured query language sql": "SQL",
    "oracle java": "Java",
    "rust programming language": "Rust",
    "amazon web services aws software": "AWS",
    "microsoft azure software": "Azure",
    "microsoft power bi": "Power BI",
    "microsoft sql server": "SQL Server",
    "microsoft excel": "Excel",
    "microsoft sharepoint": "SharePoint",
    "microsoft visio": "Visio",
    "microsoft powerpoint": "PowerPoint",
    "atlassian jira": "Jira",
    "atlassian confluence": "Confluence",
    "apache spark": "Apache Spark",
    "apache hadoop": "Hadoop",
    "apache kafka": "Kafka",
    "apache airflow": "Airflow",
    "google angular": "Angular",
    "sap software": "SAP",
    "low-level virtual machine llvm compilers": "LLVM",
}
# generic O*NET category labels — not real named skills, dropped
_SKILL_DROP = {
    "operating system software", "microsoft office software", "software libraries",
    "source code editor software", "web framework software",
    "development environment software", "object oriented development environment software",
    "enterprise application integration software", "data base management system software",
    "data base user interface and query software", "web platform development software",
    "spreadsheet software", "presentation software", "word processing software",
    "electronic mail software", "office suite software", "analytical or scientific software",
}


def _norm_skill(raw: str) -> str | None:
    """Normalize an O*NET technology-tool label to a clean skill name (or None to drop)."""
    s = (raw or "").strip()
    if not s:
        return None
    key = s.lower()
    if key in _SKILL_DROP:
        return None
    if key in _SKILL_RENAME:
        return _SKILL_RENAME[key]
    # strip a trailing generic " software" qualifier: 'Ansible software' -> 'Ansible'
    if key.endswith(" software"):
        s2 = s[: -len(" software")].strip()
        if s2:
            s = s2
    # drop noisy over-long labels (whole phrases, not skills)
    if len(s) > 40:
        return None
    return s


def _zip_path(zip_path: Path | None) -> Path:
    return zip_path or (settings.staging_dir.joinpath(*_ONET_ZIP_REL))


def _member(zf: zipfile.ZipFile, needle: str) -> str | None:
    return next((n for n in zf.namelist()
                 if needle in n.lower() and n.lower().endswith((".txt", ".csv"))), None)


def _blurb(description: str, limit: int = 240) -> str:
    """Trim an O*NET description to a compact blurb at a sentence/word boundary."""
    d = " ".join((description or "").split())
    if len(d) <= limit:
        return d
    cut = d[:limit]
    dot = cut.rfind(". ")
    if dot >= 80:
        return cut[: dot + 1]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp >= 80 else cut).rstrip(" ,;:") + "…"


def load_onet_occupations(zip_path: Path | None = None) -> list[dict]:
    """Read O*NET 'Occupation Data' (SOC, Title, Description) → tech occupations only.

    Returns ``[{soc, title, description}]`` (graceful ``[]`` when the zip/member absent)."""
    zp = _zip_path(zip_path)
    if not zp.exists():
        log.warning("O*NET zip absent (%s) — no taxonomy occupation nodes [TODO: fetch O*NET DB]", zp)
        return []
    out: list[dict] = []
    try:
        with zipfile.ZipFile(zp) as zf:
            member = _member(zf, "occupation data")
            if not member:
                log.warning("O*NET zip has no 'Occupation Data' member — skipping (members: %s)",
                            zf.namelist()[:5])
                return []
            with zf.open(member) as fh:
                reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"),
                                    delimiter="\t")
                next(reader, None)  # header
                for row in reader:
                    if len(row) < 2:
                        continue
                    soc, title = row[0].strip(), row[1].strip()
                    desc = row[2].strip() if len(row) > 2 else ""
                    if title and _tech_soc(soc):
                        out.append({"soc": soc, "title": title, "description": desc})
    except Exception as e:  # noqa: BLE001 — a malformed reference file must not break the build
        log.error("O*NET occupation parse failed: %s", e)
        return []
    log.info("O*NET occupations → %d tech occupations", len(out))
    return out


def load_onet_indemand_skills(zip_path: Path | None = None, cap: int = 12) -> dict[str, list[str]]:
    """Read O*NET 'Technology Skills' → the most *distinctive* In-Demand tools per SOC.

    In-Demand is O*NET's own relevance flag — a tight, recognizable set (Python, SQL,
    Docker, Kubernetes, React, …). But O*NET lists tools alphabetically with no relevance
    rank, so a naive top-N drops a role's *defining* tool (Solidity for Blockchain
    Engineers) in favour of ubiquitous ones (AWS, Excel) that appear for every occupation.
    So we rank by **distinctiveness**: a tool's document frequency across all tech SOCs —
    rarer tool first (Solidity, df≈1, leads; AWS, high df, trails). Hot-Technology breaks
    ties; Hot-only tools backfill a SOC with too few In-Demand entries. Returns
    ``{soc: [skill, ...]}`` (graceful ``{}`` when absent)."""
    zp = _zip_path(zip_path)
    if not zp.exists():
        return {}
    indemand: dict[str, list[str]] = defaultdict(list)
    hot: dict[str, list[str]] = defaultdict(list)
    is_hot_skill: dict[str, bool] = {}
    seen: dict[str, set[str]] = defaultdict(set)
    df: dict[str, int] = defaultdict(int)          # distinct SOCs each In-Demand tool serves
    try:
        with zipfile.ZipFile(zp) as zf:
            member = _member(zf, "technology skills")
            if not member:
                return {}
            with zf.open(member) as fh:
                reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"),
                                    delimiter="\t")
                next(reader, None)  # header: SOC, Example, Commodity Code, Commodity Title, Hot, In Demand
                for row in reader:
                    if len(row) < 6:
                        continue
                    soc = row[0].strip()
                    if not _tech_soc(soc):
                        continue
                    name = _norm_skill(row[1])
                    if not name:
                        continue
                    lk = name.lower()
                    if lk in seen[soc]:
                        continue
                    seen[soc].add(lk)
                    is_hot = row[4].strip().upper() == "Y"
                    is_indemand = row[5].strip().upper() == "Y"
                    is_hot_skill[lk] = is_hot_skill.get(lk, False) or is_hot
                    if is_indemand:
                        indemand[soc].append(name)
                        df[lk] += 1
                    elif is_hot:
                        hot[soc].append(name)
    except Exception as e:  # noqa: BLE001
        log.error("O*NET technology-skill parse failed: %s", e)
        return {}

    def _rank(name: str) -> tuple:
        lk = name.lower()
        return (df.get(lk, 999), 0 if is_hot_skill.get(lk) else 1, name.lower())

    out: dict[str, list[str]] = {}
    for soc in set(indemand) | set(hot):
        picks = sorted(indemand.get(soc, []), key=_rank)[:cap]
        if len(picks) < cap:
            picks += [h for h in sorted(hot.get(soc, []), key=_rank) if h not in picks][
                : cap - len(picks)]
        if picks:
            out[soc] = picks
    return out


def curated_soc_anchors() -> set[str]:
    """The exact O*NET-SOC codes already owned by a curated role (GOV_CROSSWALK onet_soc
    anchors). An O*NET occupation on one of these is the SAME node as a curated role — the
    hand-curated node wins, so the assembler skips it (no near-dupe)."""
    from backend.warehouse.taxonomy import GOV_CROSSWALK
    anchors: set[str] = set()
    for systems in GOV_CROSSWALK.values():
        code = systems.get("onet_soc", (None, None))[0]
        if code:
            anchors.add(code.strip())
    return anchors


def assemble_universe_nodes(zip_path: Path | None = None, cap_skills: int = 12) -> list[dict]:
    """Assemble the corpus-free occupation node set.

    For each O*NET tech occupation whose SOC is NOT already a curated crosswalk anchor,
    build a node dict::

        {role_id, name, blurb, family_id, family_name, family_hue, soc, skills:[...],
         provenance}

    ``role_id`` is ``onet:<slug(title)>`` (disjoint namespace). De-duplicated on role_id.
    Skills are real O*NET In-Demand tools. Graceful ``[]`` when the O*NET zip is absent.
    """
    from backend.warehouse.build import slug

    occs = load_onet_occupations(zip_path)
    if not occs:
        return []
    skills_by_soc = load_onet_indemand_skills(zip_path, cap=cap_skills)
    anchors = curated_soc_anchors()

    nodes: list[dict] = []
    seen_ids: set[str] = set()
    skipped_curated = 0
    for occ in occs:
        soc, title = occ["soc"], occ["title"]
        if "all other" in title.lower():
            continue  # O*NET residual catch-all bucket, not a searchable role
        if soc in anchors:
            skipped_curated += 1
            continue  # a curated role already owns this SOC — hand-curated node wins
        rid = ONET_PREFIX + slug(title)
        if not rid or rid == ONET_PREFIX or rid in seen_ids:
            continue
        seen_ids.add(rid)
        # skills for this occupation, or its base SOC (…​.00) if the detailed code has none
        sk = skills_by_soc.get(soc) or skills_by_soc.get(soc[:7] + ".00") or []
        nodes.append({
            "role_id": rid,
            "name": title,
            "blurb": _blurb(occ.get("description", "")),
            "family_id": FAMILY_ID,
            "family_name": FAMILY_NAME,
            "family_hue": FAMILY_HUE,
            "soc": soc,
            "skills": sk,
            "provenance": "onet-occupation",
        })
    log.info("role universe → %d occupation nodes admitted (%d skipped as curated anchors)",
             len(nodes), skipped_curated)
    return nodes
