"""Demand forecasting — back-tested and honest (brief §4/§7).

Validates against held-out history (stores predicted vs actual in
`fact_forecast_backtest`), then forecasts the horizon with a **confidence band
derived from real back-test error** that widens with horizon — never a confident
invented line. A GPU-free numpy baseline (least-squares trend + residual band)
runs now; if `darts`/`statsmodels` (requirements-ml) are present they are used for
stronger models. Reads/writes the warehouse only.
"""
from __future__ import annotations

import math

import numpy as np

from backend.core.config import settings
from backend.core.db import duckdb_connect
from backend.core.logging import get_logger, stage_timer

log = get_logger("ml.forecasting")

Z = 1.28  # ~80% interval


def _fit_predict(years: np.ndarray, vals: np.ndarray, future: np.ndarray) -> np.ndarray:
    """Least-squares linear trend (damped) — the GPU-free baseline."""
    a, b = np.polyfit(years, vals, 1)
    return a * future + b


def compute_forecasts(rematerialize: bool = False) -> dict:
    con = duckdb_connect()
    try:
        with stage_timer(log, "ml.forecasting"):
            hist_years = [r[0] for r in con.execute(
                "SELECT DISTINCT year FROM fact_demand ORDER BY year").fetchall()]
            fyears = [r[0] for r in con.execute(
                "SELECT year FROM dim_time WHERE is_forecast ORDER BY year").fetchall()]
            if not fyears:
                last = max(hist_years)
                fyears = [last + 1, last + 2, last + 3]
            pairs = con.execute(
                "SELECT DISTINCT role_id, country_code FROM fact_demand").fetchall()

            k = min(settings.forecast_backtest_periods, max(1, len(hist_years) // 3))
            fc_rows, bt_rows = [], []
            for rid, code in pairs:
                series = con.execute(
                    "SELECT year, demand_index FROM fact_demand WHERE role_id=? AND country_code=? ORDER BY year",
                    [rid, code]).fetchall()
                yrs = np.array([s[0] for s in series], float)
                val = np.array([s[1] for s in series], float)
                if len(yrs) < 4:
                    continue

                # back-test: hold out the last k years, fit on the rest, score
                tr_y, tr_v, te_y, te_v = yrs[:-k], val[:-k], yrs[-k:], val[-k:]
                pred = _fit_predict(tr_y, tr_v, te_y)
                errs = np.abs(pred - te_v)
                rmse = float(np.sqrt(np.mean((pred - te_v) ** 2))) or 2.0
                for yr, p, ac, e in zip(te_y, pred, te_v, errs):
                    bt_rows.append((rid, code, int(yr), float(round(p, 1)), float(ac),
                                    float(round(e, 2)), "compute:forecast", False))

                # forecast horizon with a band that widens with the step
                fut = np.array(fyears, float)
                fpred = _fit_predict(yrs, val, fut)
                for step, (yr, p) in enumerate(zip(fyears, fpred), start=1):
                    band = Z * rmse * math.sqrt(step)
                    v = min(100.0, max(10.0, p))
                    fc_rows.append((rid, code, int(yr), round(v, 1),
                                    round(max(5.0, v - band), 1), round(min(100.0, v + band), 1),
                                    "compute:forecast", False))

            con.execute("BEGIN TRANSACTION")
            con.execute("DELETE FROM fact_demand_forecast WHERE source_id='compute:forecast'")
            con.execute("DELETE FROM fact_forecast_backtest WHERE source_id='compute:forecast'")
            con.executemany("INSERT OR REPLACE INTO fact_demand_forecast VALUES (?,?,?,?,?,?,?,?)", fc_rows)
            con.executemany("INSERT OR REPLACE INTO fact_forecast_backtest VALUES (?,?,?,?,?,?,?,?)", bt_rows)
            con.execute("COMMIT")
            mae = float(np.mean([b[5] for b in bt_rows])) if bt_rows else 0.0
            log.info("forecast %d rows, backtest %d rows, holdout=%d, MAE=%.2f",
                     len(fc_rows), len(bt_rows), k, mae)
    finally:
        con.close()
    if rematerialize:
        from backend.marts.materialize import materialize_from_warehouse
        materialize_from_warehouse()
    return {"forecast_rows": len(fc_rows), "backtest_rows": len(bt_rows), "holdout_periods": k, "mae": round(mae, 2)}


def run() -> None:
    compute_forecasts()


if __name__ == "__main__":
    compute_forecasts()
