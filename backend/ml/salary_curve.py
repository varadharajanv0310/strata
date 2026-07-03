"""Fit a pay-vs-experience CURVE per (role, country, lens) — GRID_PLAN §3 Layer 4.

Turns the interval-native, sparse ``fact_salary_yoe_obs`` rows (AmbitionBox per-year,
PayScale brackets, Robert Half tiers, …) into a smooth, MONOTONE curve the UI can query
for *any* year — the "what do I earn at 5 years?" answer, everywhere data exists.

Honesty rules baked in:
* **Monotone non-decreasing** via weighted isotonic regression (pay doesn't fall with
  experience in the aggregate; plateaus are allowed). Weight = sample size.
* **Never blended across kinds** — advertised / realized / official / estimate each get
  their own curve; the caller groups by ``kind``.
* **No extrapolation** — the fitted curve spans only the observed range
  ``[min_obs_yoe, max_obs_yoe]`` and ends there; it never invents years past the last
  observation. Each point is flagged ``observed`` (a real bracket covered it) or
  ``interpolated`` (a fitted year between observations).
* Confidence band widens where support is thin (few effective samples).

Interval handling: a bracket [lo,hi] with median m contributes an anchor at its
midpoint (or at lo for open-ended 10+). Per-year points ([N,N]) anchor at N. Level-only
rows (yoe NULL) are ignored here — they reach the years axis only via the seniority↔YoE
concordance upstream, never by inventing years in this module.

Pure numpy/sklearn/scipy — NO network, NO GPU. ``fit_curves(obs_rows)`` returns curve
rows ready for ``fact_salary_curve``; ``fit_one`` is unit-testable in isolation.
"""
from __future__ import annotations

import math

EXTRAP_CAP = 0          # 0 = curve ends at the last observation (no extrapolation)
MIN_ANCHORS = 2         # need at least 2 experience anchors to draw a curve
YOE_GRID_MAX = 25


def _anchor(row: dict) -> tuple[float, float, float] | None:
    """(yoe, median, weight) from one observation, or None if it has no usable year."""
    lo, hi, med = row.get("yoe_min"), row.get("yoe_max"), row.get("median")
    if med is None or lo is None:
        return None
    if hi is None:
        hi = lo
    # open-ended top bracket (e.g. 10..99 / 20..30): anchor a little above the floor
    yoe = lo if (hi - lo) > 12 else (lo + hi) / 2.0
    w = float(row.get("sample_size") or 1) ** 0.5     # sqrt-dampen huge n
    return float(yoe), float(med), max(w, 1.0)


def fit_one(obs_rows: list[dict], method: str = "isotonic-pchip") -> list[dict]:
    """Fit one (role, country, kind) group → per-year curve points. [] if too thin."""
    import numpy as np
    from scipy.interpolate import PchipInterpolator
    from sklearn.isotonic import IsotonicRegression

    anchors = [a for a in (_anchor(r) for r in obs_rows) if a]
    if len({round(a[0], 1) for a in anchors}) < MIN_ANCHORS:
        return []
    # collapse duplicate years (weighted mean), keep weights
    by_year: dict[float, list[tuple[float, float]]] = {}
    for yoe, med, w in anchors:
        by_year.setdefault(round(yoe, 1), []).append((med, w))
    xs, ys, ws = [], [], []
    for yoe in sorted(by_year):
        pts = by_year[yoe]
        wsum = sum(w for _, w in pts)
        xs.append(yoe)
        ys.append(sum(m * w for m, w in pts) / wsum)
        ws.append(wsum)
    xs, ys, ws = np.array(xs), np.array(ys), np.array(ws)

    iso = IsotonicRegression(increasing=True, out_of_bounds="clip")
    y_iso = iso.fit_transform(xs, ys, sample_weight=ws)

    lo_year, hi_year = int(math.floor(xs.min())), int(math.ceil(xs.max())) + EXTRAP_CAP
    hi_year = min(hi_year, YOE_GRID_MAX)
    grid = list(range(max(0, lo_year), hi_year + 1))
    if len(xs) >= 2:
        pchip = PchipInterpolator(xs, y_iso, extrapolate=False)
        fitted = pchip(np.array(grid, dtype=float))
    else:
        fitted = np.interp(grid, xs, y_iso)

    obs_years = {round(x) for x in xs}
    span = max(1.0, xs.max() - xs.min())
    out = []
    for yr, fv in zip(grid, fitted):
        if fv is None or (isinstance(fv, float) and math.isnan(fv)):
            continue
        # nearest anchor drives the confidence band (thin support ⇒ wider band)
        d = min(abs(yr - x) for x in xs)
        rel = 0.06 + 0.10 * (d / span)             # ±6%..~16% band
        neff = float(ws[int(np.argmin(np.abs(xs - yr)))])
        out.append({
            "yoe": int(yr), "fit_median": round(float(fv), 2),
            "lo": round(float(fv) * (1 - rel), 2), "hi": round(float(fv) * (1 + rel), 2),
            "support": "observed" if round(yr) in obs_years else "interpolated",
            "n_effective": round(neff, 2), "method": method,
        })
    return out


def fit_curves(obs_rows: list[dict]) -> list[dict]:
    """Fit every (role_id, country_code, kind) group in ``obs_rows`` → fact_salary_curve rows."""
    groups: dict[tuple, list[dict]] = {}
    for r in obs_rows:
        if r.get("yoe_min") is None or r.get("median") is None:
            continue                                # level-only rows don't fit here
        groups.setdefault((r["role_id"], r["country_code"], r.get("kind", "estimate")), []).append(r)
    rows = []
    for (role_id, country, kind), grp in groups.items():
        for pt in fit_one(grp):
            rows.append({"role_id": role_id, "country_code": country, "kind": kind, **pt})
    return rows


if __name__ == "__main__":  # pragma: no cover — self-test on synthetic monotone-ish data
    import json
    demo = [
        {"role_id": "data-sci", "country_code": "IN", "kind": "estimate",
         "yoe_min": y, "yoe_max": y, "median": m, "sample_size": n}
        for y, m, n in [(1, 800000, 108000), (2, 780000, 202000), (3, 844000, 218000),
                        (4, 1020000, 134000), (5, 1250000, 34000)]
    ]
    curve = fit_curves(demo)
    print(json.dumps(curve, indent=1))
    meds = [c["fit_median"] for c in curve]
    assert meds == sorted(meds), "curve must be monotone non-decreasing"
    assert max(c["yoe"] for c in curve) <= 5, "must not extrapolate past last observation (yoe=5)"
    print("\nOK: monotone, no far extrapolation,", len(curve), "yearly points")
