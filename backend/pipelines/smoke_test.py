"""Smoke test — run every connector BOUNDED + FAST to find what breaks before a real run.

This is the prior council's "smoke run" (B1): the highest-information pre-run action. It does
NOT land real data or populate anything — the caller points DATA_DIR / DUCKDB_PATH / DATABASE_URL
at a TEMP dir so nothing touches the persistent warehouse / app.db / staging. Each connector is
run in a subprocess with a tiny bound (where it accepts one) and a hard timeout; we classify from
its summary dict, its landed staging, and its stderr (heartbeats = endpoint reached vs an error):

  ok        — completed + landed rows in the expected shape (endpoint live, schema matches)
  empty     — completed cleanly but landed 0 rows (soft-failed / genuinely nothing in the sample)
  auth      — skipped for missing credentials / config (not broken; needs a key/url)
  reachable — timed out mid-fetch but printed progress (endpoint live; needs a real bounded mode)
  heavy     — a large bulk download we don't pull in smoke (reachability inferred, not tested)
  broken    — raised / 404 / parse error / dead endpoint (the real broken-list)
  cached    — cached-data stage (not network); green iff its cache file exists

    python -m backend.pipelines.smoke_test            # all
    python -m backend.pipelines.smoke_test ilostat    # one
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# name -> (python expr returning a summary dict, timeout_s, kind, loader-expr-or-None)
# kind: net | heavy | cached | auth.  loader returns the landed row count for an honest shape read.
SMOKE: dict[str, tuple[str, int, str, str | None]] = {
    # ---- council network connectors (the unverified ones the smoke test is FOR) ----
    "ilostat":             ("from backend.ingest.ilostat import run; print(__import__('json').dumps(run(time_cap_s=40)))", 70, "net", "from backend.ingest.ilostat import load_earnings as L"),
    "gov_projections":     ("from backend.ingest.gov_projections import run; print(__import__('json').dumps(run()))", 110, "net", "from backend.ingest.gov_projections import load_outlook as L"),
    "package_registries":  ("from backend.ingest.package_registries import run; print(__import__('json').dumps(run()))", 70, "net", "from backend.ingest.package_registries import load_adoption as L"),
    "arxiv":               ("from backend.ingest.arxiv import run; print(__import__('json').dumps(run(time_cap_s=40)))", 70, "net", "from backend.ingest.arxiv import load_velocity as L"),
    "huggingface":         ("from backend.ingest.huggingface import run; print(__import__('json').dumps(run(max_pages=1)))", 60, "net", "from backend.ingest.huggingface import load_velocity as L"),
    "wikipedia_pageviews": ("from backend.ingest.wikipedia_pageviews import run; print(__import__('json').dumps(run()))", 95, "net", "from backend.ingest.wikipedia_pageviews import load_pageviews as L"),
    "eures":               ("from backend.ingest.eures import run; print(__import__('json').dumps(run()))", 50, "net", "from backend.ingest.eures import load_vacancies as L"),
    "bundesagentur":       ("from backend.ingest.bundesagentur import run; print(__import__('json').dumps(run(time_cap_s=30)))", 55, "net", "from backend.ingest.bundesagentur import load_vacancies as L"),
    "mycareersfuture":     ("from backend.ingest.mycareersfuture import run; print(__import__('json').dumps(run(max_pages=1)))", 55, "net", "from backend.ingest.mycareersfuture import load_postings as L"),
    "usajobs":             ("from backend.ingest.usajobs import run; print(__import__('json').dumps(run(max_pages=1)))", 50, "auth", "from backend.ingest.usajobs import load_postings as L"),
    "cedefop_ovate":       ("from backend.ingest.cedefop_ovate import run; print(__import__('json').dumps(run()))", 45, "auth", "from backend.ingest.cedefop_ovate import load_skill_demand as L"),
    "hn_hiring":           ("from backend.ingest.hn_hiring import run; print(__import__('json').dumps(run(max_threads=1)))", 65, "net", "from backend.ingest.hn_hiring import load_postings as L"),
    "remoteok":            ("from backend.ingest.remoteok import run; print(__import__('json').dumps(run()))", 40, "net", "from backend.ingest.remoteok import load_postings as L"),
    "wikidata_occupations":("from backend.ingest.wikidata_occupations import run; print(__import__('json').dumps(run()))", 95, "net", "from backend.ingest.wikidata_occupations import load_occupations as L"),
    # ---- earlier connectors (mixed network) ----
    "google_trends":       ("from backend.ingest.google_trends import run; print(__import__('json').dumps(run(max_units=2)))", 70, "net", "from backend.ingest.google_trends import load_interest as L"),
    "baselines":           ("from backend.ingest.baselines import run; print(__import__('json').dumps(run()))", 70, "net", "from backend.ingest.baselines import load_all as L"),
    "worldbank_ppp":       ("from backend.ingest.worldbank_ppp import fetch_ppp; print(__import__('json').dumps({'ppp': len(fetch_ppp(force=True))}))", 45, "net", "from backend.ingest.worldbank_ppp import load_ppp as L"),
    # ---- heavy bulk downloads — NOT pulled in smoke (GB-scale); reachability noted, not fetched ----
    "stack_exchange":      ("", 0, "heavy", None),
    "gh_archive":          ("", 0, "heavy", None),
    "common_crawl":        ("", 0, "heavy", None),
    # ---- cached-data stages (not network) — green iff the cache exists ----
    "so_survey":           ("", 0, "cached", "from backend.ingest.so_survey import load_agg as L"),
    "h1b":                 ("", 0, "cached", "from backend.ingest.h1b import load_agg as L"),
    "onet_trajectory":     ("", 0, "cached", "from backend.warehouse.onet_trajectory import load_adjacency as L"),
}


def _row_count(loader_expr: str | None) -> int | str:
    if not loader_expr:
        return "?"
    code = f"{loader_expr}\nimport json,sys\ntry:\n d=L()\n print(len(d) if d is not None else 0)\nexcept Exception as e:\n print('loaderr:'+str(e)[:60])"
    try:
        p = subprocess.run([PY, "-c", code], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=40, env={**os.environ})
        out = (p.stdout or "").strip().splitlines()
        return out[-1] if out else "?"
    except Exception as e:  # noqa: BLE001
        return f"loaderr:{e}"


def _classify(name: str, expr: str, timeout: int, kind: str, loader: str | None) -> dict:
    if kind == "heavy":
        return {"name": name, "status": "heavy", "rows": "—",
                "note": "GB-scale bulk download — not pulled in smoke; reachability inferred from the run stage"}
    if kind == "cached":
        rc = _row_count(loader)
        ok = isinstance(rc, str) and rc.isdigit() and int(rc) > 0
        return {"name": name, "status": "cached-ok" if ok else "cached-empty", "rows": rc,
                "note": "cached-data stage (not network); cache present" if ok else "no cache in this env (verified in prior real runs)"}

    t0 = time.time()
    timed_out = False
    out = err = ""
    rc = -1
    try:
        p = subprocess.run([PY, "-c", expr], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env={**os.environ})
        rc, out, err = p.returncode, (p.stdout or ""), (p.stderr or "")
    except subprocess.TimeoutExpired as e:
        timed_out = True
        def _dec(x):
            return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")
        out, err = _dec(e.stdout), _dec(e.stderr)
    dt = int(time.time() - t0)
    rows = _row_count(loader)
    low = (err + out).lower()

    # classify
    if timed_out:
        progressed = any(k in low for k in ("fetch", "land", "heartbeat", "[", "page", "country", "downloading"))
        status, note = ("reachable", "timed out mid-fetch but printed progress — endpoint live, needs a real bounded mode") \
            if progressed else ("broken", f"timeout {timeout}s with no progress — endpoint slow/dead")
    elif rc != 0:
        last = (err.strip().splitlines() or ["nonzero exit"])[-1][:160]
        status, note = "broken", f"exit {rc}: {last}"
    elif isinstance(rows, str) and rows.isdigit() and int(rows) > 0:
        # landed data wins over any auth-ish log line (a partial/throttled fetch still proves the endpoint)
        status, note = "ok", f"landed {rows} rows in {dt}s"
    elif any(k in low for k in ("missing credential", "no api key", "api_key", "no usajobs", "url absent", "no cedefop", "not configured", "graceful")):
        status, note = "auth", "skipped — missing credentials/config (graceful)"
    elif isinstance(rows, str) and rows == "0":
        # completed but nothing landed — look for a soft-fail signal
        soft = [ln for ln in err.splitlines() if any(k in ln.lower() for k in ("warn", "fail", "404", "error", "skip", "empty", "no "))]
        status, note = "empty", (soft[-1].strip()[:150] if soft else f"ran clean in {dt}s but landed 0 rows")
    else:
        status, note = "empty", f"completed in {dt}s; rows={rows}; tail={(err.strip().splitlines() or [''])[-1][:120]}"
    return {"name": name, "status": status, "rows": rows, "note": note}


def main(only: list[str] | None = None) -> None:
    names = only or list(SMOKE)
    targets = {n: SMOKE[n] for n in names if n in SMOKE}
    print(f"smoke-testing {len(targets)} connectors (isolated env: DATA_DIR={os.environ.get('DATA_DIR','<default>')})\n", flush=True)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_classify, n, *cfg): n for n, cfg in targets.items()}
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            print(f"  [{r['status']:>11}] {r['name']:<22} rows={str(r['rows']):<6} {r['note']}", flush=True)

    order = {"broken": 0, "auth": 1, "reachable": 2, "empty": 3, "heavy": 4, "cached-empty": 5, "cached-ok": 6, "ok": 7}
    results.sort(key=lambda r: (order.get(r["status"], 9), r["name"]))
    by = {}
    for r in results:
        by.setdefault(r["status"], []).append(r["name"])
    print("\n=== SMOKE SUMMARY ===")
    for s in ("ok", "cached-ok", "heavy", "auth", "reachable", "empty", "broken", "cached-empty"):
        if by.get(s):
            print(f"  {s:>11} ({len(by[s])}): {', '.join(by[s])}")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
