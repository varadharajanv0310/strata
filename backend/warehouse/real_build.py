"""Assemble the **real** nested dataset: live Adzuna numbers overlaid on the
curated role taxonomy.

We start from the seed structure (which already carries the curated catalogue —
names, families, skills, ladders, blurbs — and a valid, fully-shaped dataset),
then overwrite, per Role×Country, the figures Adzuna measures: the salary anchor,
the demand index, the pay-transparency rate, sample size, confidence and source.
Multi-year *shape* is preserved from the seed curve but **re-anchored to the real
latest value** (Adzuna's free tier gives the current aggregate, not 9 years of
history), so the headline is real and the trajectory is a clearly-labelled model.
``is_seed`` flips to False; the authoritative Job Score and back-tested forecast
are recomputed downstream from these real facts.
"""
from __future__ import annotations

from backend.core.logging import get_logger
from backend.ingest.adzuna_salaries import load_cache
from backend.warehouse.seed import COUNTRIES, ROLE_DEFS, build_seed_dataset, round_nice

log = get_logger("warehouse.real_build")

REAL_SOURCE = "Adzuna aggregated postings"
MODELED_SOURCE = "Modeled estimate (sparse market)"
MIXED_SOURCE = "Adzuna demand · modeled salary"

# Adzuna reports Singapore salaries monthly / mixed-unit (SWE mean ≈ S$3k), so the
# figures are unreliable. We keep SG's real *demand* (posting volume is sound) but
# fall back to a modeled, flagged salary rather than publish a broken number.
UNRELIABLE_SALARY_CC = {"SG"}


def _demand_from_count(count: int, max_count: int) -> int:
    """Map raw posting volume → a 0–100 demand index, scaled within the country.

    Power curve lifts the long tail so niche-but-real roles don't read as ~0; the
    most-posted role in a country lands near the top of the range.
    """
    if max_count <= 0:
        return 40
    frac = max(0.0, min(1.0, count / max_count))
    return max(12, min(99, round(40 + 59 * (frac ** 0.5))))


def build_real_dataset() -> dict:
    ds = build_seed_dataset()
    cache = load_cache()
    if not cache:
        raise RuntimeError("No Adzuna staging cache found — run the fetch first "
                           "(backend.ingest.adzuna_salaries.fetch_all)")

    for s in (REAL_SOURCE, MODELED_SOURCE, MIXED_SOURCE):
        if s not in ds["sources"]:
            ds["sources"].append(s)

    # per-country max posting volume, for demand scaling
    max_count: dict[str, int] = {}
    for co in COUNTRIES:
        counts = [int((cache.get((co["code"], d["id"])) or {}).get("count") or 0) for d in ROLE_DEFS]
        max_count[co["code"]] = max(counts) if counts else 0

    def _apply_demand(cd, count, code):
        dem = _demand_from_count(count, max_count[code])
        old_last = cd["demandSeries"][-1]["value"] or dem
        dscale = (dem / old_last) if old_last else 1.0
        cd["demandSeries"] = [{"year": p["year"], "value": max(8, min(100, round(p["value"] * dscale)))}
                              for p in cd["demandSeries"]]
        cd["demandSeries"][-1]["value"] = dem
        cd["demand"] = dem

    real_n = total = modeled_n = mixed_n = 0
    for role in ds["roles"]:
        for code, cd in role["countries"].items():
            total += 1
            rec = cache.get((code, role["id"])) or {}
            mean = rec.get("mean")
            count = int(rec.get("count") or 0)
            n_res = int(rec.get("n_results") or 0)
            n_disc = int(rec.get("n_disclosed") or 0)
            has_salary = bool(mean and float(mean) > 0)

            if has_salary and code in UNRELIABLE_SALARY_CC:
                # real demand + transparency, but salary stays modeled (flagged)
                mixed_n += 1
                _apply_demand(cd, count, code)
                if n_res:
                    cd["transparency"] = max(0.05, min(0.95, round(n_disc / n_res, 3)))
                cd["sample"] = count
                cd["conf"] = "low"
                cd["source"] = MIXED_SOURCE
                cd["kind"] = "job-level"
            elif has_salary:
                real_n += 1
                real_med = round_nice(float(mean), code)
                old_med = cd["median"] or real_med
                scale = (real_med / old_med) if old_med else 1.0
                cd["series"] = [{"year": p["year"], "value": round_nice(p["value"] * scale, code)}
                                for p in cd["series"]]
                cd["median"] = real_med
                cd["series"][-1]["value"] = real_med

                _apply_demand(cd, count, code)
                if n_res:
                    cd["transparency"] = max(0.05, min(0.95, round(n_disc / n_res, 3)))

                cd["sample"] = count
                cd["conf"] = "high" if count > 1500 else "med" if count > 400 else "low"
                cd["source"] = REAL_SOURCE
                cd["kind"] = "job-level"
                cd["freshness"] = "live"
            else:
                modeled_n += 1
                cd["conf"] = "low"
                cd["source"] = MODELED_SOURCE
                cd["kind"] = "job-level"

    ds["is_seed"] = False
    log.info("real overlay: %d/%d real (Adzuna salary+demand), %d mixed (real demand, modeled salary), %d modeled",
             real_n, total, mixed_n, modeled_n)
    ds["_real_count"], ds["_total_count"] = real_n, total
    ds["_modeled_count"], ds["_mixed_count"] = modeled_n, mixed_n
    return ds
