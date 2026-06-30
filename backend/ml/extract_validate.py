"""Validation harness for the LLM job-posting extraction — **no hand labels, ever**.

The owner will not label data, so we never ask them to. Instead we validate the
LLM extraction (produced by ``backend.ml.llm_extract`` →
``staging/extracted/postings_extracted.parquet``) against truth that *already
exists* in our pipeline, plus two model-internal consistency signals. Three
independent strategies, each emitting real numbers:

  (1) **GROUND-TRUTH** (the spine, surfaceable as fact)
      For the discrete fields we *can* anchor:
        * extracted ``role`` → SOC via ``match_title_to_role`` / ``_soc_to_role``
          and compared to the posting's own resolvable title (role agreement %);
        * the extracted ``skills`` set vs the O*NET occupation→skill set for that
          SOC (parsed from ``staging/onet/onet_db.zip``) **and** vs ESCO
          occupation→skill when present — Jaccard / precision / recall.
      Cross-lingual: non-English postings whose extracted ``role`` matches the
      ESCO occupation label *in that language* (ESCO is multilingual).

  (2) **LLM-AS-JUDGE** (moderate tier)
      ``responsibilities_summary`` has no structured truth. A stronger / different
      model scores a SAMPLE for faithfulness-to-the-posting on a 1–5 scale. The
      scoring prompt + aggregation live here; the model call is a **lazy vLLM
      import** that degrades gracefully (clear log + skip, tier→``unvalidated``)
      when ``vllm``/``torch`` are absent.

  (3) **SELF-CONSISTENCY** (moderate tier)
      Re-extract a sample at ``temperature > 0`` (or a paraphrased prompt) and
      measure per-field agreement across runs; low agreement flags the posting
      low-confidence (feeds the abstain rule). PLUS cross-field consistency
      checks (e.g. ``seniority`` vs ``years_required``) that need no second run.

Output: ``staging/extracted/validation_report.json`` — per-field accuracy + a
TIER per field:
  * ``ground-truth``      — externally anchored, **surfaceable as fact**;
  * ``judge`` / ``self-consistency`` — model-internal, moderate confidence;
  * ``unvalidated``       — store-only, **never surfaced**.

ROLES-ONLY: nothing here touches employer/company. Heavy deps (vLLM/torch) are
imported lazily so this module imports cleanly on a machine with no GPU stack.
BUILD-ONLY: real + runnable, but not executed this pass; idempotent + resumable
(a per-stage checkpoint sidecar lets a re-run skip finished stages).
"""
from __future__ import annotations

import csv
import io
import json
import statistics
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from backend.core.config import settings
from backend.core.logging import get_logger, stage_timer
from backend.ingest.h1b import _soc_to_role
from backend.warehouse.taxonomy import (
    GOV_CROSSWALK,
    load_esco,
    match_title_to_role,
    normalize_surface,
)

log = get_logger("ml.extract_validate")

# --------------------------------------------------------------------------- #
#  Paths / tiers / config                                                      #
# --------------------------------------------------------------------------- #
EXTRACTED_PARQUET = settings.staging_dir / "extracted" / "postings_extracted.parquet"
REPORT_PATH = settings.staging_dir / "extracted" / "validation_report.json"
CHECKPOINT_PATH = settings.staging_dir / "extracted" / "validation_checkpoint.json"
ONET_ZIP = settings.staging_dir / "onet" / "onet_db.zip"

# Tier vocabulary (single source of truth — drives what the API may surface).
TIER_GROUND_TRUTH = "ground-truth"   # externally anchored → fact
TIER_JUDGE = "judge"                 # LLM-as-judge → moderate
TIER_SELF = "self-consistency"       # re-extraction agreement → moderate
TIER_UNVALIDATED = "unvalidated"     # store-only, never surfaced

# Which extraction fields each strategy is responsible for. Anything not claimed
# by a strategy that produced a real number stays ``unvalidated``.
GROUND_TRUTH_FIELDS = ("role", "skills")
JUDGE_FIELDS = ("responsibilities_summary",)
SELF_CONSISTENCY_FIELDS = ("role", "seniority", "years_required", "skills",
                           "remote", "employment_type")

# Sampling caps (kept small — these are slow, model-bound stages).
JUDGE_SAMPLE = 200
SELF_CONSISTENCY_SAMPLE = 200
SELF_CONSISTENCY_TEMPERATURE = 0.7

# Agreement thresholds → "validated as fact" gate for the discrete fields.
GROUND_TRUTH_ROLE_ACCEPT = 0.70      # role agreement % to call the field surfaceable
GROUND_TRUTH_SKILL_JACCARD = 0.20    # mean Jaccard vs O*NET/ESCO skill set
SELF_CONSISTENCY_ACCEPT = 0.75       # per-field cross-run agreement to trust
LOW_CONFIDENCE_AGREEMENT = 0.50      # below this on a posting → abstain flag


# --------------------------------------------------------------------------- #
#  Loading the extraction (lazy dep on the not-yet-built llm_extract module)    #
# --------------------------------------------------------------------------- #
def _load_extracted() -> list[dict]:
    """Load the LLM-extracted postings.

    Prefers ``backend.ml.llm_extract.load_extracted`` (the canonical reader the
    extractor ships). That module is a separate build step and pulls in vLLM, so
    we import it lazily and fall back to reading the parquet directly. Returns a
    list of plain dicts (one per posting) so the rest of the harness is
    dependency-light.
    """
    # 1) canonical reader, if the extractor module is present
    try:
        from backend.ml import llm_extract  # noqa: WPS433 (lazy: heavy/optional)

        loader = getattr(llm_extract, "load_extracted", None)
        if callable(loader):
            rows = loader()
            recs = _to_records(rows)
            if recs:
                log.info("loaded %d extracted postings via llm_extract.load_extracted", len(recs))
                return recs
    except Exception as e:  # noqa: BLE001 — module absent or heavy import failed; fall through
        log.info("llm_extract.load_extracted unavailable (%s) — reading parquet directly", e)

    # 2) direct parquet read
    if not EXTRACTED_PARQUET.exists():
        log.warning("extracted parquet absent (%s) — nothing to validate "
                    "[run backend.ml.llm_extract first]", EXTRACTED_PARQUET)
        return []
    try:
        import pandas as pd  # local: pandas is a normal dep but keep import lazy/clear

        df = pd.read_parquet(EXTRACTED_PARQUET)
        recs = df.to_dict("records")
        log.info("loaded %d extracted postings from %s", len(recs), EXTRACTED_PARQUET)
        return _to_records(recs)
    except Exception as e:  # noqa: BLE001 — a bad/missing parquet must not crash the harness
        log.error("failed to read extracted parquet: %s", e)
        return []


def _to_records(rows: Any) -> list[dict]:
    """Coerce whatever the loader returns (DataFrame / list / iterable) → list[dict]."""
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):  # a DataFrame
        try:
            return rows.to_dict("records")  # type: ignore[call-arg]
        except Exception:  # noqa: BLE001
            return []
    out: list[dict] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(r)
        elif hasattr(r, "_asdict"):
            out.append(dict(r._asdict()))
        elif hasattr(r, "__dict__"):
            out.append(dict(r.__dict__))
    return out


# --------------------------------------------------------------------------- #
#  Field accessors — tolerate schema drift in the extractor's output           #
# --------------------------------------------------------------------------- #
def _g(rec: dict, *keys: str, default=None):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return rec[k]
    return default


def _as_skill_list(val: Any) -> list[str]:
    """Normalize an extracted ``skills`` cell (list / JSON string / delimited) → list[str]."""
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("["):
            try:
                val = json.loads(s)
            except Exception:  # noqa: BLE001
                val = s.replace(";", ",").split(",")
        else:
            val = s.replace(";", ",").split(",")
    try:
        items = list(val)
    except TypeError:
        return []
    out, seen = [], set()
    for x in items:
        t = str(x).strip()
        if t:
            n = _norm_skill(t)
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


def _norm_skill(s: str) -> str:
    """Canonicalize a skill surface form for set comparison (lower, collapse aliases).

    Keeps the comparison fair across O*NET/ESCO/LLM surfaces: 'Node.js' == 'nodejs'
    == 'node', 'Postgres' == 'postgresql', etc. Deliberately small + transparent.
    """
    t = (s or "").strip().lower()
    t = t.replace("’", "'")
    # strip parentheticals: 'amazon web services (aws)' -> keep both halves later
    t = t.split("(")[0].strip() or t
    aliases = {
        "nodejs": "node", "node.js": "node",
        "postgresql": "postgres", "postgre": "postgres",
        "k8s": "kubernetes", "amazon web services": "aws",
        "google cloud platform": "gcp", "golang": "go",
        "js": "javascript", "ts": "typescript", "py": "python",
        "ms sql": "sql server", "rest api": "rest", "restful": "rest",
        "ci cd": "ci/cd", "cicd": "ci/cd",
    }
    t = aliases.get(t, t)
    # squeeze internal whitespace / trailing punctuation
    t = " ".join(t.replace("/", " / ").split()) if t == "ci/cd" else " ".join(t.split())
    return t.strip(" .,-")


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------- #
#  O*NET occupation → skill set (ground-truth reference)                        #
# --------------------------------------------------------------------------- #
def _onet_member(zf: zipfile.ZipFile, needle: str) -> str | None:
    return next((n for n in zf.namelist()
                 if needle.lower() in n.lower() and n.lower().endswith((".txt", ".csv"))), None)


def load_onet_skill_sets(zip_path: Path | None = None) -> dict[str, set[str]]:
    """Build ``role_id -> {normalized skill}`` from the O*NET DB zip.

    We union two O*NET tables per occupation:
      * **Technology Skills** (``Example`` column) — concrete tools/tech the LLM
        actually emits ("Python", "Kubernetes", "Atlassian JIRA");
      * **Skills** (``Element Name``) — the competency layer ("Programming",
        "Complex Problem Solving").
    Each occupation's SOC is crosswalked to our role via ``_soc_to_role``, so the
    set is keyed by *role_id* (roles-only). Returns ``{}`` (logged) when the zip
    or members are absent — a missing reference file must never break the build.
    """
    zip_path = zip_path or ONET_ZIP
    if not zip_path.exists():
        log.warning("O*NET zip absent (%s) — skipping O*NET skill ground-truth", zip_path)
        return {}
    by_role: dict[str, set[str]] = defaultdict(set)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            tech = _onet_member(zf, "Technology Skills")
            if tech:
                _accumulate_onet(zf, tech, by_role, name_col="Example")
            comp = _onet_member(zf, "Skills.txt") or _onet_member(zf, "Skills")
            if comp:
                _accumulate_onet(zf, comp, by_role, name_col="Element Name")
    except Exception as e:  # noqa: BLE001 — malformed reference file must not break the build
        log.error("O*NET skill parse failed: %s", e)
        return {}
    out = {r: s for r, s in by_role.items() if s}
    log.info("O*NET skill ground-truth: %d roles, %d total skill terms",
             len(out), sum(len(s) for s in out.values()))
    return out


def _accumulate_onet(zf: zipfile.ZipFile, member: str, by_role: dict[str, set[str]],
                     *, name_col: str) -> None:
    with zf.open(member) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        reader = csv.reader(text, delimiter="\t")
        header = next(reader, None)
        if not header:
            return
        hmap = {h.strip().lower(): i for i, h in enumerate(header)}
        i_soc = hmap.get("o*net-soc code", 0)
        i_name = hmap.get(name_col.lower())
        if i_name is None:
            return
        for row in reader:
            if len(row) <= max(i_soc, i_name):
                continue
            role_id = _soc_to_role(row[i_soc].strip())
            if not role_id:
                continue
            skill = _norm_skill(row[i_name])
            if skill:
                by_role[role_id].add(skill)


def load_esco_skill_sets() -> dict[str, set[str]]:
    """ESCO occupation→skill, keyed by role_id.

    ESCO ships an occupation↔skill relation file. We reuse ``taxonomy.load_esco``
    to get ESCO-occupation→role_id, then (if an ESCO skills relation is present in
    ``staging/esco/``) union the linked skill labels per role. Graceful: when the
    relation file is absent we return ``{}`` and ground-truth simply falls back to
    O*NET alone. Roles-only.
    """
    esco_dir = settings.staging_dir / "esco"
    rel = esco_dir / "occupationSkillRelations_en.csv"
    skills_file = esco_dir / "skills_en.csv"
    if not rel.exists() or not skills_file.exists():
        log.info("ESCO skill relation files absent (%s) — O*NET-only skill ground-truth",
                 esco_dir)
        return {}
    # occupation conceptUri -> role_id, via the taxonomy ESCO loader's crosswalk rows
    _aliases, xwalk = load_esco()
    uri_to_role = {code: role_id for role_id, code, _ in xwalk if code}
    skill_uri_to_label: dict[str, str] = {}
    by_role: dict[str, set[str]] = defaultdict(set)
    try:
        with open(skills_file, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                uri = (r.get("conceptUri") or r.get("uri") or "").strip()
                lab = (r.get("preferredLabel") or "").strip()
                if uri and lab:
                    skill_uri_to_label[uri] = _norm_skill(lab)
        with open(rel, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                occ = (r.get("occupationUri") or "").strip()
                sk = (r.get("skillUri") or "").strip()
                role_id = uri_to_role.get(occ)
                label = skill_uri_to_label.get(sk)
                if role_id and label:
                    by_role[role_id].add(label)
    except Exception as e:  # noqa: BLE001
        log.error("ESCO skill parse failed: %s", e)
        return {}
    out = {r: s for r, s in by_role.items() if s}
    log.info("ESCO skill ground-truth: %d roles", len(out))
    return out


# --------------------------------------------------------------------------- #
#  ESCO multilingual occupation labels → role (cross-lingual role check)         #
# --------------------------------------------------------------------------- #
def load_esco_label_index_by_lang() -> dict[str, dict[str, str]]:
    """``lang -> {normalized occupation label -> role_id}`` from the per-language
    ESCO occupation files (``occupations_<lang>.csv``).

    Used by the cross-lingual ground-truth check: a non-English posting whose
    extracted ``role`` (in its own language) matches the ESCO label for a role we
    serve is an externally-anchored hit. Graceful: returns ``{}`` for langs whose
    file is absent. ESCO is *the* roles-only multilingual occupation taxonomy, so
    no company data is involved.
    """
    esco_dir = settings.staging_dir / "esco"
    out: dict[str, dict[str, str]] = {}
    if not esco_dir.exists():
        return out
    # build ISCO-08 group -> role_id once (same mapping taxonomy.load_esco uses)
    isco_to_role: dict[str, str] = {}
    for role_id, systems in GOV_CROSSWALK.items():
        for system, (code, _label) in systems.items():
            if "isco" in system.lower():
                isco_to_role[str(code)[:4]] = role_id
    for f in esco_dir.glob("occupations_*.csv"):
        lang = f.stem.split("_")[-1].lower()
        idx: dict[str, str] = {}
        try:
            with open(f, encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    isco = str(r.get("iscoGroup") or "")[:4]
                    role_id = isco_to_role.get(isco)
                    if not role_id:
                        continue
                    for field in ("preferredLabel", "altLabels"):
                        raw = r.get(field) or ""
                        for lab in raw.replace("\r", "\n").split("\n"):
                            n = normalize_surface(lab)
                            if n:
                                idx.setdefault(n, role_id)
        except Exception as e:  # noqa: BLE001
            log.warning("ESCO %s labels parse failed: %s", lang, e)
            continue
        if idx:
            out[lang] = idx
    if out:
        log.info("ESCO multilingual occupation labels: %d languages", len(out))
    return out


# =========================================================================== #
#  STRATEGY 1 — GROUND TRUTH                                                    #
# =========================================================================== #
def validate_ground_truth(records: list[dict]) -> dict[str, Any]:
    """Anchor the discrete fields (``role``, ``skills``) against structured truth.

    role  : extracted role vs the role independently resolvable from the posting's
            own title (``match_title_to_role``) — agreement %.
    skills: extracted skill set vs O*NET (and ESCO when present) occupation→skill
            for the posting's role — mean Jaccard / precision / recall.
    Plus a cross-lingual sub-check for non-English postings via ESCO labels.
    """
    onet_skills = load_onet_skill_sets()
    esco_skills = load_esco_skill_sets()
    esco_labels = load_esco_label_index_by_lang()

    role_total = role_agree = 0
    role_resolvable = 0
    skill_jaccards: list[float] = []
    skill_precisions: list[float] = []
    skill_recalls: list[float] = []
    skill_covered = 0
    xling_total = xling_agree = 0
    by_role_role_agree: dict[str, list[int]] = defaultdict(list)

    for rec in records:
        ex_role = _g(rec, "role", "role_id")
        title = _g(rec, "title", "job_title")
        # --- role agreement: trust the title-resolver as the structured truth ---
        truth_role = match_title_to_role(title) if title else None
        if truth_role:
            role_resolvable += 1
            role_total += 1
            hit = 1 if (ex_role and str(ex_role) == truth_role) else 0
            role_agree += hit
            by_role_role_agree[truth_role].append(hit)

        # --- skills agreement vs O*NET/ESCO for the (resolved) role ---
        ref_role = (str(ex_role) if ex_role else None) or truth_role
        ref: set[str] = set()
        if ref_role:
            ref |= onet_skills.get(ref_role, set())
            ref |= esco_skills.get(ref_role, set())
        ex_skills = set(_as_skill_list(_g(rec, "skills", default=[])))
        if ref and ex_skills:
            skill_covered += 1
            inter = ex_skills & ref
            skill_jaccards.append(_jaccard(ex_skills, ref))
            skill_precisions.append(len(inter) / len(ex_skills))
            skill_recalls.append(len(inter) / len(ref))

        # --- cross-lingual: non-English posting role vs ESCO label in its lang ---
        lang = str(_g(rec, "lang", "language", default="en") or "en").lower()[:2]
        if lang != "en" and lang in esco_labels and title:
            xling_total += 1
            n = normalize_surface(title)
            truth_xl = esco_labels[lang].get(n)
            if truth_xl and ex_role and str(ex_role) == truth_xl:
                xling_agree += 1

    role_acc = (role_agree / role_total) if role_total else None
    mean_jac = statistics.mean(skill_jaccards) if skill_jaccards else None
    mean_prec = statistics.mean(skill_precisions) if skill_precisions else None
    mean_rec = statistics.mean(skill_recalls) if skill_recalls else None
    xling_acc = (xling_agree / xling_total) if xling_total else None

    role_surfaceable = bool(role_acc is not None and role_acc >= GROUND_TRUTH_ROLE_ACCEPT)
    skill_surfaceable = bool(mean_jac is not None and mean_jac >= GROUND_TRUTH_SKILL_JACCARD)

    return {
        "role": {
            "tier": TIER_GROUND_TRUTH if role_surfaceable else TIER_UNVALIDATED,
            "accuracy": role_acc,
            "n_compared": role_total,
            "n_resolvable_titles": role_resolvable,
            "surfaceable": role_surfaceable,
            "per_role_accuracy": {
                r: round(sum(v) / len(v), 3) for r, v in sorted(by_role_role_agree.items()) if v
            },
            "method": "extracted role vs title-resolver (match_title_to_role) agreement",
        },
        "skills": {
            "tier": TIER_GROUND_TRUTH if skill_surfaceable else TIER_UNVALIDATED,
            "mean_jaccard": mean_jac,
            "mean_precision": mean_prec,
            "mean_recall": mean_rec,
            "n_compared": skill_covered,
            "surfaceable": skill_surfaceable,
            "reference": "O*NET Technology+Skills" + ("+ESCO" if esco_skills else ""),
            "method": "extracted skill set vs O*NET/ESCO occupation→skill (Jaccard/P/R)",
        },
        "cross_lingual_role": {
            "tier": TIER_GROUND_TRUTH if xling_acc is not None else TIER_UNVALIDATED,
            "accuracy": xling_acc,
            "n_compared": xling_total,
            "method": "non-English extracted role vs ESCO occupation label in that language",
        },
    }


# =========================================================================== #
#  STRATEGY 2 — LLM-AS-JUDGE  (responsibilities_summary faithfulness)           #
# =========================================================================== #
JUDGE_SYSTEM_PROMPT = (
    "You are a meticulous evaluator checking whether a SUMMARY is faithful to a "
    "SOURCE job posting. Faithful means: every claim in the summary is supported "
    "by the posting, with no invented responsibilities, employers, seniority, or "
    "perks. Judge ONLY faithfulness/grounding — not fluency or completeness."
)

JUDGE_USER_TEMPLATE = (
    "SOURCE POSTING (verbatim, may be truncated):\n"
    "\"\"\"\n{posting}\n\"\"\"\n\n"
    "EXTRACTED responsibilities_summary:\n"
    "\"\"\"\n{summary}\n\"\"\"\n\n"
    "Score faithfulness on this rubric:\n"
    "5 = every statement is directly supported by the posting.\n"
    "4 = supported, with minor harmless paraphrase.\n"
    "3 = mostly supported; one unsupported-but-plausible detail.\n"
    "2 = several unsupported claims OR a clear distortion.\n"
    "1 = largely fabricated / contradicts the posting.\n\n"
    "Respond with ONLY a JSON object: {{\"score\": <1-5 int>, \"reason\": \"<short>\"}}"
)


# constrained judge output: {score: 1-5, reason: str} — keeps Ollama replies parseable.
_JUDGE_FMT = {
    "type": "object", "additionalProperties": False,
    "properties": {"score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                   "reason": {"type": "string"}},
    "required": ["score", "reason"],
}


def _load_judge_llm():
    """Lazily build the judge generate() — vLLM if present, else a live Ollama server.

    The judge is a *different/stronger* model than the extractor (Qwen2.5-14B on vLLM,
    or ``ollama_judge_model`` — gpt-oss:20b — natively). Returns a callable
    ``generate(prompts: list[str]) -> list[str]`` or ``None`` when no backend is
    available (judge stage then skips → tier stays ``unvalidated``). Import-clean.
    """
    # ---- preferred: vLLM (Linux/WSL), highest throughput ----
    try:
        from vllm import LLM, SamplingParams  # noqa: WPS433 (lazy heavy dep)
        model_id = settings.judge_model or "Qwen/Qwen2.5-14B-Instruct"
        try:
            llm = LLM(model=model_id, dtype="auto", gpu_memory_utilization=0.85,
                      max_model_len=8192, enforce_eager=True)
            sp = SamplingParams(temperature=0.0, max_tokens=128)
        except Exception as e:  # noqa: BLE001 — OOM / bad id → fall through to Ollama
            log.error("judge vLLM init failed (%s: %s) — trying Ollama", model_id, e)
        else:
            def generate(prompts: list[str]) -> list[str]:
                outs = llm.generate(prompts, sp)
                return [o.outputs[0].text if o.outputs else "" for o in outs]
            log.info("judge LLM ready (vLLM): %s", model_id)
            return generate
    except Exception:  # noqa: BLE001 — vllm absent; fall through to the Ollama path
        pass

    # ---- native-Windows fallback: a local Ollama server ----
    from backend.ml.llm_extract import _ollama_available, ollama_chat
    host = settings.ollama_host
    model_id = settings.ollama_judge_model
    if not _ollama_available(host, model_id):
        log.warning("no judge backend (vLLM absent, Ollama model '%s' unavailable) — "
                    "skipping LLM-as-judge [responsibilities_summary stays unvalidated]",
                    model_id)
        return None

    conc = max(1, int(settings.ollama_concurrency))

    def generate(prompts: list[str]) -> list[str]:
        def _one(p: str) -> str:
            try:
                return ollama_chat([{"role": "user", "content": p}], model=model_id,
                                   host=host, fmt=_JUDGE_FMT, num_ctx=settings.ollama_num_ctx)
            except Exception as e:  # noqa: BLE001 — failed call → unparseable → dropped score
                log.warning("judge ollama call failed (%s)", e)
                return ""
        if conc == 1:
            return [_one(p) for p in prompts]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=conc) as ex:
            return list(ex.map(_one, prompts))

    log.info("judge LLM ready (Ollama): %s", model_id)
    return generate


def _parse_judge_score(text: str) -> int | None:
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        obj = json.loads(text[start:end])
        s = int(obj.get("score"))
        return s if 1 <= s <= 5 else None
    except Exception:  # noqa: BLE001
        return None


def validate_judge(records: list[dict], sample: int = JUDGE_SAMPLE) -> dict[str, Any]:
    """LLM-as-judge faithfulness scoring for ``responsibilities_summary`` on a sample."""
    field = "responsibilities_summary"
    cand = [r for r in records if _g(r, field) and _g(r, "description", "posting_text", "body")]
    if not cand:
        return {field: {"tier": TIER_UNVALIDATED, "reason": "no summaries+source to judge",
                        "n_scored": 0}}
    sample_recs = cand[:sample]
    generate = _load_judge_llm()
    if generate is None:
        return {field: {"tier": TIER_UNVALIDATED, "reason": "judge model unavailable (vLLM absent)",
                        "n_candidates": len(cand), "n_scored": 0}}

    prompts = []
    for r in sample_recs:
        posting = str(_g(r, "description", "posting_text", "body", default=""))[:6000]
        summary = str(_g(r, field, default=""))
        prompts.append(
            JUDGE_SYSTEM_PROMPT + "\n\n" + JUDGE_USER_TEMPLATE.format(posting=posting, summary=summary)
        )

    scores: list[int] = []
    t0 = time.time()
    for i in range(0, len(prompts), 64):  # batch + heartbeat
        batch = prompts[i:i + 64]
        for text in generate(batch):
            s = _parse_judge_score(text)
            if s is not None:
                scores.append(s)
        print(f"[validate] judge: scored {min(i + 64, len(prompts))}/{len(prompts)} "
              f"elapsed {time.time() - t0:.0f}s", flush=True)

    if not scores:
        return {field: {"tier": TIER_UNVALIDATED, "reason": "judge produced no parseable scores",
                        "n_scored": 0}}
    mean = statistics.mean(scores)
    dist = {str(k): scores.count(k) for k in range(1, 6)}
    return {field: {
        "tier": TIER_JUDGE,
        "mean_faithfulness": round(mean, 3),
        "pct_faithful_ge4": round(sum(s >= 4 for s in scores) / len(scores), 3),
        "score_distribution": dist,
        "n_scored": len(scores),
        "scale": "1-5 (5=fully grounded)",
        "method": "stronger model scores summary faithfulness-to-posting on a sample",
    }}


# =========================================================================== #
#  STRATEGY 3 — SELF-CONSISTENCY  +  CROSS-FIELD CONSISTENCY                    #
# =========================================================================== #
def _field_agree(a: Any, b: Any, *, is_skills: bool = False) -> float:
    """Agreement between two extractions of one field (1.0 exact, Jaccard for skills)."""
    if is_skills:
        return _jaccard(set(_as_skill_list(a)), set(_as_skill_list(b)))
    na = normalize_surface(str(a)) if a is not None else ""
    nb = normalize_surface(str(b)) if b is not None else ""
    if na == "" and nb == "":
        return 1.0
    return 1.0 if na == nb else 0.0


def validate_self_consistency(records: list[dict],
                              sample: int = SELF_CONSISTENCY_SAMPLE) -> dict[str, Any]:
    """Re-extract a sample at temperature>0 and measure per-field cross-run agreement.

    Low agreement → the posting is flagged low-confidence (``low_confidence_ids``),
    feeding the extractor's abstain rule. Re-extraction goes through
    ``llm_extract.reextract`` (lazy); when that's unavailable we **still** run the
    structural cross-field consistency checks below (which need no model), so this
    stage always yields *some* real number.
    """
    sample_recs = records[:sample]
    reextract = _load_reextractor()
    per_field: dict[str, list[float]] = defaultdict(list)
    low_conf_ids: list[Any] = []

    if reextract is not None:
        postings = [str(_g(r, "description", "posting_text", "body", default="")) for r in sample_recs]
        try:
            second = _to_records(reextract(postings, temperature=SELF_CONSISTENCY_TEMPERATURE))
        except Exception as e:  # noqa: BLE001 — re-extraction failure shouldn't kill the stage
            log.error("self-consistency re-extraction failed: %s", e)
            second = []
        for orig, again in zip(sample_recs, second):
            agreements = []
            for f in SELF_CONSISTENCY_FIELDS:
                score = _field_agree(_g(orig, f), _g(again, f), is_skills=(f == "skills"))
                per_field[f].append(score)
                agreements.append(score)
            if agreements and statistics.mean(agreements) < LOW_CONFIDENCE_AGREEMENT:
                low_conf_ids.append(_g(orig, "posting_id", "id", "url", default=None))
    else:
        log.info("re-extractor unavailable — self-consistency runs cross-field checks only")

    field_report = {}
    for f in SELF_CONSISTENCY_FIELDS:
        vals = per_field.get(f)
        if vals:
            acc = statistics.mean(vals)
            field_report[f] = {
                "tier": TIER_SELF if acc >= SELF_CONSISTENCY_ACCEPT else TIER_UNVALIDATED,
                "agreement": round(acc, 3),
                "n_pairs": len(vals),
                "method": f"cross-run agreement at temperature={SELF_CONSISTENCY_TEMPERATURE}",
            }

    cross = _cross_field_consistency(records)
    return {
        "fields": field_report,
        "cross_field": cross,
        "low_confidence_ids": [i for i in low_conf_ids if i is not None],
        "n_low_confidence": len([i for i in low_conf_ids if i is not None]),
        "n_resampled": len(per_field.get(SELF_CONSISTENCY_FIELDS[0], [])),
    }


def _load_reextractor():
    """Lazy handle to ``llm_extract.reextract(postings, temperature=...)`` or ``None``."""
    try:
        from backend.ml import llm_extract  # noqa: WPS433 (lazy heavy/optional)

        fn = getattr(llm_extract, "reextract", None)
        if callable(fn):
            return fn
        log.info("llm_extract.reextract not found — self-consistency re-run skipped")
    except Exception as e:  # noqa: BLE001 — module/heavy import absent → skip
        log.warning("re-extractor unavailable (%s) — self-consistency cross-field only", e)
    return None


# years_required bands that should co-occur with each seniority label
_SENIORITY_YEARS = {
    "intern": (0, 1), "junior": (0, 3), "associate": (0, 3), "entry": (0, 2),
    "mid": (2, 6), "senior": (4, 12), "staff": (6, 20), "principal": (8, 30),
    "lead": (5, 20), "manager": (5, 25), "director": (8, 30),
}


def _cross_field_consistency(records: list[dict]) -> dict[str, Any]:
    """Internal-logic checks needing no second run (seniority↔years, remote↔location).

    These are *moderate-tier* signals: a contradiction means the extraction is
    internally inconsistent (likely wrong), independent of any external truth.
    """
    sy_total = sy_ok = 0
    rl_total = rl_ok = 0
    for rec in records:
        # NB: do NOT use normalize_surface here — it strips seniority tokens by
        # design (taxonomy's job), which would erase the very signal we check.
        sen = str(_g(rec, "seniority", default="")).strip().lower()
        yrs = _g(rec, "years_required", "years_experience", "min_years")
        if sen and yrs is not None:
            band = next((b for k, b in _SENIORITY_YEARS.items() if k in sen), None)
            try:
                y = float(yrs)
            except (TypeError, ValueError):
                y = None
            if band and y is not None:
                sy_total += 1
                # allow 1-year slack on either side
                if band[0] - 1 <= y <= band[1] + 1:
                    sy_ok += 1
        remote = str(_g(rec, "remote", "work_mode", default="")).lower()
        loc = str(_g(rec, "location", default="")).lower()
        if remote in ("onsite", "on-site", "in-office") and loc:
            rl_total += 1
            if "remote" not in loc:
                rl_ok += 1

    return {
        "tier": TIER_SELF,
        "seniority_vs_years": {
            "consistency": round(sy_ok / sy_total, 3) if sy_total else None,
            "n_checked": sy_total,
        },
        "remote_vs_location": {
            "consistency": round(rl_ok / rl_total, 3) if rl_total else None,
            "n_checked": rl_total,
        },
        "method": "internal cross-field logic (seniority↔years_required, remote↔location)",
    }


# =========================================================================== #
#  Report assembly + checkpointing                                             #
# =========================================================================== #
def _read_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _write_checkpoint(ckpt: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(ckpt), encoding="utf-8")


def _assemble_field_tiers(gt: dict, judge: dict, sc: dict) -> dict[str, Any]:
    """Collapse the three strategies into one per-field verdict.

    Precedence: a field validated as ``ground-truth`` (surfaceable) wins; else the
    best moderate signal (judge / self-consistency); else ``unvalidated``.
    """
    fields: dict[str, Any] = {}
    # ground-truth fields
    for f in GROUND_TRUTH_FIELDS:
        if f in gt:
            fields[f] = {"tier": gt[f]["tier"], "ground_truth": gt[f]}
    if "cross_lingual_role" in gt:
        fields.setdefault("role", {}).setdefault("ground_truth_cross_lingual",
                                                 gt["cross_lingual_role"])
    # judge fields
    for f, payload in judge.items():
        fields.setdefault(f, {"tier": payload["tier"]})
        fields[f]["judge"] = payload
        if fields[f].get("tier") == TIER_UNVALIDATED and payload["tier"] != TIER_UNVALIDATED:
            fields[f]["tier"] = payload["tier"]
    # self-consistency fields
    for f, payload in sc.get("fields", {}).items():
        entry = fields.setdefault(f, {"tier": payload["tier"]})
        entry["self_consistency"] = payload
        # don't demote a ground-truth/judge tier; only upgrade from unvalidated
        if entry.get("tier") == TIER_UNVALIDATED and payload["tier"] != TIER_UNVALIDATED:
            entry["tier"] = payload["tier"]
    return fields


def run(*, judge_sample: int = JUDGE_SAMPLE,
        self_consistency_sample: int = SELF_CONSISTENCY_SAMPLE,
        resume: bool = True) -> dict:
    """Run all three validation strategies → write + return the report dict.

    Idempotent + resumable: each stage's result is checkpointed to
    ``validation_checkpoint.json``; a re-run with ``resume=True`` reuses finished
    stages. Emits ``validation_report.json``. Returns the report.
    """
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    records = _load_extracted()
    ckpt = _read_checkpoint() if resume else {}

    with stage_timer(log, "ground-truth validation"):
        if resume and "ground_truth" in ckpt:
            gt = ckpt["ground_truth"]
            log.info("ground-truth: reused from checkpoint")
        else:
            gt = validate_ground_truth(records)
            ckpt["ground_truth"] = gt
            _write_checkpoint(ckpt)

    with stage_timer(log, "LLM-as-judge validation"):
        if resume and "judge" in ckpt:
            judge = ckpt["judge"]
            log.info("judge: reused from checkpoint")
        else:
            judge = validate_judge(records, sample=judge_sample)
            ckpt["judge"] = judge
            _write_checkpoint(ckpt)

    with stage_timer(log, "self-consistency validation"):
        if resume and "self_consistency" in ckpt:
            sc = ckpt["self_consistency"]
            log.info("self-consistency: reused from checkpoint")
        else:
            sc = validate_self_consistency(records, sample=self_consistency_sample)
            ckpt["self_consistency"] = sc
            _write_checkpoint(ckpt)

    fields = _assemble_field_tiers(gt, judge, sc)
    surfaceable = sorted(f for f, v in fields.items() if v.get("tier") == TIER_GROUND_TRUTH)
    moderate = sorted(f for f, v in fields.items() if v.get("tier") in (TIER_JUDGE, TIER_SELF))
    store_only = sorted(f for f, v in fields.items() if v.get("tier") == TIER_UNVALIDATED)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_records": len(records),
        "extracted_source": str(EXTRACTED_PARQUET),
        "no_hand_labels": True,
        "fields": fields,
        "strategies": {
            "ground_truth": gt,
            "judge": judge,
            "self_consistency": sc,
        },
        "tier_summary": {
            "surfaceable_as_fact": surfaceable,
            "moderate_confidence": moderate,
            "store_only_unvalidated": store_only,
        },
        "low_confidence_posting_ids": sc.get("low_confidence_ids", []),
        "tiers": {
            "ground-truth": "externally anchored — surfaceable as fact",
            "judge": "LLM-as-judge faithfulness — moderate",
            "self-consistency": "cross-run/cross-field agreement — moderate",
            "unvalidated": "store-only, NOT surfaced",
        },
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("validation report → %s | surfaceable=%s moderate=%s store-only=%s",
             REPORT_PATH, surfaceable, moderate, store_only)
    return report


def load_report() -> dict:
    """Read the last-written validation report (for the API surfacing-gate)."""
    return json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.exists() else {}


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2)[:4000])
