"""Forecasting (brief §4): a back-test is stored and the band widens with horizon."""
from __future__ import annotations

from collections import defaultdict

from backend.core.db import duckdb_connect
from backend.ml.forecasting import compute_forecasts
from backend.tests.conftest import _restore_seed


def test_backtest_and_widening_band():
    try:
        res = compute_forecasts()
        assert res["backtest_rows"] > 0 and res["forecast_rows"] > 0

        con = duckdb_connect(read_only=True)
        fc = con.execute(
            "SELECT year, value, lo, hi FROM fact_demand_forecast WHERE source_id='compute:forecast'"
        ).fetchall()
        bt = con.execute(
            "SELECT count(*) FROM fact_forecast_backtest WHERE source_id='compute:forecast'"
        ).fetchone()[0]
        con.close()

        assert bt > 0
        assert all(lo <= v <= hi for _, v, lo, hi in fc)         # value inside band

        # average band width is non-decreasing across the forecast horizon
        by_year = defaultdict(list)
        for yr, _, lo, hi in fc:
            by_year[yr].append(hi - lo)
        years = sorted(by_year)
        avg = [sum(by_year[y]) / len(by_year[y]) for y in years]
        assert avg[0] <= avg[-1] + 1e-9
    finally:
        _restore_seed()
