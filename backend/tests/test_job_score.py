"""Job Score math (brief §4): within-country normalization, weighted sum, percentile."""
from __future__ import annotations

from backend.core.config import settings
from backend.core.db import duckdb_connect
from backend.ml.job_score import _norm, compute_job_scores
from backend.tests.conftest import _restore_seed


def test_norm_within_range():
    n = _norm({"a": 0.0, "b": 5.0, "c": 10.0})
    assert n["a"] == 0.0 and n["c"] == 1.0 and abs(n["b"] - 0.5) < 1e-9
    assert _norm({"a": 7.0, "b": 7.0}) == {"a": 0.5, "b": 0.5}  # zero span → neutral


def test_job_score_properties():
    try:
        compute_job_scores()
        con = duckdb_connect(read_only=True)
        rows = con.execute(
            "SELECT total, demand_score, pay_score, opp_score, rank, pctile "
            "FROM fact_job_score WHERE source_id='compute:jobscore' AND country_code='IN' ORDER BY rank"
        ).fetchall()
        con.close()
        assert rows
        totals = [r[0] for r in rows]
        assert totals == sorted(totals, reverse=True)            # ranked by composite
        assert all(0 <= t <= 10 for t in totals)                 # 0–10 scale
        assert rows[0][4] == 1                                    # top role rank 1
        assert all(1 <= r[5] <= 100 for r in rows)               # percentile bounds
        # weighted-sum reproduction (components are the *10 versions)
        w = settings.jobscore_weights
        t, d, p, o = rows[0][0], rows[0][1], rows[0][2], rows[0][3]
        assert abs((w["demand"] * d + w["salary"] * p + w["interest"] * o) - t) <= 0.15
    finally:
        _restore_seed()
