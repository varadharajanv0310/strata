"""LLM corpus extraction — the GPU differentiator (roles-only, abstain-capable).

This is the stage that makes strata *honest* about what a job actually is. A keyword
matcher (``fingerprint.extract_skills``) can tell you "the word Python appears in this
posting"; it cannot tell you that the title says "Engineer" but the body describes a
Tier-1 support queue, that "AI Engineer" here really means prompt-tuning a vendor SDK,
or that the posting is too thin to claim anything at all. A 7B instruct model reading
the **full description** can — and with GUIDED/CONSTRAINED JSON decoding its output is
*always* schema-valid, so the cost is GPU time, not parsing fragility.

Roles-only guardrail (SCOPE.md, non-negotiable)
------------------------------------------------
The extraction schema is about the ROLE and the WORK, never the employer. There is NO
company / employer / company_size / industry / team / funding / perks / benefits axis
anywhere in the schema or the prompt. ``employer`` survives only as the pre-existing
internal dedup field on the posting parquet (untouched here, never read into the schema).

Honesty design
--------------
Every field has a legal *abstain / unknown* value, and there is a top-level ``abstain``
bool plus a per-item ``honesty_flag``. The model is instructed to abstain rather than
guess. ``skills`` is CONSTRAINED to the taxonomy vocab (``fingerprint.SKILL_VOCAB``) for
precision; ``skills_emerging`` is free text so genuinely new tech still surfaces;
``disambiguated_role`` is free text so EMERGING roles aren't crushed into a fixed list.

Throughput
----------
On an RTX 5080 (16 GB, FP8) with vLLM continuous batching + guided JSON, a 7B instruct
model sustains roughly **~4k–7k postings/GPU-hour** on real posting-length text, i.e.
**~15–28 GPU-hours for a 400k-posting corpus**. The same corpus on a CPU laptop with a
naive per-posting call is a **6–10 week** job — this stage is the reason the GPU exists.

Build notes
-----------
* Heavy deps (vllm, torch) are imported **lazily**. If they are absent the module still
  imports cleanly; ``run()`` logs a clear skip and returns a summary with ``mode="skipped"``.
* Idempotent + resumable: the corpus is split into shards of ``shard_size``; a completed
  shard writes ``shard_XXX.parquet`` + appends to the raw JSONL, and a checkpoint file
  records which shards are done. Re-running skips finished shards. Heartbeat every
  ``HEARTBEAT_EVERY`` postings.
* Teacher→student distillation is left as a clean OPTIONAL hook (``distill_hook``) — we
  do not build a fragile distillation pipeline this pass.

Outputs
-------
* ``staging/extracted/postings_extracted.parquet`` — one row per posting, all schema fields.
* ``staging/extracted/raw_outputs.jsonl``          — raw model JSON per posting (audit trail).
* ``staging/extracted/_checkpoint.json``           — resume state (done shards).

Entry point
-----------
``run(model=..., max_postings=None, shard_size=2000, temperature=0.0, time_cap_s=None)``
returns a summary dict. ``load_extracted()`` reads the consolidated parquet back.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.core.config import settings
from backend.core.logging import get_logger, stage_timer
from backend.ml.fingerprint import SKILL_VOCAB

log = get_logger("ml.llm_extract")

# --------------------------------------------------------------------------- #
#  paths / constants                                                          #
# --------------------------------------------------------------------------- #
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
OUT_DIR_REL = "extracted"
CONSOLIDATED = "postings_extracted.parquet"
RAW_JSONL = "raw_outputs.jsonl"
CHECKPOINT = "_checkpoint.json"

HEARTBEAT_EVERY = 200       # log a heartbeat every N postings within a shard
MAX_DESC_CHARS = 6000       # truncate very long descriptions to keep prompts bounded

# input posting parquets (common_crawl is canonical; others accepted if present)
POSTING_PARQUETS = [
    "common_crawl/postings.parquet",
    "eures/postings.parquet",
    "bundesagentur/postings.parquet",
    "mycareersfuture/postings.parquet",
    "usajobs/postings.parquet",
    "remoteok/postings.parquet",
]

# --------------------------------------------------------------------------- #
#  the extraction schema (role-only, abstain-capable)                          #
#  -- this is the contract; guided decoding is generated from it.              #
# --------------------------------------------------------------------------- #
SENIORITY_ENUM = [
    "intern", "junior", "mid", "senior", "staff",
    "principal", "lead", "manager", "unknown",
]
CONFIDENCE_ENUM = ["high", "medium", "low", "abstain"]
WORK_ENUM = ["onsite", "hybrid", "remote", "unknown"]
COMP_ENUM = ["salary", "salary+equity", "salary+bonus", "contract", "unknown"]

# ALL schema fields are about the ROLE and the WORK. There is deliberately NO
# company / employer / industry / team / funding / perks / benefits field. If you
# add an employer-shaped field you have broken the roles-only charter (SCOPE.md).
EXTRACT_FIELDS: list[str] = [
    "posting_id",
    "disambiguated_role",
    "role_confidence",
    "skills",
    "skills_emerging",
    "seniority",
    "years_required",
    "responsibilities_summary",
    "work_arrangement",
    "comp_structure",
    "language",
    "abstain",
    "honesty_flag",
]


def extraction_json_schema() -> dict[str, Any]:
    """JSON Schema handed to vLLM ``guided_json`` so every output is schema-valid.

    ``skills`` is constrained to the taxonomy vocab (closed enum) for precision;
    ``skills_emerging`` and ``disambiguated_role`` are free so new tech / emerging
    roles still surface. Roles-only: no employer/company axis anywhere.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "disambiguated_role": {"type": "string"},
            "role_confidence": {"type": "string", "enum": CONFIDENCE_ENUM},
            "skills": {
                "type": "array",
                "items": {"type": "string", "enum": list(SKILL_VOCAB)},
            },
            "skills_emerging": {"type": "array", "items": {"type": "string"}},
            "seniority": {"type": "string", "enum": SENIORITY_ENUM},
            "years_required": {"type": ["integer", "null"]},
            "responsibilities_summary": {"type": "string"},
            "work_arrangement": {"type": "string", "enum": WORK_ENUM},
            "comp_structure": {"type": "string", "enum": COMP_ENUM},
            "language": {"type": "string"},
            "abstain": {"type": "boolean"},
            "honesty_flag": {"type": ["string", "null"]},
        },
        "required": [
            "disambiguated_role", "role_confidence", "skills", "skills_emerging",
            "seniority", "years_required", "responsibilities_summary",
            "work_arrangement", "comp_structure", "language", "abstain", "honesty_flag",
        ],
    }


def _empty_extraction(posting_id: int, *, language: str = "unknown") -> dict[str, Any]:
    """The honest abstain record — used when text is too thin or a row errors."""
    return {
        "posting_id": int(posting_id),
        "disambiguated_role": "unknown",
        "role_confidence": "abstain",
        "skills": [],
        "skills_emerging": [],
        "seniority": "unknown",
        "years_required": None,
        "responsibilities_summary": "",
        "work_arrangement": "unknown",
        "comp_structure": "unknown",
        "language": language,
        "abstain": True,
        "honesty_flag": None,
    }


# --------------------------------------------------------------------------- #
#  the prompt (role-only extraction + abstain)                                 #
# --------------------------------------------------------------------------- #
_VOCAB_PREVIEW = ", ".join(SKILL_VOCAB[:60])

SYSTEM_PROMPT = (
    "You are a careful labour-market analyst. You read a single job posting and extract "
    "a STRICT JSON object describing the ROLE and the WORK ONLY.\n\n"
    "ABSOLUTE RULES:\n"
    "1. ROLES ONLY. Describe the role and the day-to-day work. NEVER describe or infer "
    "anything about the employer: no company name, company size, industry, team, "
    "department, funding, perks, or benefits. Those fields do not exist in your output.\n"
    "2. BE HONEST. If the posting text is too thin, boilerplate, or contradictory to "
    "extract a field truthfully, use the field's unknown/abstain value rather than "
    "guessing. Set top-level \"abstain\": true when the whole posting is too thin to "
    "characterise the role honestly.\n"
    "3. FLAG MISMATCHES. If the title and the body disagree about what the job actually "
    "is (e.g. title says 'Engineer' but the body is a support queue), set "
    "\"honesty_flag\" to a short note describing the mismatch; otherwise null.\n"
    "4. SKILLS are inferred from the actual responsibilities, not keyword-spotted. Put "
    "skills that appear in the provided controlled vocabulary into \"skills\". Put any "
    "real skill/tool mentioned that is NOT in the vocabulary into \"skills_emerging\".\n"
    "5. \"disambiguated_role\" is a clean canonical role title and MAY be an emerging "
    "role not on any fixed list. Use \"unknown\" if the role is unclear.\n"
    "6. \"language\" is the ISO-639-1 code of the posting text.\n"
    "Return ONLY the JSON object. No prose, no markdown."
)

USER_TEMPLATE = (
    "Controlled skill vocabulary (only these may appear in \"skills\"; everything else "
    "goes in \"skills_emerging\"):\n{vocab}\n\n"
    "Extract the JSON object for this posting.\n\n"
    "TITLE: {title}\n\n"
    "DESCRIPTION:\n{description}\n"
)

# alias kept for the build spec's "a PROMPT constant" requirement
PROMPT = {"system": SYSTEM_PROMPT, "user_template": USER_TEMPLATE}


def build_messages(title: str, description: str) -> list[dict[str, str]]:
    """Chat messages for one posting (system + user), vocab injected."""
    desc = (description or "").strip()
    if len(desc) > MAX_DESC_CHARS:
        desc = desc[:MAX_DESC_CHARS] + " …[truncated]"
    user = USER_TEMPLATE.format(
        vocab=", ".join(SKILL_VOCAB),
        title=(title or "").strip() or "(none)",
        description=desc or "(none)",
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# --------------------------------------------------------------------------- #
#  output paths / checkpoint                                                    #
# --------------------------------------------------------------------------- #
def _out_dir() -> Path:
    d = settings.staging_dir / OUT_DIR_REL
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path() -> Path:
    return _out_dir() / CHECKPOINT


def _load_checkpoint() -> dict[str, Any]:
    p = _checkpoint_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.warning("llm_extract: checkpoint unreadable (%s) — starting fresh", e)
    return {"done_shards": [], "model": None, "n_done": 0}


def _save_checkpoint(state: dict[str, Any]) -> None:
    try:
        _checkpoint_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("llm_extract: could not persist checkpoint (%s)", e)


# --------------------------------------------------------------------------- #
#  input loading                                                               #
# --------------------------------------------------------------------------- #
def _load_postings(max_postings: int | None):
    """Concatenate every available posting parquet into one frame with a stable id.

    ``posting_id`` is the 0-based row index over the concatenated corpus (the parquets
    carry no natural key). ``employer`` (if present) is NOT carried into extraction — it
    stays the internal dedup field only. Returns a pandas DataFrame with
    posting_id / title / description, or ``None`` if nothing is on disk.
    """
    import pandas as pd

    frames = []
    for rel in POSTING_PARQUETS:
        p = settings.staging_dir / rel
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p, columns=None)
        except Exception as e:  # noqa: BLE001
            log.warning("llm_extract: could not read %s (%s) — skipping", p, e)
            continue
        if df.empty:
            continue
        keep = pd.DataFrame({
            "title": df.get("title", "").fillna("").astype(str),
            "description": df.get("description", "").fillna("").astype(str),
            "_source": rel,
        })
        frames.append(keep)
        log.info("llm_extract: loaded %d postings from %s", len(keep), rel)

    if not frames:
        return None
    corpus = pd.concat(frames, ignore_index=True)
    corpus.insert(0, "posting_id", range(len(corpus)))
    if max_postings is not None:
        corpus = corpus.iloc[:max_postings].copy()
    return corpus


def _iter_shards(corpus, shard_size: int) -> Iterable[tuple[int, Any]]:
    n = len(corpus)
    for start in range(0, n, shard_size):
        yield start // shard_size, corpus.iloc[start:start + shard_size]


# --------------------------------------------------------------------------- #
#  vLLM engine (lazy, graceful)                                                #
# --------------------------------------------------------------------------- #
class _Engine:
    """Thin wrapper over a vLLM LLM with guided-JSON sampling.

    Constructed lazily so that importing this module never pulls in vllm/torch.
    Raises on construction if the stack is missing — callers catch and skip.
    """

    def __init__(self, model: str, temperature: float, max_tokens: int = 640):
        # heavy imports are confined to here
        from vllm import LLM, SamplingParams  # type: ignore
        try:
            from vllm.sampling_params import GuidedDecodingParams  # type: ignore
            guided = GuidedDecodingParams(json=extraction_json_schema())
            self._sampling = SamplingParams(
                temperature=temperature, max_tokens=max_tokens,
                guided_decoding=guided,
            )
        except Exception:  # noqa: BLE001 — older vLLM API
            self._sampling = SamplingParams(temperature=temperature, max_tokens=max_tokens)
            log.warning("llm_extract: GuidedDecodingParams unavailable — "
                        "running UNCONSTRAINED decode (output validated post-hoc)")

        # FP8 on the 5080: weights quantised to fit 7B comfortably in 16 GB with
        # headroom for the KV cache + continuous batching.
        self._llm = LLM(
            model=model,
            dtype="auto",
            quantization="fp8",
            gpu_memory_utilization=0.90,
            max_model_len=8192,
            enforce_eager=False,
            trust_remote_code=True,
        )
        self.model = model
        log.info("llm_extract: vLLM engine up — model=%s fp8", model)

    def generate(self, prompts: list[str]) -> list[str]:
        outs = self._llm.generate(prompts, self._sampling)
        return [o.outputs[0].text if o.outputs else "" for o in outs]

    def render(self, messages_batch: list[list[dict[str, str]]]) -> list[str]:
        """Apply the model's chat template to each message list."""
        tok = self._llm.get_tokenizer()
        rendered = []
        for messages in messages_batch:
            try:
                rendered.append(tok.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True))
            except Exception:  # noqa: BLE001 — tokenizer without chat template
                rendered.append(messages[-1]["content"])
        return rendered


def _vllm_available() -> bool:
    try:
        import vllm  # noqa: F401
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("llm_extract: vLLM/torch stack unavailable (%s) — stage will SKIP", e)
        return False


# --------------------------------------------------------------------------- #
#  output coercion — enforce the schema even on a degraded (unconstrained) run #
# --------------------------------------------------------------------------- #
_VOCAB_SET = {s.lower() for s in SKILL_VOCAB}


def _coerce(raw_text: str, posting_id: int) -> dict[str, Any]:
    """Parse + clamp a model output to the schema; abstain on anything unparseable."""
    try:
        obj = json.loads(raw_text)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
    except Exception:
        # last-ditch: pull the first {...} block out of chatter
        try:
            s = raw_text[raw_text.index("{"): raw_text.rindex("}") + 1]
            obj = json.loads(s)
        except Exception:
            return _empty_extraction(posting_id)

    out = _empty_extraction(posting_id)

    def _enum(val, allowed, default):
        v = str(val).strip().lower() if val is not None else default
        return v if v in allowed else default

    out["disambiguated_role"] = str(obj.get("disambiguated_role") or "unknown").strip() or "unknown"
    out["role_confidence"] = _enum(obj.get("role_confidence"), CONFIDENCE_ENUM, "abstain")
    out["seniority"] = _enum(obj.get("seniority"), SENIORITY_ENUM, "unknown")
    out["work_arrangement"] = _enum(obj.get("work_arrangement"), WORK_ENUM, "unknown")
    out["comp_structure"] = _enum(obj.get("comp_structure"), COMP_ENUM, "unknown")

    # skills clamped to the vocab (precision); emerging stays free
    sk = obj.get("skills") or []
    if isinstance(sk, list):
        out["skills"] = [s for s in (str(x).strip().lower() for x in sk) if s in _VOCAB_SET]
    em = obj.get("skills_emerging") or []
    if isinstance(em, list):
        out["skills_emerging"] = [str(x).strip() for x in em if str(x).strip()][:24]

    yr = obj.get("years_required")
    if isinstance(yr, bool):
        yr = None
    if isinstance(yr, (int, float)) and 0 <= yr <= 60:
        out["years_required"] = int(yr)

    out["responsibilities_summary"] = str(obj.get("responsibilities_summary") or "").strip()[:600]
    lang = str(obj.get("language") or "unknown").strip().lower()
    out["language"] = lang[:5] if lang else "unknown"
    out["abstain"] = bool(obj.get("abstain", False))
    hf = obj.get("honesty_flag")
    out["honesty_flag"] = str(hf).strip()[:200] if hf else None

    # guardrail: strip any stray employer-shaped key the model may have hallucinated —
    # it can never enter our output (roles-only). out only ever has schema keys, so this
    # is belt-and-suspenders: we simply never copy unknown keys from `obj`.
    return out


# --------------------------------------------------------------------------- #
#  teacher -> student distillation hook (OPTIONAL — stub only)                  #
# --------------------------------------------------------------------------- #
def distill_hook(
    extracted_parquet: Path | None = None,
    *,
    student_model: str | None = None,
) -> dict[str, Any]:
    """OPTIONAL teacher→student hook (NOT built this pass).

    Once a 7B teacher has labelled the corpus, those labels are clean SFT data for a
    smaller/faster student (cheaper re-runs as new postings land). We expose only a
    clean entry point here and deliberately do NOT implement a fragile distillation
    pipeline. Returns a not-implemented marker.
    """
    return {
        "implemented": False,
        "note": "teacher labels at staging/extracted/postings_extracted.parquet are "
                "ready-made SFT data; wire a student trainer here when desired.",
        "teacher_parquet": str(extracted_parquet or (_out_dir() / CONSOLIDATED)),
        "student_model": student_model,
    }


# --------------------------------------------------------------------------- #
#  shard processing                                                            #
# --------------------------------------------------------------------------- #
def _process_shard(engine: _Engine, shard, *, batch_size: int = 64) -> list[dict[str, Any]]:
    import pandas as pd  # noqa: F401  (shard is a pandas frame)

    rows: list[dict[str, Any]] = []
    records = shard.to_dict("records")
    done = 0
    for b0 in range(0, len(records), batch_size):
        batch = records[b0:b0 + batch_size]
        msgs = [build_messages(r["title"], r["description"]) for r in batch]
        try:
            prompts = engine.render(msgs)
            texts = engine.generate(prompts)
        except Exception as e:  # noqa: BLE001 — a bad batch must not sink the shard
            log.warning("llm_extract: batch failed (%s) — abstaining %d rows", e, len(batch))
            texts = [""] * len(batch)
        for r, txt in zip(batch, texts):
            rec = _coerce(txt, int(r["posting_id"]))
            rec["_raw"] = txt  # carried to JSONL, dropped before parquet
            rows.append(rec)
        done += len(batch)
        if done % HEARTBEAT_EVERY < batch_size:
            log.info("llm_extract:   …%d/%d postings in shard", done, len(records))
    return rows


def _write_shard(shard_idx: int, rows: list[dict[str, Any]]) -> Path:
    import pandas as pd

    out_dir = _out_dir()
    # raw JSONL (audit) — append
    with open(out_dir / RAW_JSONL, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(
                {"posting_id": r["posting_id"], "raw": r.get("_raw", "")},
                ensure_ascii=False) + "\n")
    # per-shard parquet (resume unit) — schema fields only
    clean = [{k: r[k] for k in (["posting_id"] + EXTRACT_FIELDS[1:])} for r in rows]
    df = pd.DataFrame(clean)
    shard_path = out_dir / f"shard_{shard_idx:04d}.parquet"
    df.to_parquet(shard_path, index=False)
    return shard_path


def _consolidate() -> Path | None:
    """Merge all shard parquets into the single consolidated output."""
    import pandas as pd

    out_dir = _out_dir()
    parts = sorted(out_dir.glob("shard_*.parquet"))
    if not parts:
        return None
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    df = df.drop_duplicates(subset=["posting_id"], keep="last").sort_values("posting_id")
    dest = out_dir / CONSOLIDATED
    df.to_parquet(dest, index=False)
    return dest


# --------------------------------------------------------------------------- #
#  entry point                                                                 #
# --------------------------------------------------------------------------- #
def run(
    model: str = DEFAULT_MODEL,
    *,
    max_postings: int | None = None,
    shard_size: int = 2000,
    temperature: float = 0.0,
    time_cap_s: float | None = None,
    batch_size: int = 64,
    distill_student: str | None = None,  # reserved for the optional distill hook
) -> dict[str, Any]:
    """Run LLM corpus extraction over the posting corpus → staging/extracted/.

    Resumable per shard (skips shards already in the checkpoint), graceful if the GPU
    stack is missing (returns ``mode="skipped"``), respects ``time_cap_s`` by stopping
    cleanly between shards and consolidating what landed.

    Returns a summary dict: {mode, model, postings, shards_done, shards_total,
    rows_written, abstained, out, written}.
    """
    with stage_timer(log, "ml.llm_extract"):
        t0 = time.time()

        corpus = _load_postings(max_postings)
        if corpus is None or len(corpus) == 0:
            log.warning("llm_extract: no posting parquets found in %s — nothing to extract",
                        settings.staging_dir)
            return {"mode": "skipped", "reason": "no_postings", "postings": 0,
                    "written": False}

        n = len(corpus)
        shards_total = (n + shard_size - 1) // shard_size

        if not _vllm_available():
            return {"mode": "skipped", "reason": "no_vllm", "postings": n,
                    "shards_total": shards_total, "written": False,
                    "note": "install vllm+torch on the 5080 to run extraction"}

        state = _load_checkpoint()
        if state.get("model") not in (None, model):
            log.info("llm_extract: model changed (%s → %s) — resetting checkpoint",
                     state.get("model"), model)
            state = {"done_shards": [], "model": model, "n_done": 0}
        state["model"] = model
        done_shards = set(state.get("done_shards", []))

        try:
            engine = _Engine(model, temperature=temperature)
        except Exception as e:  # noqa: BLE001 — construction needs the GPU
            log.warning("llm_extract: could not start vLLM engine (%s) — SKIP", e)
            return {"mode": "skipped", "reason": "engine_init_failed", "error": str(e),
                    "postings": n, "shards_total": shards_total, "written": False}

        rows_written = 0
        shards_run = 0
        capped = False
        for shard_idx, shard in _iter_shards(corpus, shard_size):
            if shard_idx in done_shards:
                continue
            if time_cap_s is not None and (time.time() - t0) > time_cap_s:
                log.info("llm_extract: time cap %.0fs reached — stopping at shard %d "
                         "(resumable)", time_cap_s, shard_idx)
                capped = True
                break
            log.info("llm_extract: ▶ shard %d/%d (%d postings)",
                     shard_idx, shards_total - 1, len(shard))
            rows = _process_shard(engine, shard, batch_size=batch_size)
            _write_shard(shard_idx, rows)
            rows_written += len(rows)
            shards_run += 1
            done_shards.add(shard_idx)
            state["done_shards"] = sorted(done_shards)
            state["n_done"] = int(state.get("n_done", 0)) + len(rows)
            _save_checkpoint(state)
            log.info("llm_extract: ✓ shard %d done (%d rows, %d/%d shards complete)",
                     shard_idx, len(rows), len(done_shards), shards_total)

        dest = _consolidate()

        # abstain rate (honesty signal) from the consolidated frame
        abstained = 0
        if dest is not None:
            try:
                import pandas as pd
                cdf = pd.read_parquet(dest, columns=["abstain"])
                abstained = int(cdf["abstain"].sum())
            except Exception:  # noqa: BLE001
                pass

        # optional distillation hook (not built — clean marker only)
        distill = distill_hook(dest, student_model=distill_student) if distill_student else None

        summary = {
            "mode": "extract",
            "model": model,
            "postings": n,
            "shards_total": shards_total,
            "shards_done": len(done_shards),
            "shards_this_run": shards_run,
            "rows_written": rows_written,
            "abstained": abstained,
            "time_capped": capped,
            "out": str(dest) if dest else None,
            "written": dest is not None,
            "distill": distill,
        }
        log.info("llm_extract: %s", summary)
        return summary


# --------------------------------------------------------------------------- #
#  reader                                                                      #
# --------------------------------------------------------------------------- #
def load_extracted():
    """Read the consolidated extraction parquet back (or an empty frame)."""
    import pandas as pd

    p = _out_dir() / CONSOLIDATED
    if not p.exists():
        log.info("llm_extract: no extracted parquet at %s yet", p)
        return pd.DataFrame(columns=EXTRACT_FIELDS)
    return pd.read_parquet(p)


if __name__ == "__main__":  # pragma: no cover — manual smoke / GPU run
    import argparse

    ap = argparse.ArgumentParser(description="LLM corpus extraction (roles-only)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--max-postings", type=int, default=None)
    ap.add_argument("--shard-size", type=int, default=2000)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--time-cap-s", type=float, default=None)
    args = ap.parse_args()
    print(run(model=args.model, max_postings=args.max_postings,
              shard_size=args.shard_size, temperature=args.temperature,
              time_cap_s=args.time_cap_s))
