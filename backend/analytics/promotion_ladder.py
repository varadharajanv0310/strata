"""H-1B within-employer promotion ladder — the dollar value of a promotion.

The DOL LCA disclosure files we already hold in staging carry, per certified
filing, the **employer name**, **job title**, **offered wage**, and the
**prevailing-wage level (I–IV)** — a legally-attested seniority tier. Group by
``(employer, title-family)``, rank the rungs by wage level, and the median wage
*step* between adjacent rungs is the single most valuable career fact a comp site
can show, hiding in data we already collected:

    Google · "software engineer"
      I  (entry)        $128k   n=210
      II (qualified)    $159k   n=540   ▲ +$31k (+24%)
      III(experienced)  $196k   n=380   ▲ +$37k (+23%)
      IV (senior)       $241k   n=120   ▲ +$45k (+23%)

Pure derivation on cached files — NO download, NO ingestion run. Reuses the H-1B
connector's wage-annualization + level mapping. Reads only LCA xlsx already in
``staging/h1b``; if none are cached it returns an empty result (logged), never
fetches.
"""
from __future__ import annotations

import re
import statistics
import time
from collections import defaultdict

from backend.core.logging import get_logger
from backend.ingest.h1b import (
    _SNAPSHOTS,
    _annualize,
    _file_path,
    _pick,
    WAGE_HI,
    WAGE_LO,
)
from backend.warehouse.taxonomy import normalize_surface

log = get_logger("analytics.promotion_ladder")

# LCA columns we need (beyond what h1b.py picks)
_C_EMPLOYER = ["EMPLOYER_NAME", "EMPLOYER_NAME_1"]
_C_TITLE = ["JOB_TITLE", "JOB_TITLE_1"]
_C_STATUS = ["CASE_STATUS"]
_C_WAGE_FROM = ["WAGE_RATE_OF_PAY_FROM", "WAGE_RATE_OF_PAY_FROM_1", "WAGE_RATE_OF_PAY"]
_C_UNIT = ["WAGE_UNIT_OF_PAY", "WAGE_UNIT_OF_PAY_1", "PW_UNIT_OF_PAY"]
_C_LEVEL = ["PW_WAGE_LEVEL", "PW_WAGE_LEVEL_1", "WAGE_LEVEL"]

_LEVEL_RANK = {"i": 1, "1": 1, "level i": 1, "ii": 2, "2": 2, "level ii": 2,
               "iii": 3, "3": 3, "level iii": 3, "iv": 4, "4": 4, "level iv": 4}
_LEVEL_LABEL = {1: "I (entry)", 2: "II (qualified)", 3: "III (experienced)", 4: "IV (senior)"}

_EMP_SUFFIX = re.compile(r"\b(inc|incorporated|llc|l l c|corp|corporation|ltd|limited|"
                         r"co|company|plc|llp|lp|gmbh|pvt|private)\b\.?")


def normalize_employer(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9 &]+", " ", s)
    s = _EMP_SUFFIX.sub(" ", s)
    return " ".join(s.split())


def _cached_files() -> list[tuple[str, str]]:
    """(fy, q) pairs whose xlsx is actually cached in staging — newest first."""
    have = [(fy, q) for (fy, q) in _SNAPSHOTS if _file_path(fy, q).exists()]
    return sorted(have, reverse=True)


def build_ladders(
    files: list[tuple[str, str]] | None = None,
    *,
    max_rows: int | None = 600_000,
    min_rung_n: int = 4,
    min_levels: int = 2,
    time_cap_s: float | None = 180.0,
) -> dict:
    """Parse cached LCA xlsx → within-employer wage ladders.

    Returns {"ladders": [...], "files": [...], "n_filings": int}. Each ladder:
    {employer, title_family, rungs:[{level,label,median,n}], steps:[{from,to,abs,pct}]}.
    """
    import openpyxl  # heavy; only on demand

    files = files or _cached_files()
    if not files:
        log.warning("no cached LCA xlsx in staging/h1b — run the H-1B connector first")
        return {"ladders": [], "files": [], "n_filings": 0}

    # (employer_norm, title_family, level_rank) -> [annualized wages]
    buckets: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    emp_display: dict[str, str] = {}
    n_filings = 0
    t0 = time.time()

    for fy, q in files:
        p = _file_path(fy, q)
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        if header is None:
            wb.close()
            continue
        hmap = {str(h).strip().upper(): i for i, h in enumerate(header) if h is not None}
        i_emp = _pick(hmap, _C_EMPLOYER)
        i_title = _pick(hmap, _C_TITLE)
        i_status = _pick(hmap, _C_STATUS)
        i_from = _pick(hmap, _C_WAGE_FROM)
        i_unit = _pick(hmap, _C_UNIT)
        i_level = _pick(hmap, _C_LEVEL)
        if None in (i_emp, i_title, i_from, i_unit, i_level):
            log.error("%s %s: missing cols (emp=%s title=%s from=%s unit=%s level=%s)",
                      fy, q, i_emp, i_title, i_from, i_unit, i_level)
            wb.close()
            continue

        seen = 0
        for row in it:
            seen += 1
            if max_rows is not None and seen > max_rows:
                break
            if seen % 50_000 == 0:
                el = time.time() - t0
                print(f"[ladder] {fy} {q}: scanned {seen:,} kept {n_filings:,} "
                      f"employers={len(emp_display)} elapsed {el:.0f}s", flush=True)
                if time_cap_s and el > time_cap_s:
                    print(f"[ladder] time cap {time_cap_s}s — partial", flush=True)
                    break
            if i_status is not None and not str(row[i_status] or "").strip().lower().startswith("certified"):
                continue
            rank = _LEVEL_RANK.get(str(row[i_level] or "").strip().lower())
            if not rank:
                continue
            wage = _annualize(row[i_from], row[i_unit])
            if wage is None or not (WAGE_LO <= wage <= WAGE_HI):
                continue
            fam = normalize_surface(str(row[i_title] or ""))
            if not fam:
                continue
            emp_norm = normalize_employer(str(row[i_emp] or ""))
            if not emp_norm:
                continue
            emp_display.setdefault(emp_norm, str(row[i_emp]).strip())
            buckets[(emp_norm, fam, rank)].append(wage)
            n_filings += 1
        wb.close()
        if time_cap_s and (time.time() - t0) > time_cap_s:
            break

    # assemble ladders: an (employer, family) with >= min_levels populated rungs
    grouped: dict[tuple[str, str], dict[int, list[float]]] = defaultdict(dict)
    for (emp, fam, rank), vals in buckets.items():
        if len(vals) >= min_rung_n:
            grouped[(emp, fam)][rank] = vals

    ladders = []
    for (emp, fam), rungs in grouped.items():
        if len(rungs) < min_levels:
            continue
        ordered = sorted(rungs.items())
        rung_rows = [{"level": r, "label": _LEVEL_LABEL[r],
                      "median": round(statistics.median(v)), "n": len(v)}
                     for r, v in ordered]
        steps = []
        for a, b in zip(rung_rows, rung_rows[1:]):
            d = b["median"] - a["median"]
            steps.append({"from": a["label"], "to": b["label"], "abs": d,
                          "pct": round(100 * d / a["median"], 1) if a["median"] else 0.0})
        ladders.append({
            "employer": emp_display.get(emp, emp),
            "title_family": fam,
            "n_filings": sum(r["n"] for r in rung_rows),
            "rungs": rung_rows,
            "steps": steps,
            "total_climb_pct": round(100 * (rung_rows[-1]["median"] - rung_rows[0]["median"])
                                     / rung_rows[0]["median"], 1) if rung_rows[0]["median"] else 0.0,
        })
    ladders.sort(key=lambda L: L["n_filings"], reverse=True)
    log.info("promotion ladders: %d filings → %d ladders (>=%d levels) from %s",
             n_filings, len(ladders), min_levels, files)
    return {"ladders": ladders, "files": files, "n_filings": n_filings}


def sample(top: int = 8, **kw) -> dict:
    """Build ladders and pretty-print the top-N for a quick proof."""
    res = build_ladders(**kw)
    print(f"\n=== H-1B WITHIN-EMPLOYER PROMOTION LADDERS "
          f"({res['n_filings']:,} filings → {len(res['ladders'])} ladders) ===")
    for L in res["ladders"][:top]:
        print(f"\n{L['employer']} · \"{L['title_family']}\"  "
              f"(n={L['n_filings']}, climb +{L['total_climb_pct']}%)")
        for r in L["rungs"]:
            step = ""
            for s in L["steps"]:
                if s["to"] == r["label"]:
                    step = f"   ▲ +${s['abs']:,} (+{s['pct']}%)"
            print(f"   {r['label']:18} ${r['median']:>8,}  n={r['n']:<5}{step}")
    return res


if __name__ == "__main__":  # pragma: no cover
    sample()
