"""Extraction validation gate — does qwen3:8b hold on German / 2021-historical text?

Runs the REAL extractor over a German-weighted stratified sample drawn from the already-
landed corpus (HF DE + HF EN + SmartRecruiters incl. India + Personio DE), measures
throughput, and grades role quality per-language. The English side uses the title
resolver (its native yardstick). The GERMAN side is judged by the MULTILINGUAL adjudicator
(gpt-oss:20b reads German natively) asking "is this the right role for this posting?" —
NOT the English title resolver, which would mis-grade German rather than measure it, and
NOT ESCO (its German occupation labels are not landed). The adjudicator is also run on an
English slice to calibrate itself against the title resolver.

Isolated: extracts in-memory, never touches staging/extracted or the warehouse. Writes
reports/valgate_result.json.
"""
from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import settings
from backend.ml.llm_extract import build_messages, extraction_json_schema
from backend.warehouse.taxonomy import match_title_to_role, normalize_surface

MODEL = settings.ollama_model
HOST = settings.ollama_host
FMT = extraction_json_schema()
CONC = 4


def _sample() -> list[dict]:
    sd = settings.staging_dir
    recs: list[dict] = []
    hf = sd / "hf_jobposting" / "postings.parquet"
    if hf.exists():
        df = pd.read_parquet(hf)
        de = df[df["country_code"] == "DE"].head(60)
        en = df[df["country_code"] == "US"].head(40)
        for _, r in pd.concat([de, en]).iterrows():
            recs.append({"title": r["title"], "description": r["description"],
                         "country_code": r["country_code"], "src": "hf"})
    for name in ("smartrecruiters", "personio"):
        p = sd / name / "postings.parquet"
        if p.exists():
            for _, r in pd.read_parquet(p).iterrows():
                recs.append({"title": r["title"], "description": r["description"],
                             "country_code": r["country_code"], "src": name})
    return recs


def _extract_one(rec: dict) -> dict:
    msgs = build_messages(rec["title"], (rec["description"] or "")[:6000])
    payload = {"model": MODEL, "messages": msgs, "stream": False, "think": False,
               "format": FMT, "options": {"temperature": 0, "num_ctx": 8192}}
    req = urllib.request.Request(HOST.rstrip("/") + "/api/chat",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            out = json.loads(r.read()).get("message", {}).get("content", "") or ""
        parsed = json.loads(out)
    except Exception as e:  # noqa: BLE001
        parsed = {"_error": str(e)[:80]}
    parsed["_secs"] = round(time.time() - t, 2)
    parsed.update(rec)   # keep title/description/country/src for validation
    return parsed


def main() -> None:
    recs = _sample()
    print(f"[valgate] sample: {len(recs)} postings", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        extracted = list(ex.map(_extract_one, recs))
    wall = time.time() - t0
    ok = [r for r in extracted if "_error" not in r]
    per = [r["_secs"] for r in ok]
    n = len(ok)

    # ---- multilingual role adjudicator (gpt-oss:20b reads German natively) ----
    JUDGE = settings.ollama_judge_model

    def adjudicate(rec) -> bool | None:
        ex = rec.get("disambiguated_role") or rec.get("role")
        if not ex:
            return None
        prompt = (
            "You are checking whether an extracted ROLE label correctly names the primary "
            "role of a job posting. Read the posting (it may be in German) and answer.\n\n"
            f"POSTING TITLE: {rec.get('title', '')}\n"
            f"POSTING BODY (may be truncated):\n{(rec.get('description') or '')[:1500]}\n\n"
            f"EXTRACTED ROLE: {ex}\n\n"
            "Is EXTRACTED ROLE a correct primary role for this posting? Reply ONLY the word "
            "yes or no.")
        payload = {"model": JUDGE, "messages": [{"role": "user", "content": prompt}],
                   "stream": False, "think": False, "options": {"temperature": 0, "num_ctx": 8192}}
        try:
            req = urllib.request.Request(HOST.rstrip("/") + "/api/chat",
                                         data=json.dumps(payload).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                out = json.loads(r.read()).get("message", {}).get("content", "") or ""
            return out.strip().lower().startswith("y")
        except Exception:  # noqa: BLE001
            return None

    def role_id(rec):
        ex = rec.get("disambiguated_role") or rec.get("role")
        return match_title_to_role(str(ex)) if ex else None

    def truth_en(rec):
        return match_title_to_role(rec["title"]) if rec.get("title") else None

    # English baseline: title resolver (its native yardstick)
    buckets = {"en": {"tot": 0, "hit": 0}}
    for r in ok:
        lang = str(r.get("language") or "").lower()[:2]
        if r.get("country_code") == "US" or lang == "en":
            t = truth_en(r)
            ex = role_id(r)
            if t:
                buckets["en"]["tot"] += 1
                buckets["en"]["hit"] += 1 if ex == t else 0

    # German (+ English calibration slice): the multilingual adjudicator
    de_rows = [r for r in ok if r.get("country_code") == "DE"
               or str(r.get("language") or "").lower()[:2] == "de"]
    en_calib = [r for r in ok if r not in de_rows][:20]
    de_adj = {"tot": 0, "hit": 0}
    for r in de_rows:
        v = adjudicate(r)
        if v is not None:
            de_adj["tot"] += 1
            de_adj["hit"] += 1 if v else 0
    en_adj = {"tot": 0, "hit": 0}
    for r in en_calib:
        v = adjudicate(r)
        if v is not None:
            en_adj["tot"] += 1
            en_adj["hit"] += 1 if v else 0

    def acc(b):
        return round(b["hit"] / b["tot"], 3) if b["tot"] else None

    # abstain / non-abstain
    abst = sum(1 for r in ok if r.get("abstain"))
    result = {
        "sample_n": len(recs), "extracted_ok": n, "errors": len(extracted) - n,
        "throughput": {
            "wall_s": round(wall, 1), "concurrency": CONC,
            "mean_s_per_posting_single_stream": round(sum(per) / n, 2) if n else None,
            "effective_s_per_posting_at_conc": round(wall / n, 2) if n else None,
            "postings_per_hour_at_conc": int(3600 / (wall / n)) if n else None,
        },
        "abstain_rate": round(abst / n, 3) if n else None,
        "role_agreement": {
            "english_title_resolver": {"acc": acc(buckets["en"]), "n": buckets["en"]["tot"],
                                       "note": "EN baseline vs match_title_to_role"},
            "german_multilingual_adjudicator": {"acc": acc(de_adj), "n": de_adj["tot"],
                                                "note": "the trustworthy German number (gpt-oss:20b)"},
            "english_adjudicator_calibration": {"acc": acc(en_adj), "n": en_adj["tot"],
                                                "note": "adjudicator sanity-check vs the EN resolver"},
        },
        "sample_roles": [(r.get("country_code"), str(r.get("title"))[:34],
                          r.get("disambiguated_role"), r.get("language"))
                         for r in ok[:12]],
    }
    Path("reports").mkdir(exist_ok=True)
    Path("reports/valgate_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False),
                                                   encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
