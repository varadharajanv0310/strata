"""Job Score computation (brief §4) — GPU-free, fully runnable.

Per **country**: normalize each component to 0–1 **within that country**, then a
weighted sum (`w_demand·demand + w_interest·(1−interest) + w_salary·salary`), with
weights from config. Interest is inverted (less crowding ⇒ higher score) and is
labelled *interest*, never "competition". Salary is **PPP-normalized** so it is
comparable within the ranking. Output is a 0–10 score **and** a percentile against
the country's **full** role distribution. Components are persisted (clickable on
the frontend). Reads/writes the warehouse only — never on the API request path.
"""
from __future__ import annotations

from collections import defaultdict

from backend.core.config import settings
from backend.core.db import duckdb_connect
from backend.core.logging import get_logger, stage_timer

log = get_logger("ml.job_score")


def _norm(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    span = hi - lo
    if span == 0:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / span for k, v in values.items()}


def compute_job_scores(rematerialize: bool = False) -> dict:
    """Recompute `fact_job_score` from facts. Returns a small summary.

    This is the real (Phase-5) Job Score; on seed data it validates the math.
    Pass ``rematerialize=True`` to refresh the serving marts afterwards.
    """
    w = settings.jobscore_weights
    con = duckdb_connect()
    try:
        with stage_timer(log, "ml.job_score"):
            score_year = con.execute("SELECT max(year) FROM fact_salary_job").fetchone()[0]
            countries = [r[0] for r in con.execute("SELECT code FROM dim_country").fetchall()]

            salary = con.execute(
                "SELECT role_id, country_code, median FROM fact_salary_job "
                "WHERE experience_code='pooled' AND year=?", [score_year]).fetchall()
            demand = con.execute(
                "SELECT role_id, country_code, demand_index FROM fact_demand WHERE year=?",
                [score_year]).fetchall()
            interest = con.execute(
                "SELECT role_id, country_code, interest_index FROM fact_interest").fetchall()
            ppp = dict(con.execute("SELECT country_code, ppp_factor FROM dim_ppp WHERE year=?", [score_year]).fetchall())

            sal = {(r[0], r[1]): r[2] for r in salary}
            dem = {(r[0], r[1]): r[2] for r in demand}
            int_ = {(r[0], r[1]): r[2] for r in interest}

            by_country: dict[str, list[str]] = defaultdict(list)
            for (rid, code) in sal:
                by_country[code].append(rid)

            rows = []
            for code in countries:
                roles = by_country.get(code, [])
                if not roles:
                    continue
                pf = ppp.get(code, 1.0) or 1.0
                dem_n = _norm({r: float(dem.get((r, code), 0)) for r in roles})
                int_n = _norm({r: float(int_.get((r, code), 50)) for r in roles})
                sal_n = _norm({r: float(sal.get((r, code), 0)) / pf for r in roles})

                scored = []
                for r in roles:
                    s01 = (w["demand"] * dem_n[r]
                           + w["interest"] * (1 - int_n[r])
                           + w["salary"] * sal_n[r])
                    scored.append((r, s01, dem_n[r], sal_n[r], 1 - int_n[r]))
                scored.sort(key=lambda x: x[1], reverse=True)
                n = len(scored)
                for i, (r, s01, dn, sn, inv_i) in enumerate(scored):
                    rows.append((r, code, score_year, round(s01 * 10, 1),
                                 round(dn * 10, 1), round(sn * 10, 1), round(inv_i * 10, 1),
                                 i + 1, max(1, round(i / n * 100)), "compute:jobscore", False))

            con.execute("BEGIN TRANSACTION")
            con.execute("DELETE FROM fact_job_score WHERE source_id='compute:jobscore'")
            con.executemany("INSERT OR REPLACE INTO fact_job_score VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            con.execute("COMMIT")
            log.info("computed %d job scores across %d countries (year %s, weights %s)",
                     len(rows), len([c for c in countries if by_country.get(c)]), score_year, w)
    finally:
        con.close()

    if rematerialize:
        from backend.marts.materialize import materialize_from_warehouse
        materialize_from_warehouse()
    return {"rows": len(rows), "year": score_year, "weights": w}


def run() -> None:
    compute_job_scores()


if __name__ == "__main__":
    compute_job_scores()
