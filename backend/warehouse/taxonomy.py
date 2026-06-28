"""Role taxonomy — canonical nodes + alias graph + government-code crosswalk.

This is the structural spine for *near-total role coverage* (see the brainstorm).
The model separates three things that title strings smear together:

  * **Canonical role node** — the unit of analysis (lives in ``dim_role``). Few
    thousand at full scale; 16 curated today.
  * **Alias graph** (``dim_role_alias``) — the 10-40+ surface forms a human might
    type for one node ("SDET", "Test Automation Engineer", "QA Automation" → qa).
    This is ~80% of the product: it's what makes the resolver never dead-end.
  * **Orthogonal axes** — seniority (``dim_seniority``) and specialization
    (``dim_specialization``) are *axes*, NOT nodes. "Senior Data Engineer" is
    ``data-eng @ senior``, not a separate role; that's how 16 nodes stay 16 and a
    future 3,800 don't explode into 30,000 near-dupes.

Plus ``dim_role_crosswalk`` (one node → many official occupation codes, so a role
is comparable across 7 markets) and an **append-only** ``dim_role_birth`` ledger
(first-seen + status, powering the emerging/extinction radar).

Static taxonomy files (O*NET Alternate Titles is already in
``staging/onet/onet_db.zip``; ESCO / Lightcast are fetchable reference files) feed
the alias graph. Loaders degrade gracefully when a file is absent — the curated
seed alone makes the resolver work today. Data-dependent enrichment (emergent-role
mining from the posting stream) is left as a documented TODO.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import duckdb

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("warehouse.taxonomy")

# --------------------------------------------------------------------------- #
#  Schema (self-contained; does not touch the core star-schema build)          #
# --------------------------------------------------------------------------- #
TAXONOMY_TABLES = {
    # seniority is an AXIS, not a node — an ordinal ladder + a manager track
    "dim_seniority": """
        CREATE TABLE IF NOT EXISTS dim_seniority (
            code     VARCHAR PRIMARY KEY,   -- intern, junior, mid, senior, staff, principal, distinguished
            label    VARCHAR NOT NULL,
            rank     INTEGER NOT NULL,      -- ordinal 0..n
            track    VARCHAR NOT NULL       -- ic | mgr
        )""",
    # specialization is an AXIS too — faceted tags on a node (domain/stack/modality)
    "dim_specialization": """
        CREATE TABLE IF NOT EXISTS dim_specialization (
            spec_id VARCHAR PRIMARY KEY,
            name    VARCHAR NOT NULL,
            axis    VARCHAR NOT NULL        -- domain | stack | modality
        )""",
    # the alias graph: many surface forms -> one canonical role node
    "dim_role_alias": """
        CREATE TABLE IF NOT EXISTS dim_role_alias (
            alias_id VARCHAR PRIMARY KEY,   -- stable hash(norm + role_id)
            surface  VARCHAR NOT NULL,      -- raw surface form, as a human would type
            norm     VARCHAR NOT NULL,      -- normalized (lowercased, seniority-stripped)
            role_id  VARCHAR NOT NULL,      -- -> dim_role.role_id
            source   VARCHAR,               -- curated | onet | esco | lightcast | observed
            lang     VARCHAR DEFAULT 'en',  -- multilingual aliases (e.g. de) live here
            weight   DOUBLE DEFAULT 1.0     -- frequency / confidence
        )""",
    # one canonical node -> many official government occupation codes (7 systems)
    "dim_role_crosswalk": """
        CREATE TABLE IF NOT EXISTS dim_role_crosswalk (
            role_id    VARCHAR,
            system     VARCHAR,             -- onet_soc | uk_soc2020 | noc2021 | anzsco | ssoc | kldb | esco | isco08
            code       VARCHAR,
            label      VARCHAR,
            confidence VARCHAR,             -- high (anchored) | med | low (soft-voted)
            PRIMARY KEY (role_id, system, code)
        )""",
    # append-only role-birth ledger — roles are born, never silently renumbered
    "dim_role_birth": """
        CREATE TABLE IF NOT EXISTS dim_role_birth (
            role_id        VARCHAR PRIMARY KEY,
            first_seen     VARCHAR,         -- ISO date or year the node first appeared
            status         VARCHAR,         -- canonical | emerging | extinct
            n_postings     INTEGER,
            n_countries    INTEGER,
            detected_from  VARCHAR,         -- seed | cluster | taxonomy
            provenance     VARCHAR,         -- JSON blob (centroid title, QoQ growth, ...)
            created_at     VARCHAR          -- append timestamp
        )""",
}

# canonical seniority ladder (axis values)
SENIORITY = [
    ("intern",        "Intern",            0, "ic"),
    ("junior",        "Junior",            1, "ic"),
    ("mid",           "Mid",               2, "ic"),
    ("senior",        "Senior",            3, "ic"),
    ("staff",         "Staff",             4, "ic"),
    ("principal",     "Principal",         5, "ic"),
    ("distinguished", "Distinguished",     6, "ic"),
    ("manager",       "Manager",           4, "mgr"),
    ("director",      "Director",          5, "mgr"),
    ("vp",            "VP",                6, "mgr"),
]

# surface tokens that signal seniority (stripped before alias matching)
_SENIORITY_TOKENS = re.compile(
    r"\b(intern|graduate|grad|entry|junior|jr|associate|mid|senior|sr|lead|"
    r"staff|principal|prin|distinguished|fellow|head\s+of|manager|mgr|director|"
    r"vp|vice\s+president|i{1,3}|iv|v|1|2|3|4)\b",
    re.IGNORECASE,
)
_NONWORD = re.compile(r"[^a-z0-9+#./ ]+")


def normalize_surface(text: str) -> str:
    """Lowercase, strip punctuation + seniority tokens — the alias-match key.

    'Sr. Software Engineer II' -> 'software engineer'.  Keeps +,#,.,/ so that
    'c++', 'c#', 'node.js', 'ci/cd' survive.
    """
    s = (text or "").lower().strip()
    s = _NONWORD.sub(" ", s)
    s = _SENIORITY_TOKENS.sub(" ", s)
    # drop tokens left as bare punctuation ('sr.' -> '.'); keep c++, c#, node.js, ci/cd
    return " ".join(t for t in s.split() if any(ch.isalnum() for ch in t))


def _alias_id(norm: str, role_id: str) -> str:
    return hashlib.sha1(f"{norm}\x00{role_id}".encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
#  Curated seed for the 16 served roles                                        #
#  (single source of truth — reused by the resolver as an always-available     #
#   fallback so it works even before the warehouse alias table is materialized) #
# --------------------------------------------------------------------------- #
CURATED_ALIASES: dict[str, list[str]] = {
    "swe": ["software engineer", "SWE", "software developer", "developer", "programmer",
            "SDE", "software development engineer", "member of technical staff", "MTS",
            "applications developer", "coder", "software engineer ii"],
    "backend": ["backend engineer", "back-end engineer", "backend developer", "server-side engineer",
                "BE engineer", "API engineer", "RoR developer", "rails developer", "golang engineer",
                "java backend engineer", "backend software engineer"],
    "frontend": ["frontend engineer", "front-end engineer", "frontend developer", "UI engineer",
                 "FE engineer", "react developer", "javascript engineer", "web developer",
                 "front end developer", "angular developer", "vue developer"],
    "mobile": ["mobile engineer", "mobile developer", "iOS engineer", "android engineer",
               "iOS developer", "android developer", "react native developer", "flutter developer",
               "mobile app developer", "swift developer", "kotlin developer"],
    "data-eng": ["data engineer", "DE", "data engineering", "ETL developer", "big data engineer",
                 "data pipeline engineer", "analytics engineer", "spark engineer", "data platform engineer"],
    "data-sci": ["data scientist", "DS", "applied scientist", "research scientist", "ML scientist",
                 "data science", "quantitative analyst", "decision scientist"],
    "data-analyst": ["data analyst", "business analyst", "BI analyst", "analytics analyst",
                     "reporting analyst", "business intelligence analyst", "insights analyst",
                     "product analyst"],
    "ml-eng": ["machine learning engineer", "ML engineer", "MLE", "AI engineer", "MLOps engineer",
               "MLOps", "deep learning engineer", "AI/ML engineer", "applied ML engineer",
               "LLM engineer", "GenAI engineer", "prompt engineer"],
    "devops": ["devops engineer", "dev ops", "devops", "CI/CD engineer", "build engineer",
               "release engineer", "automation engineer", "platform engineer", "infrastructure engineer"],
    "sre": ["site reliability engineer", "SRE", "reliability engineer", "production engineer",
            "systems reliability engineer", "site reliability engineer ii"],
    "cloud-arch": ["cloud architect", "cloud engineer", "AWS architect", "solutions architect",
                   "infrastructure architect", "cloud infrastructure engineer", "azure architect",
                   "GCP architect", "systems architect"],
    "security": ["security engineer", "infosec engineer", "application security engineer", "appsec engineer",
                 "cybersecurity engineer", "security analyst", "SOC analyst", "penetration tester",
                 "pentester", "GRC analyst", "cloud security engineer"],
    "qa": ["QA engineer", "quality assurance engineer", "QA analyst", "test engineer", "SDET",
           "automation QA", "test automation engineer", "quality engineer", "QA automation engineer",
           "software test engineer", "manual tester"],
    "eng-mgr": ["engineering manager", "eng manager", "EM", "dev manager", "software engineering manager",
                "technical lead manager", "head of engineering", "director of engineering",
                "VP of engineering", "engineering lead"],
    "pm": ["product manager", "PM", "technical product manager", "TPM", "product owner",
           "associate product manager", "APM", "senior product manager", "group product manager"],
    "ux": ["UX designer", "product designer", "UI designer", "UX researcher", "interaction designer",
           "UI/UX designer", "user experience designer", "visual designer", "design researcher"],
}

# canonical-node -> official occupation codes. O*NET-SOC is seeded with reasonable
# confidence; other systems are partial — extend via load_crosswalk_file(). The
# TABLE supports all 7 systems even where the seed is thin (honest: confidence col).
GOV_CROSSWALK: dict[str, dict[str, tuple[str, str]]] = {
    # role_id: { system: (code, label) }
    "swe":          {"onet_soc": ("15-1252.00", "Software Developers"),        "isco08": ("2512", "Software developers"), "uk_soc2020": ("2134", "Programmers and software development professionals")},
    "backend":      {"onet_soc": ("15-1252.00", "Software Developers"),        "isco08": ("2512", "Software developers")},
    "frontend":     {"onet_soc": ("15-1254.00", "Web Developers"),            "isco08": ("2513", "Web and multimedia developers")},
    "mobile":       {"onet_soc": ("15-1252.00", "Software Developers"),        "isco08": ("2512", "Software developers")},
    "data-eng":     {"onet_soc": ("15-1243.00", "Database Architects"),        "isco08": ("2521", "Database designers and administrators")},
    "data-sci":     {"onet_soc": ("15-2051.00", "Data Scientists"),            "isco08": ("2120", "Mathematicians, actuaries and statisticians")},
    "data-analyst": {"onet_soc": ("15-2051.01", "Business Intelligence Analysts"), "isco08": ("2511", "Systems analysts")},
    "ml-eng":       {"onet_soc": ("15-2051.00", "Data Scientists"),            "isco08": ("2512", "Software developers")},
    "devops":       {"onet_soc": ("15-1244.00", "Network and Computer Systems Administrators"), "isco08": ("2522", "Systems administrators")},
    "sre":          {"onet_soc": ("15-1244.00", "Network and Computer Systems Administrators"), "isco08": ("2522", "Systems administrators")},
    "cloud-arch":   {"onet_soc": ("15-1241.00", "Computer Network Architects"), "isco08": ("2523", "Computer network professionals")},
    "security":     {"onet_soc": ("15-1212.00", "Information Security Analysts"), "isco08": ("2529", "Database and network professionals nec")},
    "qa":           {"onet_soc": ("15-1253.00", "Software Quality Assurance Analysts and Testers"), "isco08": ("2512", "Software developers")},
    "eng-mgr":      {"onet_soc": ("11-3021.00", "Computer and Information Systems Managers"), "isco08": ("1330", "ICT service managers")},
    "pm":           {"onet_soc": ("11-3021.00", "Computer and Information Systems Managers"), "isco08": ("1330", "ICT service managers")},
    "ux":           {"onet_soc": ("15-1255.00", "Web and Digital Interface Designers"), "isco08": ("2166", "Graphic and multimedia designers")},
}

SPECIALIZATIONS = [
    # axis: domain
    ("dom-fintech", "Fintech", "domain"), ("dom-health", "Health", "domain"),
    ("dom-gaming", "Gaming", "domain"), ("dom-ecom", "E-commerce", "domain"),
    # axis: stack
    ("stk-react", "React", "stack"), ("stk-rust", "Rust", "stack"),
    ("stk-python", "Python", "stack"), ("stk-java", "Java", "stack"),
    ("stk-go", "Go", "stack"), ("stk-k8s", "Kubernetes", "stack"),
    # axis: modality
    ("mod-backend", "Backend", "modality"), ("mod-frontend", "Frontend", "modality"),
    ("mod-fullstack", "Full-stack", "modality"), ("mod-platform", "Platform", "modality"),
]


# --------------------------------------------------------------------------- #
#  Static-file loaders (graceful — skip with a TODO when a file is absent)     #
# --------------------------------------------------------------------------- #
def _onet_soc_to_role() -> dict[str, str]:
    """Reverse the crosswalk: O*NET-SOC code (6-digit prefix) -> role_id."""
    out: dict[str, str] = {}
    for role_id, systems in GOV_CROSSWALK.items():
        code = systems.get("onet_soc", (None, None))[0]
        if code:
            out.setdefault(code[:7], role_id)   # '15-1252' prefix matches '.00/.01/...'
    return out


def load_onet_alternate_titles(zip_path: Path | None = None) -> list[tuple[str, str]]:
    """Extract (alternate_title, role_id) pairs from the O*NET DB zip in staging.

    O*NET ships an 'Alternate Titles' table (tab-delimited: O*NET-SOC Code,
    Alternate Title, Short Title, Source). We keep the rows whose SOC maps to one
    of our roles. Returns [] (logged) when the zip or member is absent — the build
    must not fail on a missing reference file.
    """
    zip_path = zip_path or (settings.staging_dir / "onet" / "onet_db.zip")
    if not zip_path.exists():
        log.warning("O*NET zip absent (%s) — skipping alternate-title aliases (TODO: fetch O*NET DB)", zip_path)
        return []
    soc_to_role = _onet_soc_to_role()
    pairs: list[tuple[str, str]] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            member = next((n for n in zf.namelist() if "alternate" in n.lower() and n.lower().endswith((".txt", ".csv"))), None)
            if not member:
                log.warning("O*NET zip has no 'Alternate Titles' member — skipping (members: %s)", zf.namelist()[:5])
                return []
            with zf.open(member) as fh:
                text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
                delim = "\t" if member.lower().endswith(".txt") else ","
                reader = csv.reader(text, delimiter=delim)
                header = next(reader, None)
                for row in reader:
                    if len(row) < 2:
                        continue
                    soc, title = row[0].strip(), row[1].strip()
                    role_id = soc_to_role.get(soc[:7])
                    if role_id and title:
                        pairs.append((title, role_id))
    except Exception as e:  # noqa: BLE001 — a malformed reference file must not break the build
        log.error("O*NET alternate-title parse failed: %s", e)
        return []
    log.info("O*NET alternate titles → %d aliases across %d roles", len(pairs), len({r for _, r in pairs}))
    return pairs


def load_crosswalk_file(path: Path, system: str) -> list[tuple[str, str, str]]:
    """Load an official occupation crosswalk file (role_id, code, label).

    TODO(ingestion): wire ESCO occupations.csv / UK SOC2020 / NOC2021 / ANZSCO /
    SSOC / KldB official crosswalk files here. Until a file is dropped in, the
    curated GOV_CROSSWALK seed carries O*NET-SOC + ISCO-08. Expected CSV columns:
    role_id, code, label. Returns [] gracefully when absent.
    """
    if not path or not Path(path).exists():
        log.info("crosswalk file for %s absent (%s) — using curated seed only [TODO]", system, path)
        return []
    rows: list[tuple[str, str, str]] = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("role_id") and r.get("code"):
                rows.append((r["role_id"], r["code"], r.get("label", "")))
    return rows


def load_esco(path: Path | None = None) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """Parse the ESCO ``occupations_en.csv`` → (alias pairs, crosswalk rows).

    Maps each ESCO occupation to one of our roles via its **ISCO-08 group** (the
    ``isco``-keyed codes already in ``GOV_CROSSWALK``), then mines its multilingual
    ``preferredLabel`` + ``altLabels`` as role aliases and emits an ``esco`` crosswalk
    row. ESCO is the European, multilingual occupation taxonomy — it widens alias
    recall (esp. for DE) without any company data. Graceful: returns ``([], [])`` when
    the file is absent (drop the standard ESCO ``occupations_en.csv`` into
    ``staging/esco/`` to enable). ROLES-ONLY: occupations + labels only.
    """
    from backend.core.config import settings
    path = Path(path) if path else (settings.staging_dir / "esco" / "occupations_en.csv")
    if not path.exists():
        log.info("ESCO occupations file absent (%s) — skipping [drop occupations_en.csv to enable]", path)
        return [], []
    # ISCO-08 4-digit group → our role_id, from the curated crosswalk
    isco_to_role: dict[str, str] = {}
    for role_id, systems in GOV_CROSSWALK.items():
        for system, (code, _label) in systems.items():
            if "isco" in system.lower():
                isco_to_role[str(code)[:4]] = role_id
    aliases: list[tuple[str, str]] = []
    xwalk: list[tuple[str, str, str]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                isco = str(r.get("iscoGroup") or r.get("iscoGroup".lower()) or "")[:4]
                role_id = isco_to_role.get(isco)
                if not role_id:
                    continue
                pref = (r.get("preferredLabel") or "").strip()
                code = (r.get("code") or r.get("conceptUri") or "").strip()
                if pref:
                    aliases.append((pref, role_id))
                    xwalk.append((role_id, code, pref))
                for alt in (r.get("altLabels") or "").replace("\r", "\n").split("\n"):
                    alt = alt.strip()
                    if alt:
                        aliases.append((alt, role_id))
    except Exception as e:  # noqa: BLE001 — a malformed reference file must not break the build
        log.error("ESCO parse failed: %s", e)
        return [], []
    log.info("ESCO → %d aliases, %d crosswalk rows across %d roles",
             len(aliases), len(xwalk), len({x[0] for x in xwalk}))
    return aliases, xwalk


def load_lightcast(path: Path | None = None) -> list[tuple[str, str]]:
    """Parse the Lightcast **Open Titles** export → role alias pairs.

    Lightcast Open Skills/Titles is a public, free taxonomy. The Titles file maps a
    large surface vocabulary of raw job titles to a normalized title + (in the
    SOC-mapped exports) an O*NET-SOC code. Where a SOC mapping is present we crosswalk
    SOC → our role (the same path as the O*NET alternate-title loader) and mine the
    raw + normalized title as aliases — widening never-dead-end recall. Graceful:
    returns ``[]`` when the file is absent (drop a Lightcast titles CSV with a
    ``soc``/``onet_soc`` column + a ``title``/``name`` column into ``staging/lightcast/``)
    or when no SOC column exists. ROLES-ONLY: titles + occupation codes only.
    """
    from backend.core.config import settings
    path = Path(path) if path else (settings.staging_dir / "lightcast" / "titles.csv")
    if not path.exists():
        log.info("Lightcast titles file absent (%s) — skipping [drop titles.csv to enable]", path)
        return []
    from backend.ingest.h1b import _soc_to_role
    aliases: list[tuple[str, str]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            cols = {c.lower(): c for c in (reader.fieldnames or [])}
            soc_col = next((cols[k] for k in cols if "soc" in k), None)
            title_cols = [cols[k] for k in cols if k in ("title", "name", "raw_title", "normalized_title")]
            if not soc_col or not title_cols:
                log.info("Lightcast file lacks a SOC + title column — skipping (cols: %s)", list(cols))
                return []
            for r in reader:
                role_id = _soc_to_role(str(r.get(soc_col) or "").split(".")[0][:7])
                if not role_id:
                    continue
                for tc in title_cols:
                    t = (r.get(tc) or "").strip()
                    if t:
                        aliases.append((t, role_id))
    except Exception as e:  # noqa: BLE001 — a malformed reference file must not break the build
        log.error("Lightcast parse failed: %s", e)
        return []
    log.info("Lightcast → %d title aliases across %d roles",
             len(aliases), len({r for _, r in aliases}))
    return aliases


# --------------------------------------------------------------------------- #
#  Fuse-time crosswalks for the new vacancy/salary feeds (roles-only)           #
# --------------------------------------------------------------------------- #
# German KldB-2010 occupation codes the Entgeltatlas connector queries → our roles.
KLDB_TO_ROLE: dict[str, str] = {
    "43412": "swe",        # Softwareentwicklung
    "43414": "swe",        # technische Informatik
    "43422": "frontend",   # Web-/Multimediaprogrammierung
    "43432": "data-eng",   # IT-Systemanalyse / -beratung
    "43442": "data-eng",   # Datenbankadministration
    "43402": "swe", "43512": "security", "43302": "devops",   # broader fallbacks
}

# US OPM occupational series the USAJobs connector sweeps → our roles.
OPM_TO_ROLE: dict[str, str] = {
    "2210": "swe",         # Information Technology Management
    "1550": "swe",         # Computer Science
    "0854": "swe",         # Computer Engineering
    "0855": "swe",         # Electronics Engineering
    "1560": "data-sci",    # Data Science
    "1530": "data-sci",    # Statistician
    "1515": "data-analyst",  # Operations Research
}

_TITLE_INDEX: dict[str, str] | None = None


def _title_index() -> dict[str, str]:
    """Normalized surface → role_id, built once from the curated alias seed. The
    matcher's vocabulary (no warehouse/db dependency, usable inside the fuse)."""
    global _TITLE_INDEX
    if _TITLE_INDEX is None:
        idx: dict[str, str] = {}
        for role_id, surfaces in CURATED_ALIASES.items():
            for s in surfaces:
                n = normalize_surface(s)
                if n:
                    idx.setdefault(n, role_id)
        _TITLE_INDEX = idx
    return _TITLE_INDEX


def match_title_to_role(title: str | None) -> str | None:
    """Resolve a free-text occupation/title → one of our roles using the curated alias
    seed: exact normalized match first, then the longest word-bounded alias contained in
    the title ("Senior Software Engineer II" → swe). Dependency-free (no marts/db), so
    the warehouse fuse can resolve vacancy-feed titles + Wikidata occupation labels."""
    n = normalize_surface(title or "")
    if not n:
        return None
    idx = _title_index()
    if n in idx:
        return idx[n]
    padded = f" {n} "
    best, blen = None, 0
    for alias, rid in idx.items():
        if len(alias) >= 4 and len(alias) > blen and f" {alias} " in padded:
            best, blen = rid, len(alias)
    return best


# --------------------------------------------------------------------------- #
#  Build                                                                        #
# --------------------------------------------------------------------------- #
def create_taxonomy_schema(con: duckdb.DuckDBPyConnection) -> None:
    for ddl in TAXONOMY_TABLES.values():
        con.execute(ddl)


def _existing_role_ids(con: duckdb.DuckDBPyConnection) -> list[str]:
    try:
        return [r[0] for r in con.execute("SELECT role_id FROM dim_role ORDER BY ord").fetchall()]
    except duckdb.Error:
        return []


def build_taxonomy(con: duckdb.DuckDBPyConnection, *, as_of: str | None = None) -> dict[str, int]:
    """Create + populate the taxonomy tables on an open warehouse connection.

    Idempotent: clears + reloads the alias / crosswalk / axis tables, but the
    role-birth ledger is **append-only** (a node already in it is never re-dated).
    Returns row counts for verification. Does NOT touch the core star schema.
    """
    create_taxonomy_schema(con)
    role_ids = set(_existing_role_ids(con)) or set(CURATED_ALIASES.keys())

    # axes (reference values) — full reload
    con.execute("DELETE FROM dim_seniority")
    con.executemany("INSERT INTO dim_seniority VALUES (?,?,?,?)", SENIORITY)
    con.execute("DELETE FROM dim_specialization")
    con.executemany("INSERT INTO dim_specialization VALUES (?,?,?)", SPECIALIZATIONS)

    # alias graph — curated seed + O*NET alternate titles + the role's own name
    con.execute("DELETE FROM dim_role_alias")
    seen: set[str] = set()
    alias_rows: list[tuple] = []

    def _add(surface: str, role_id: str, source: str, lang: str = "en", weight: float = 1.0):
        if role_id not in role_ids:
            return
        norm = normalize_surface(surface)
        if not norm:
            return
        aid = _alias_id(norm, role_id)
        if aid in seen:
            return
        seen.add(aid)
        alias_rows.append((aid, surface, norm, role_id, source, lang, weight))

    # the canonical name itself is an alias (read names from dim_role if present)
    try:
        for role_id, name in con.execute("SELECT role_id, name FROM dim_role").fetchall():
            _add(name, role_id, "curated", weight=2.0)
    except duckdb.Error:
        pass
    for role_id, surfaces in CURATED_ALIASES.items():
        for s in surfaces:
            _add(s, role_id, "curated")
    for title, role_id in load_onet_alternate_titles():
        _add(title, role_id, "onet", weight=0.8)
    esco_aliases, esco_xwalk = load_esco()
    for title, role_id in esco_aliases:
        _add(title, role_id, "esco", weight=0.75)
    for title, role_id in load_lightcast():
        _add(title, role_id, "lightcast", weight=0.7)

    if alias_rows:
        con.executemany("INSERT INTO dim_role_alias VALUES (?,?,?,?,?,?,?)", alias_rows)

    # government-code crosswalk — full reload from curated seed (+ any dropped files)
    con.execute("DELETE FROM dim_role_crosswalk")
    xwalk_rows: list[tuple] = []
    for role_id, systems in GOV_CROSSWALK.items():
        if role_id not in role_ids:
            continue
        for system, (code, label) in systems.items():
            conf = "high" if system in ("onet_soc",) else "med"
            xwalk_rows.append((role_id, system, code, label, conf))
    for role_id, code, label in esco_xwalk:                    # ESCO occupations (when present)
        if role_id in role_ids:
            xwalk_rows.append((role_id, "esco", code, label, "med"))
    if xwalk_rows:
        con.executemany("INSERT INTO dim_role_crosswalk VALUES (?,?,?,?,?)", xwalk_rows)

    # role-birth ledger — APPEND ONLY: insert nodes not already present
    existing_births = {r[0] for r in con.execute("SELECT role_id FROM dim_role_birth").fetchall()}
    stamp = as_of or "2026-06-25"
    birth_rows = [
        (rid, "2026", "canonical", None, None, "seed",
         '{"detected_from":"curated-seed"}', stamp)
        for rid in sorted(role_ids) if rid not in existing_births
    ]
    if birth_rows:
        con.executemany("INSERT INTO dim_role_birth VALUES (?,?,?,?,?,?,?,?)", birth_rows)

    # emergent-role mining (append-only; empty until the at-scale posting corpus exists)
    emergent = mine_emergent_roles(con, as_of=as_of)

    counts = {
        "dim_seniority": con.execute("SELECT count(*) FROM dim_seniority").fetchone()[0],
        "dim_specialization": con.execute("SELECT count(*) FROM dim_specialization").fetchone()[0],
        "dim_role_alias": con.execute("SELECT count(*) FROM dim_role_alias").fetchone()[0],
        "dim_role_crosswalk": con.execute("SELECT count(*) FROM dim_role_crosswalk").fetchone()[0],
        "dim_role_birth": con.execute("SELECT count(*) FROM dim_role_birth").fetchone()[0],
        "emergent_promoted": emergent["promoted"],
    }
    log.info("taxonomy built: %s", counts)
    return counts


def mine_emergent_roles(con: duckdb.DuckDBPyConnection, *, min_countries: int = 2,
                        as_of: str | None = None) -> dict[str, int]:
    """Emergent-role miner (append-only). A role isn't a fixed list — this promotes
    genuinely-new ones the market surfaces.

    Reads the **composite-fingerprint-clustered** observed-title stream
    (``role_derivation``'s ``derived_roles.parquet``), groups clusters by canonical
    label across countries, and promotes any label that (a) cleared the volume floor,
    (b) surfaced in ``>= min_countries`` of our markets, and (c) has **no canonical
    alias match** — stamping it as an ``emerging`` node in the role-birth ledger with
    its first-seen date + the countries it appeared in. De-companied + de-countried
    (the node is cross-country, roles-only). Idempotent: a node already born is never
    re-dated. Empty until the at-scale posting corpus exists (needs a run).
    """
    from collections import defaultdict

    from backend.core.config import settings
    from backend.warehouse.build import slug

    p = settings.staging_dir / "normalized" / "derived_roles.parquet"
    if not p.exists():
        log.info("emergent miner: no derived_roles.parquet — nothing to mine [needs a run]")
        return {"candidates": 0, "promoted": 0}
    import pandas as pd

    df = pd.read_parquet(p)
    if df.empty:
        return {"candidates": 0, "promoted": 0}

    known = {r[0] for r in con.execute("SELECT norm FROM dim_role_alias").fetchall()}
    existing = {r[0] for r in con.execute("SELECT role_id FROM dim_role_birth").fetchall()}

    by_label: dict[str, dict] = defaultdict(lambda: {"countries": set(), "postings": 0})
    for _, r in df.iterrows():
        lab = normalize_surface(str(r.get("label_title") or ""))
        if not lab:
            continue
        g = by_label[lab]
        g["countries"].add(r.get("country"))
        g["postings"] += int(r.get("posting_count") or 0)

    stamp = as_of or "2026-06-25"
    rows, candidates = [], 0
    for lab, g in by_label.items():
        if len(g["countries"]) < min_countries or lab in known:
            continue                              # too local, or already canonical
        candidates += 1
        rid = "emerging:" + slug(lab)
        if rid in existing:
            continue                              # append-only: never re-date
        meta = json.dumps({"detected_from": "emergent-miner",
                           "countries": sorted(c for c in g["countries"] if c),
                           "postings": g["postings"]})
        rows.append((rid, "2026", "emerging", None, None, "miner", meta, stamp))
    if rows:
        con.executemany("INSERT INTO dim_role_birth VALUES (?,?,?,?,?,?,?,?)", rows)
    log.info("emergent miner: %d cross-country candidates, %d newly promoted (>=%d countries)",
             candidates, len(rows), min_countries)
    return {"candidates": candidates, "promoted": len(rows)}
