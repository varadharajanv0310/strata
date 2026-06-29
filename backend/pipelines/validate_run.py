"""Run-GATE harness — sits between the warehouse fuse and ``publish_served`` and
PHYSICALLY blocks a bad run from publishing.

strata is FREE / PUBLIC / HONEST / ROLES-ONLY. A published number that is thin,
out-of-band, schema-broken, or un-suppressed is worse than no number at all — it
launders noise into apparent authority. This module is the tripwire: it reads the
analytical warehouse READ-ONLY (``db.duckdb_connect(read_only=True)`` — NEVER the
app/serving DB) and runs five families of checks, each a hard pass/fail:

  (a) ROW-COUNT FLOORS    — every fact / bridge must clear a ``min_expected`` floor;
                            below floor => FAIL 'suspect/thin'.
  (b) SANITY BOUNDS       — per (country, lens) annualized median must sit inside a
                            plausible band anchored to the World Bank PPP / ILOSTAT
                            values ALREADY in the warehouse (dim_ppp / dim_country),
                            ~0.2x–5x of the anchor; outliers are flagged.
  (c) SCHEMA CONTRACT     — required columns present + non-null where required.
  (d) MIN-SAMPLE / SUPPRESSION — realized-lens cells below the sample floor MUST be
                            absent (nulled) downstream; assert suppression happened.
  (e) DISCLOSURE + VOLUME — salary-disclosure rate and per-role volume floor confirmed.

Emits ``validation_report.json`` (next to the warehouse), prints a readable summary,
and RETURNS AN EXIT CODE (0 = clean, non-zero = at least one breach). ``publish`` can
``from backend.pipelines.validate_run import validate_run`` and refuse on non-zero.

Design constraints (build pass):
  * import-clean — only stdlib + the project's own ``core`` (duckdb pulled in via
    ``duckdb_connect``). NO heavy ML / GPU deps; nothing imported here needs vLLM /
    torch. So this module imports on a machine without the GPU stack.
  * GRACEFUL — a missing table is a reported finding, never a crash. A warehouse that
    has not been built yet degrades to a single clear FAIL, not a traceback.
  * ROLES-ONLY — no company/employer axis is ever inspected or required.
  * idempotent + side-effect-light — read-only on the warehouse; only writes the JSON
    report. Safe to run repeatedly.
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("pipelines.validate_run")

# --------------------------------------------------------------------------- #
# Tunables — deliberately conservative floors. These are RUN-GATE floors (a full
# scale run), NOT the small-sample proof floors. A real publish that lands fewer
# rows than these is, by policy, too thin to call "the market".
# --------------------------------------------------------------------------- #

# (a) per-fact / per-bridge row-count floors. A table below its floor => 'suspect/thin'.
#     0 = "table must exist and be readable, but emptiness alone is not a breach"
#     (used for genuinely optional lenses that a given run may legitimately skip).
MIN_EXPECTED: dict[str, int] = {
    # core salary lenses — three lenses, never blended
    "fact_salary_job": 200,        # advertised (job-level) — the spine
    "fact_salary_person": 0,       # realized (SO/H-1B) — optional, sparse by nature
    "fact_salary_official": 0,     # official anchor (ILOSTAT/OEWS) — optional per run
    # demand / outlook
    "fact_demand": 200,
    "fact_role_outlook": 0,        # forward outlook — optional (not every run refreshes)
    # skills
    "fact_skill_adoption": 0,      # mostly global, country-invariant — optional
    # bridges (role attributes — roles-only)
    "bridge_role_skill": 50,
    "bridge_role_ladder": 0,
    "bridge_role_adjacency": 0,
    "bridge_role_skill_importance": 0,
}

# (b) sanity band: annualized median must lie within [LOW x anchor, HIGH x anchor].
#     Anchor = a PPP-scaled plausible national tech-salary level derived from the
#     World-Bank PPP / national-factor reference ALREADY in the warehouse.
SANITY_BAND_LOW = 0.2
SANITY_BAND_HIGH = 5.0
# US-anchored plausible *annual* tech-median (international $, PPP) — the single
# global anchor we scale per country by that country's nat_factor. Intentionally a
# wide reference midpoint; the band (0.2x–5x) is what does the gating, not this.
GLOBAL_ANCHOR_USD = 95_000.0

# the lenses that carry an annualized median we sanity-check
SALARY_LENSES = {
    "fact_salary_job": "advertised",
    "fact_salary_person": "realized",
    "fact_salary_official": "official",
}

# (d) min-sample suppression floors — MUST match marts.materialize so the gate and the
#     publisher agree on what "too thin to show" means.
MIN_SAMPLE_REALIZED = 30
MIN_SAMPLE_ADVERTISED = 10

# (e) disclosure + volume floors
ROLE_VOLUME_FLOOR = settings.role_volume_floor          # postings per role to be "real"
MIN_DISCLOSURE_RATE = 0.05                               # >=5% of cells must carry transparency

# (c) schema contract — required columns + which must be non-null. Only columns that,
#     if missing/null, would silently corrupt the served artifact are listed here.
SCHEMA_CONTRACT: dict[str, dict[str, list[str]]] = {
    "fact_salary_job": {
        "required": ["role_id", "country_code", "year", "median", "currency_code", "source_id"],
        "non_null": ["role_id", "country_code", "year", "median", "currency_code"],
    },
    "fact_salary_person": {
        "required": ["role_id", "country_code", "year", "median", "currency_code", "sample_size"],
        "non_null": ["role_id", "country_code", "year", "median"],
    },
    "fact_salary_official": {
        "required": ["role_id", "country_code", "year", "median", "currency_code", "source_id"],
        "non_null": ["role_id", "country_code", "year", "median", "source_id"],
    },
    "fact_demand": {
        "required": ["role_id", "country_code", "year", "demand_index"],
        "non_null": ["role_id", "country_code", "year", "demand_index"],
    },
    "fact_role_outlook": {
        "required": ["role_id", "country_code", "horizon_years", "source_id"],
        "non_null": ["role_id", "horizon_years", "source_id"],
    },
    "fact_skill_adoption": {
        "required": ["skill_id", "period", "metric", "value", "ecosystem"],
        "non_null": ["skill_id", "metric", "value"],
    },
    "bridge_role_skill": {
        "required": ["role_id", "skill_id"],
        "non_null": ["role_id", "skill_id"],
    },
}


# --------------------------------------------------------------------------- #
# Result model
# --------------------------------------------------------------------------- #
@dataclass
class Check:
    name: str           # stable id, e.g. "rowfloor.fact_salary_job"
    category: str       # rowfloor | sanity | schema | suppression | disclosure
    status: str         # pass | fail | skip
    detail: str         # human-readable one-liner
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass
class Report:
    ok: bool = True
    warehouse: str = ""
    elapsed_s: float = 0.0
    checks: list[Check] = field(default_factory=list)

    def add(self, c: Check) -> None:
        self.checks.append(c)
        if c.failed:
            self.ok = False

    def to_dict(self) -> dict:
        fails = [c.name for c in self.checks if c.status == "fail"]
        skips = [c.name for c in self.checks if c.status == "skip"]
        return {
            "ok": self.ok,
            "warehouse": self.warehouse,
            "elapsed_s": round(self.elapsed_s, 2),
            "summary": {
                "total": len(self.checks),
                "passed": sum(c.status == "pass" for c in self.checks),
                "failed": len(fails),
                "skipped": len(skips),
                "failures": fails,
                "skips": skips,
            },
            "checks": [asdict(c) for c in self.checks],
        }


# --------------------------------------------------------------------------- #
# Warehouse helpers — all defensive: any failure becomes a finding, not a crash.
# --------------------------------------------------------------------------- #
def _existing_tables(con) -> set[str]:
    try:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        return {r[0] for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("could not enumerate tables: %s", e)
        return set()


def _columns(con, table: str) -> set[str]:
    try:
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=?",
            [table],
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:  # noqa: BLE001
        return set()


def _count(con, table: str) -> Optional[int]:
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception as e:  # noqa: BLE001
        log.warning("count(%s) failed: %s", table, e)
        return None


def _ppp_anchor(con) -> dict[str, float]:
    """Per-country plausible annual tech-median anchor (international $), derived from
    the World-Bank PPP / national-factor reference ALREADY in the warehouse.

    Prefers ``dim_country.nat_factor`` (salary scale vs US). Falls back to deriving a
    scale from ``dim_ppp.ppp_factor`` if nat_factor is absent. Returns {} if neither
    dim is present — callers then SKIP the band check (graceful, not a crash).
    """
    anchors: dict[str, float] = {}
    tables = _existing_tables(con)
    if "dim_country" in tables:
        try:
            rows = con.execute(
                "SELECT code, nat_factor FROM dim_country WHERE nat_factor IS NOT NULL"
            ).fetchall()
            for code, nf in rows:
                if nf and nf > 0:
                    anchors[code] = GLOBAL_ANCHOR_USD * float(nf)
        except Exception as e:  # noqa: BLE001
            log.warning("dim_country anchor read failed: %s", e)
    if anchors:
        return anchors
    # fallback: derive a crude scale from PPP factor (US ppp ~ 1.0)
    if "dim_ppp" in tables:
        try:
            rows = con.execute(
                "SELECT country_code, AVG(ppp_factor) FROM dim_ppp "
                "WHERE ppp_factor IS NOT NULL GROUP BY country_code"
            ).fetchall()
            for code, ppp in rows:
                if ppp and ppp > 0:
                    # cheaper-PPP economies tend to lower nominal medians; this is a
                    # loose proxy only — the wide band is what actually gates.
                    anchors[code] = GLOBAL_ANCHOR_USD / float(ppp)
        except Exception as e:  # noqa: BLE001
            log.warning("dim_ppp anchor read failed: %s", e)
    return anchors


# --------------------------------------------------------------------------- #
# Check families
# --------------------------------------------------------------------------- #
def _check_row_floors(con, tables: set[str], rep: Report) -> None:
    for table, floor in MIN_EXPECTED.items():
        name = f"rowfloor.{table}"
        if table not in tables:
            # genuinely-optional tables (floor 0) absent => skip; required => fail.
            if floor <= 0:
                rep.add(Check(name, "rowfloor", "skip",
                              f"{table} absent (optional, floor 0) — skipped"))
            else:
                rep.add(Check(name, "rowfloor", "fail",
                              f"{table} ABSENT but floor is {floor} — suspect/thin run"))
            continue
        n = _count(con, table)
        if n is None:
            rep.add(Check(name, "rowfloor", "fail",
                          f"{table} present but unreadable", {"floor": floor}))
            continue
        if n < floor:
            rep.add(Check(name, "rowfloor", "fail",
                          f"{table} has {n} rows < floor {floor} — suspect/thin",
                          {"rows": n, "floor": floor}))
        else:
            rep.add(Check(name, "rowfloor", "pass",
                          f"{table}: {n} rows >= floor {floor}", {"rows": n, "floor": floor}))


def _check_schema(con, tables: set[str], rep: Report) -> None:
    for table, contract in SCHEMA_CONTRACT.items():
        name = f"schema.{table}"
        if table not in tables:
            rep.add(Check(name, "schema", "skip", f"{table} absent — schema check skipped"))
            continue
        cols = _columns(con, table)
        missing = [c for c in contract["required"] if c not in cols]
        if missing:
            rep.add(Check(name, "schema", "fail",
                          f"{table} missing required columns: {missing}",
                          {"missing": missing}))
            continue
        # non-null contract: count NULLs in each must-be-present column
        null_offenders: dict[str, int] = {}
        for col in contract["non_null"]:
            if col not in cols:
                continue
            try:
                nulls = int(con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL"
                ).fetchone()[0])
            except Exception as e:  # noqa: BLE001
                log.warning("null-scan %s.%s failed: %s", table, col, e)
                continue
            if nulls > 0:
                null_offenders[col] = nulls
        if null_offenders:
            rep.add(Check(name, "schema", "fail",
                          f"{table} has NULLs in non-null columns: {null_offenders}",
                          {"null_offenders": null_offenders}))
        else:
            rep.add(Check(name, "schema", "pass",
                          f"{table}: required cols present, non-null contract held"))


def _check_sanity_bounds(con, tables: set[str], rep: Report) -> None:
    anchors = _ppp_anchor(con)
    if not anchors:
        rep.add(Check("sanity.anchor", "sanity", "skip",
                      "no dim_country/dim_ppp anchor available — band check skipped"))
        return
    for table, lens in SALARY_LENSES.items():
        name = f"sanity.{table}"
        if table not in tables:
            rep.add(Check(name, "sanity", "skip", f"{table} absent — band check skipped"))
            continue
        cols = _columns(con, table)
        if not {"country_code", "median"} <= cols:
            rep.add(Check(name, "sanity", "skip",
                          f"{table} lacks country_code/median — band check skipped"))
            continue
        try:
            rows = con.execute(
                f"SELECT country_code, MEDIAN(median) AS m, COUNT(*) AS n "
                f"FROM {table} WHERE median IS NOT NULL AND median > 0 "
                f"GROUP BY country_code"
            ).fetchall()
        except Exception as e:  # noqa: BLE001
            rep.add(Check(name, "sanity", "fail",
                          f"{table} median aggregation failed: {e}"))
            continue
        outliers: list[dict] = []
        checked = 0
        for code, med, n in rows:
            anchor = anchors.get(code)
            if not anchor or med is None:
                continue
            checked += 1
            lo, hi = SANITY_BAND_LOW * anchor, SANITY_BAND_HIGH * anchor
            if not (lo <= float(med) <= hi):
                outliers.append({
                    "country": code, "lens": lens, "median": round(float(med), 1),
                    "anchor": round(anchor, 1), "band": [round(lo, 1), round(hi, 1)],
                    "n": int(n),
                })
        if outliers:
            rep.add(Check(name, "sanity", "fail",
                          f"{table}: {len(outliers)} (country,lens) median(s) out of "
                          f"[{SANITY_BAND_LOW}x,{SANITY_BAND_HIGH}x] PPP band",
                          {"outliers": outliers}))
        elif checked == 0:
            rep.add(Check(name, "sanity", "skip",
                          f"{table}: no country matched an anchor — band check skipped"))
        else:
            rep.add(Check(name, "sanity", "pass",
                          f"{table}: all {checked} country medians within PPP band"))


def _check_suppression(con, tables: set[str], rep: Report) -> None:
    """(d) min-sample: realized cells below the floor MUST be absent from what would
    publish. We assert the warehouse does not retain a below-floor realized median as
    a usable (non-null) cell. If the table is absent, skip (nothing to publish).
    """
    name = "suppression.fact_salary_person"
    table = "fact_salary_person"
    if table not in tables:
        rep.add(Check(name, "suppression", "skip",
                      "fact_salary_person absent — nothing to suppress"))
        return
    cols = _columns(con, table)
    if not {"median", "sample_size"} <= cols:
        rep.add(Check(name, "suppression", "skip",
                      "fact_salary_person lacks median/sample_size — check skipped"))
        return
    try:
        leak = int(con.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE median IS NOT NULL AND sample_size IS NOT NULL "
            f"AND sample_size < {MIN_SAMPLE_REALIZED}"
        ).fetchone()[0])
    except Exception as e:  # noqa: BLE001
        rep.add(Check(name, "suppression", "fail",
                      f"suppression scan failed: {e}"))
        return
    if leak > 0:
        rep.add(Check(name, "suppression", "fail",
                      f"{leak} realized cell(s) below sample floor {MIN_SAMPLE_REALIZED} "
                      f"still carry a non-null median — suppression DID NOT happen",
                      {"leaked_cells": leak, "floor": MIN_SAMPLE_REALIZED}))
    else:
        rep.add(Check(name, "suppression", "pass",
                      f"no realized cell below floor {MIN_SAMPLE_REALIZED} carries a median"))


def _check_disclosure_and_volume(con, tables: set[str], rep: Report) -> None:
    # ---- disclosure rate on the advertised lens ----
    dname = "disclosure.fact_salary_job"
    table = "fact_salary_job"
    if table in tables and "transparency" in _columns(con, table):
        try:
            total = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            disclosed = int(con.execute(
                f"SELECT COUNT(*) FROM {table} "
                f"WHERE transparency IS NOT NULL AND transparency > 0"
            ).fetchone()[0])
            rate = (disclosed / total) if total else 0.0
            if total == 0:
                rep.add(Check(dname, "disclosure", "skip",
                              "fact_salary_job empty — disclosure check skipped"))
            elif rate < MIN_DISCLOSURE_RATE:
                rep.add(Check(dname, "disclosure", "fail",
                              f"disclosure rate {rate:.1%} < floor {MIN_DISCLOSURE_RATE:.0%}",
                              {"rate": round(rate, 4), "disclosed": disclosed, "total": total}))
            else:
                rep.add(Check(dname, "disclosure", "pass",
                              f"disclosure rate {rate:.1%} >= {MIN_DISCLOSURE_RATE:.0%}",
                              {"rate": round(rate, 4), "disclosed": disclosed, "total": total}))
        except Exception as e:  # noqa: BLE001
            rep.add(Check(dname, "disclosure", "fail", f"disclosure scan failed: {e}"))
    else:
        rep.add(Check(dname, "disclosure", "skip",
                      "fact_salary_job/transparency absent — disclosure check skipped"))

    # ---- per-role volume floor on demand (postings_count) ----
    vname = "volume.fact_demand"
    table = "fact_demand"
    cols = _columns(con, table)
    if table in tables and "postings_count" in cols:
        try:
            # roles that exist in demand but never clear the per-role volume floor in
            # ANY country/year. A published role with no country above the floor is too
            # thin to be called "the market" for that role.
            thin = con.execute(
                f"SELECT role_id, MAX(postings_count) AS top FROM {table} "
                f"GROUP BY role_id HAVING MAX(postings_count) < {ROLE_VOLUME_FLOOR}"
            ).fetchall()
            total_roles = int(con.execute(
                f"SELECT COUNT(DISTINCT role_id) FROM {table}"
            ).fetchone()[0])
            if total_roles == 0:
                rep.add(Check(vname, "disclosure", "skip",
                              "fact_demand has no roles — volume floor skipped"))
            elif thin:
                rep.add(Check(vname, "disclosure", "fail",
                              f"{len(thin)}/{total_roles} role(s) never clear volume floor "
                              f"{ROLE_VOLUME_FLOOR} in any country",
                              {"thin_roles": [r[0] for r in thin][:25],
                               "thin_count": len(thin), "floor": ROLE_VOLUME_FLOOR}))
            else:
                rep.add(Check(vname, "disclosure", "pass",
                              f"all {total_roles} roles clear volume floor {ROLE_VOLUME_FLOOR}"))
        except Exception as e:  # noqa: BLE001
            rep.add(Check(vname, "disclosure", "fail", f"volume scan failed: {e}"))
    else:
        rep.add(Check(vname, "disclosure", "skip",
                      "fact_demand/postings_count absent — volume floor skipped"))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _report_path() -> Path:
    """Write the report next to the warehouse file (data dir)."""
    settings.ensure_dirs()
    return settings.duckdb_file.parent / "validation_report.json"


def run_validation(*, write_report: bool = True) -> Report:
    """Run every check family against the READ-ONLY warehouse. Never raises on a data
    problem — a problem is a recorded failing Check. Returns the populated Report.
    """
    t0 = time.time()
    rep = Report(warehouse=str(settings.duckdb_file))

    # open warehouse read-only — a totally-absent/locked warehouse is a single clear FAIL
    try:
        from backend.core.db import duckdb_connect
        con = duckdb_connect(read_only=True)
    except Exception as e:  # noqa: BLE001
        rep.add(Check("warehouse.open", "rowfloor", "fail",
                      f"cannot open warehouse read-only at {settings.duckdb_file}: {e}"))
        rep.elapsed_s = time.time() - t0
        if write_report:
            _write(rep)
        return rep

    try:
        tables = _existing_tables(con)
        if not tables:
            rep.add(Check("warehouse.empty", "rowfloor", "fail",
                          "warehouse has no tables — nothing built; refusing to publish"))
        else:
            _check_row_floors(con, tables, rep)
            _check_schema(con, tables, rep)
            _check_sanity_bounds(con, tables, rep)
            _check_suppression(con, tables, rep)
            _check_disclosure_and_volume(con, tables, rep)
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass

    rep.elapsed_s = time.time() - t0
    if write_report:
        _write(rep)
    return rep


def _write(rep: Report) -> None:
    try:
        path = _report_path()
        path.write_text(json.dumps(rep.to_dict(), indent=2), encoding="utf-8")
        log.info("validation_report.json written to %s", path)
    except Exception as e:  # noqa: BLE001
        log.warning("could not write validation report: %s", e)


def _print_summary(rep: Report) -> None:
    d = rep.to_dict()
    s = d["summary"]
    line = "=" * 72
    print(f"\n{line}")
    print(f"STRATA RUN-GATE — {'PASS ✅' if rep.ok else 'FAIL ❌'}   "
          f"({s['passed']} pass / {s['failed']} fail / {s['skipped']} skip "
          f"of {s['total']})  in {d['elapsed_s']}s")
    print(f"warehouse: {rep.warehouse}")
    print(line)
    # group by category for a readable dump
    by_cat: dict[str, list[Check]] = {}
    for c in rep.checks:
        by_cat.setdefault(c.category, []).append(c)
    glyph = {"pass": "✓", "fail": "✗", "skip": "·"}
    for cat, checks in by_cat.items():
        print(f"\n[{cat}]")
        for c in checks:
            print(f"  {glyph.get(c.status, '?')} {c.name:38} {c.detail}")
    if not rep.ok:
        print(f"\n{line}")
        print(f"BREACHES ({s['failed']}): {', '.join(s['failures'])}")
        print("PUBLISH BLOCKED — fix the above before publishing.")
    print(line + "\n")


def validate_run(*, write_report: bool = True, verbose: bool = True) -> int:
    """Public entrypoint for callers (e.g. ``publish``). Runs the gate and returns an
    EXIT CODE: 0 = clean, non-zero = at least one breach. ``publish_served`` should
    call this and REFUSE to proceed on a non-zero return.

    Usage in publish::

        from backend.pipelines.validate_run import validate_run
        if validate_run() != 0:
            raise SystemExit("run-gate failed — refusing to publish")
    """
    rep = run_validation(write_report=write_report)
    if verbose:
        _print_summary(rep)
    breaches = sum(1 for c in rep.checks if c.failed)
    return 0 if rep.ok else min(breaches, 125)  # cap so it stays a valid POSIX code


def main() -> int:
    return validate_run()


if __name__ == "__main__":
    sys.exit(main())
