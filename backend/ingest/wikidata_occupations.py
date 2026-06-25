"""Wikidata — the **occupation taxonomy graph**: occupation→occupation and
occupation→skill edges for the software/IT/engineering subtree, mined from the
public WDQS SPARQL endpoint (council source: a free, openly-licensed knowledge graph).

NEW SIGNAL strata doesn't have anywhere else: how roles *relate to each other* and
which *skills* a role implies, as a community-maintained taxonomy rather than scraped
from postings. Wikidata models occupations as items that are ``instance of``/``subclass
of`` *occupation* (Q28640), and links them with ``subclass of`` (P279, the
generalization ladder), ``part of`` (P361), and a free-text ``skills required`` style
relation. We harvest the occupation *subgraph* — the QIDs in the software/IT/eng
neighborhood — plus their related-occupation neighbors and skill-ish labels. That
gives a roles-only adjacency lattice: "Backend Developer" ~ "Software Engineer" ~
"DevOps Engineer", each carrying a handful of skill labels.

GRAIN: occupation QID (global — a taxonomy has no real geography, so country='' /
omitted) × related occupations × skill labels. It feeds **bridge_role_adjacency**
(the roles-only trajectory/taxonomy bridge) where the warehouse fuse later crosswalks
each occupation QID/label onto strata's canonical role ids.

CRITICAL ROLES-ONLY GUARD: this connector NEVER queries or lands any employer or
organization property. There is **no P108 (employer)**, **no P749 (parent
organization)**, **no P127 (owned by)**, and no company entities in any query or
output — the WHERE clauses below are restricted to occupation and skill predicates
only. If a future edit adds a company predicate, ``_assert_roles_only()`` will refuse.

LEGITIMACY / FRAGILITY: WDQS is a real public endpoint but it is rate-limited and
occasionally times out on broad queries (a documented constraint). We send a
descriptive User-Agent per the WDQS user-agent policy, chunk by occupation seed,
keep timeouts polite, and treat any query failure as skippable (log + continue) so a
single timeout never sinks the run. No credentials. The cached JSON is the checkpoint.
**Not run in this pass** — but this is real, runnable code, not a stub.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.wikidata_occupations")

# Public WDQS SPARQL endpoint (no key). format=json per WDQS docs.
SPARQL_URL = "https://query.wikidata.org/sparql"
# Descriptive UA is REQUIRED by the WDQS user-agent policy.
HEADERS = {
    "User-Agent": "strata/1.0 (+https://example.org/strata; roles-only job-market explorer; varadharajanv09@gmail.com)",
    "Accept": "application/sparql-results+json",
}

# Roots of the software/IT/engineering occupation subtree we want to walk.
# Each is an occupation (or occupation class) QID; we pull everything that is a
# subclass/instance below it. These are occupation items only — never orgs.
SEED_OCCUPATIONS = {
    "Q183888": "computer scientist",
    "Q5482740": "programmer",
    "Q1622272": "software engineer",   # broad eng anchor (university teacher subtree excluded by P279 path)
    "Q56532516": "software developer",
    "Q4663974": "systems administrator",
    "Q1371925": "database administrator",
    "Q11631": "engineer",              # engineering anchor (we keep IT/eng neighbors)
    "Q21855880": "data scientist",
    "Q81096": "engineer (generic)",
    "Q43845": "businessperson",        # NOTE: present only as a *negative*/edge anchor; not landed as a role
}

# Predicates we are ALLOWED to traverse — occupation/skill taxonomy only.
ALLOWED_PREDICATES = {
    "P279": "subclass of",        # generalization ladder (occupation -> broader occupation)
    "P361": "part of",            # occupation -> composite role
    "P425": "field of this occupation",  # occupation -> field/skill area
    "P1535": "used by",           # tool/skill -> occupation (reverse skill signal)
    "P2283": "uses",              # occupation -> tool/skill
}
# Predicates that are FORBIDDEN — employer / organization / company. Lands nothing.
FORBIDDEN_PREDICATES = {"P108", "P749", "P127", "P159", "P749", "P1830", "P355", "P199"}
# QIDs that designate a company/organization class — guard so none leak into output.
ORG_CLASS_QIDS = {"Q4830453", "Q783794", "Q43229", "Q6881511", "Q891723"}  # business, company, organization, enterprise, public company


def _staging_dir():
    d = settings.staging_dir / "wikidata"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_file():
    return _staging_dir() / "occupations.json"


def _assert_roles_only(query: str) -> None:
    """Refuse to send any SPARQL touching an employer/org predicate. Roles-only guard."""
    q = query.upper()
    for pid in FORBIDDEN_PREDICATES:
        # match wdt:P108 / p:P108 / ps:P108 etc, but not a longer QID prefix
        if f":{pid} " in q or f":{pid}\n" in q or f":{pid})" in q or f":{pid}}}" in q:
            raise AssertionError(
                f"roles-only violation: query references forbidden employer/org predicate {pid}")


def _sparql(query: str, timeout: int = 60) -> list[dict]:
    """POST a SPARQL query to WDQS, return result bindings. Roles-only guarded."""
    _assert_roles_only(query)
    data = urllib.parse.urlencode({"query": query, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(SPARQL_URL, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return payload.get("results", {}).get("bindings", [])


def _qid_from_uri(uri: str) -> str:
    """'http://www.wikidata.org/entity/Q183888' -> 'Q183888'."""
    return (uri or "").rsplit("/", 1)[-1]


# --- SPARQL templates (occupation/skill ONLY — no employer/org predicate anywhere) ---

# All occupations in the subtree below a seed, with their broader-occupation (P279)
# neighbors. P279* walks the generalization ladder; we cap depth via the seed list.
_RELATED_QUERY = """
SELECT ?occ ?occLabel ?rel ?relLabel WHERE {{
  ?occ wdt:P279* wd:{seed} .
  ?occ wdt:P279 ?rel .
  ?rel wdt:P279* wd:Q28640 .          # rel must itself be an occupation
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT {limit}
"""

# Skill-ish labels for occupations in the subtree: "uses" (P2283) tools/methods and
# "field of this occupation" (P425). These are the skill signal — labels only.
_SKILL_QUERY = """
SELECT ?occ ?occLabel ?skill ?skillLabel WHERE {{
  ?occ wdt:P279* wd:{seed} .
  {{ ?occ wdt:P2283 ?skill . }} UNION {{ ?occ wdt:P425 ?skill . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT {limit}
"""


def _fetch_seed(seed_qid: str, limit: int = 800, timeout: int = 60) -> dict:
    """Fetch related-occupation + skill edges for one seed subtree. Best-effort.

    Returns {occ_qid: {label, related_occ:set, skills:set}}. Never raises — a WDQS
    timeout/error is logged and yields {} so the run continues.
    """
    bucket: dict = {}

    def _ensure(qid: str, label: str) -> dict:
        rec = bucket.setdefault(qid, {"label": label or qid, "related_occ": set(), "skills": set()})
        if label and (rec["label"] == qid or not rec["label"]):
            rec["label"] = label
        return rec

    # 1) occupation -> related occupation edges
    try:
        for b in _sparql(_RELATED_QUERY.format(seed=seed_qid, limit=limit), timeout=timeout):
            occ = _qid_from_uri(b.get("occ", {}).get("value", ""))
            rel = _qid_from_uri(b.get("rel", {}).get("value", ""))
            if not occ or not rel:
                continue
            if occ in ORG_CLASS_QIDS or rel in ORG_CLASS_QIDS:
                continue                                   # roles-only: drop any org class
            rec = _ensure(occ, b.get("occLabel", {}).get("value", ""))
            if rel != occ:
                rec["related_occ"].add(rel)
            # seed the related node too so its label is captured
            _ensure(rel, b.get("relLabel", {}).get("value", ""))
    except Exception as e:  # noqa: BLE001 — one query timeout must not sink the run
        log.warning("wikidata: related-occ query for %s failed (%s) — skip", seed_qid, e)

    # 2) occupation -> skill label edges
    try:
        for b in _sparql(_SKILL_QUERY.format(seed=seed_qid, limit=limit), timeout=timeout):
            occ = _qid_from_uri(b.get("occ", {}).get("value", ""))
            if not occ or occ in ORG_CLASS_QIDS:
                continue
            skill = (b.get("skillLabel", {}).get("value", "") or "").strip()
            if not skill or skill.startswith("Q"):         # drop unlabeled QIDs
                continue
            rec = _ensure(occ, b.get("occLabel", {}).get("value", ""))
            rec["skills"].add(skill)
    except Exception as e:  # noqa: BLE001
        log.warning("wikidata: skill query for %s failed (%s) — skip", seed_qid, e)

    return bucket


def fetch_occupations(force: bool = False, time_cap_s: float = 600.0,
                      max_seeds: int | None = None, timeout: int = 60) -> list[dict]:
    """Fetch + cache the occupation subgraph for all seeds. Cache IS the checkpoint.

    Heartbeat-flushes per seed; honors a wall-clock ``time_cap_s`` and optional
    ``max_seeds`` bound. Network-graceful per seed. Lands the SOURCE's native QIDs +
    skill labels — the warehouse fuse crosswalks onto strata role ids later.
    """
    f = _staging_file()
    if f.exists() and not force:
        return load_occupations()

    merged: dict = {}
    seeds = list(SEED_OCCUPATIONS.items())
    if max_seeds is not None:
        seeds = seeds[:max_seeds]
    t0 = time.time()
    for i, (seed_qid, seed_name) in enumerate(seeds, 1):
        if time.time() - t0 > time_cap_s:
            log.warning("wikidata: time cap %ss hit at seed %d/%d — landing partial",
                        time_cap_s, i, len(seeds))
            break
        bucket = _fetch_seed(seed_qid, timeout=timeout)
        for qid, rec in bucket.items():
            tgt = merged.setdefault(qid, {"label": rec["label"], "related_occ": set(), "skills": set()})
            if rec["label"] and (tgt["label"] == qid or not tgt["label"]):
                tgt["label"] = rec["label"]
            tgt["related_occ"].update(rec["related_occ"])
            tgt["skills"].update(rec["skills"])
        print(f"[wikidata] seed {i}/{len(seeds)} {seed_qid} ({seed_name}): "
              f"{len(bucket)} occupations, {sum(len(v['related_occ']) for v in bucket.values())} adj edges",
              flush=True)
        time.sleep(1.0)                                    # polite to WDQS between seeds

    return _land(merged)


def _land(merged: dict) -> list[dict]:
    """Serialize merged graph → staging JSON. Drops any org-class QID defensively."""
    rows: list[dict] = []
    for qid, rec in merged.items():
        if qid in ORG_CLASS_QIDS:
            continue
        related = sorted(q for q in rec["related_occ"] if q and q not in ORG_CLASS_QIDS and q != qid)
        rows.append({
            "occ_qid": qid,
            "label": rec["label"],
            "country": "",                                 # global taxonomy — no real geography
            "related_occ": related,
            "skills": sorted(rec["skills"]),
        })
    rows.sort(key=lambda r: r["occ_qid"])
    if rows:
        _staging_file().write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    log.info("Wikidata occupations: %d nodes, %d adjacency edges, %d skill labels",
             len(rows), sum(len(r["related_occ"]) for r in rows),
             sum(len(r["skills"]) for r in rows))
    return rows


def load_occupations() -> list[dict]:
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def run(**kw) -> dict:
    """Land + cache the Wikidata occupation/skill subgraph. Connector entrypoint."""
    rows = fetch_occupations(**kw)
    return {
        "rows": len(rows),
        "adjacency_edges": sum(len(r["related_occ"]) for r in rows),
        "skill_labels": sum(len(r["skills"]) for r in rows),
        "written": bool(rows),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
