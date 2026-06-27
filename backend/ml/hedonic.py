"""Hedonic salary model — the marginal, confound-cleansed pay of a skill.

Most "X pays $Y" charts are role-mix artifacts (Rust people are senior). This fits
a hedonic wage regression on **person-level** Stack Overflow data already in
staging:

    log(comp_usd) ~ skills + (role × seniority × country × year) fixed effects

The interacted cell fixed effect absorbs price level / PPP / market, so a skill's
coefficient is its **within-market marginal premium** — "Kubernetes is worth +X%
holding role, level, country and year fixed". For a linear model the SHAP value of
a skill dummy *is* its coefficient (reported as the premium); confidence comes from
a bootstrap over respondents.

Pure modelling on data in hand — reads the SO raw CSVs inside the cached
``staging/so_survey/so_*.zip`` (the per-person skill columns the salary aggregate
throws away). NO download, NO ingestion run.
"""
from __future__ import annotations

import csv
import io
import math
import time
import zipfile
from collections import Counter, defaultdict

from backend.core.logging import get_logger
from backend.ingest.so_survey import (
    SALARY_FIELDS,
    SAL_HI,
    SAL_LO,
    SO_COUNTRY,
    SO_YEARS,
    _exp_band,
    _exp_band_2017,
    _pick,
    _role_of,
    _zip_path,
)

log = get_logger("ml.hedonic")

# per-person skill columns (semicolon-delimited multi-select). 2018+ then 2017.
_SKILL_COLS_MODERN = ["LanguageHaveWorkedWith", "DatabaseHaveWorkedWith",
                      "PlatformHaveWorkedWith", "WebframeHaveWorkedWith",
                      "MiscTechHaveWorkedWith", "ToolsTechHaveWorkedWith"]
_SKILL_COLS_2017 = ["HaveWorkedLanguage", "HaveWorkedDatabase",
                    "HaveWorkedPlatform", "HaveWorkedFramework"]


def _csv_member(zf: zipfile.ZipFile) -> str | None:
    cands = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    pref = [n for n in cands if "survey_results_public" in n.lower()]
    if pref:
        return pref[0]
    # else the biggest csv (the response file, not the schema file)
    return max(cands, key=lambda n: zf.getinfo(n).file_size) if cands else None


def _skills_from_row(row: dict, cols: list[str]) -> list[str]:
    out: list[str] = []
    for c in cols:
        v = row.get(c)
        if v:
            out.extend(t.strip().lower() for t in str(v).split(";") if t.strip())
    return out


def load_person_rows(years: list[int] | None = None, max_per_year: int | None = 40_000) -> list[dict]:
    """Extract per-respondent {comp_usd, role, country, exp, year, skills} from the
    cached SO zips. Reuses the SO connector's role/country/experience mappers."""
    years = years or SO_YEARS
    rows: list[dict] = []
    for year in years:
        zp = _zip_path(year)
        if not zp.exists():
            log.info("SO %d zip absent (%s) — skipping", year, zp)
            continue
        is2017 = year == 2017
        skill_cols = _SKILL_COLS_2017 if is2017 else _SKILL_COLS_MODERN
        with zipfile.ZipFile(zp) as zf:
            member = _csv_member(zf)
            if not member:
                continue
            with zf.open(member) as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace"))
                cols = reader.fieldnames or []
                sal_f = "Salary" if is2017 else _pick(cols, SALARY_FIELDS)
                dev_f = "DeveloperType" if is2017 else _pick(cols, ["DevType", "DeveloperType"])
                exp_f = "YearsCodedJob" if is2017 else _pick(cols, ["YearsCodePro", "YearsCode"])
                ctry_f = _pick(cols, ["Country"])
                have = [c for c in skill_cols if c in cols]
                if not (sal_f and dev_f and ctry_f):
                    log.info("SO %d: missing key cols (sal=%s dev=%s ctry=%s)", year, sal_f, dev_f, ctry_f)
                    continue
                kept = 0
                for r in reader:
                    if max_per_year and kept >= max_per_year:
                        break
                    cc = SO_COUNTRY.get((r.get(ctry_f) or "").strip())
                    if not cc:
                        continue
                    role = _role_of(r.get(dev_f))
                    if not role:
                        continue
                    raw = (r.get(sal_f) or "").strip().replace(",", "")
                    try:
                        comp = float(raw)
                    except ValueError:
                        continue
                    if not (SAL_LO <= comp <= SAL_HI):
                        continue
                    exp = _exp_band_2017(r.get(exp_f)) if is2017 else _exp_band(r.get(exp_f))
                    skills = _skills_from_row(r, have)
                    rows.append({"comp": comp, "role": role, "country": cc,
                                 "exp": exp, "year": year, "skills": skills})
                    kept += 1
        log.info("SO %d: %d person-rows for hedonic", year, kept)
    return rows


class HedonicModel:
    def __init__(self, skills: list[str], coef: dict[str, float], n: int, r2: float):
        self.skills = skills
        self.coef = coef          # skill -> log-premium coefficient
        self.n = n
        self.r2 = r2

    def premium(self, skill: str) -> float | None:
        c = self.coef.get(skill.lower())
        return (math.exp(c) - 1.0) if c is not None else None


def build_design(rows: list[dict], top_skills: int = 60):
    """Build the sparse hedonic design ONCE: (role|sen|country|year) cell dummies +
    top-K skill dummies. Returns (X csr, y, skills, skill_ix). Bootstrap resamples
    row-indices of X instead of rebuilding it — the expensive part runs once."""
    import numpy as np
    from scipy import sparse

    skill_freq = Counter(s for r in rows for s in set(r["skills"]))
    skills = [s for s, _ in skill_freq.most_common(top_skills)]
    skill_ix = {s: i for i, s in enumerate(skills)}
    cells: dict[str, int] = {}
    rows_idx, cols_idx, data, y = [], [], [], []
    n_skills = len(skills)
    for i, r in enumerate(rows):
        cell = f"{r['role']}|{r['exp']}|{r['country']}|{r['year']}"
        ci = cells.setdefault(cell, n_skills + len(cells))
        rows_idx.append(i); cols_idx.append(ci); data.append(1.0)
        for s in set(r["skills"]):
            if s in skill_ix:
                rows_idx.append(i); cols_idx.append(skill_ix[s]); data.append(1.0)
        y.append(math.log(r["comp"]))
    X = sparse.csr_matrix((data, (rows_idx, cols_idx)),
                          shape=(len(rows), n_skills + len(cells)))
    return X, np.asarray(y), skills, skill_ix


def _fit_coef(X, y, alpha: float):
    from sklearn.linear_model import Ridge
    m = Ridge(alpha=alpha, fit_intercept=True).fit(X, y)
    return m.coef_, float(m.score(X, y))


def fit_hedonic(rows: list[dict], top_skills: int = 60, alpha: float = 1.0) -> HedonicModel:
    """Ridge hedonic fit — coefficients (log-premium) per skill."""
    X, y, skills, skill_ix = build_design(rows, top_skills)
    coef, r2 = _fit_coef(X, y, alpha)
    log.info("hedonic fit: n=%d, skills=%d, cells=%d, R²=%.3f",
             len(rows), len(skills), X.shape[1] - len(skills), r2)
    return HedonicModel(skills, {s: float(coef[skill_ix[s]]) for s in skills}, len(rows), r2)


def _bootstrap(X, y, skill_ix, targets, *, B: int, alpha: float, seed: int = 0) -> dict:
    """Refit on B respondent-resamples of the PREBUILT design; collect each target
    skill's premium distribution in one pass."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = X.shape[0]
    boots: dict[str, list[float]] = {s: [] for s in targets}
    t0 = time.time()
    for b in range(B):
        idx = rng.integers(0, n, n)
        coef, _ = _fit_coef(X[idx], y[idx], alpha)
        for s in targets:
            boots[s].append(math.exp(coef[skill_ix[s]]) - 1.0)
        if (b + 1) % 20 == 0:
            print(f"[hedonic] bootstrap {b+1}/{B} ({time.time()-t0:.0f}s)", flush=True)
    return boots


def skill_premium(rows: list[dict], skill: str, *, bootstrap: int = 60, top_skills: int = 60,
                  alpha: float = 1.0, seed: int = 0) -> dict:
    """Marginal % premium of a skill + bootstrap CI (resamples respondents)."""
    import numpy as np

    X, y, skills, skill_ix = build_design(rows, top_skills)
    coef, r2 = _fit_coef(X, y, alpha)
    sk = skill.lower()
    if sk not in skill_ix:
        return {"skill": skill, "premium": None, "note": "skill not in top-K / too rare"}
    point = math.exp(coef[skill_ix[sk]]) - 1.0
    boots = _bootstrap(X, y, skill_ix, [sk], B=bootstrap, alpha=alpha, seed=seed)[sk]
    lo, hi = (float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))) if boots else (None, None)
    return {"skill": skill, "premium": point, "ci95": [lo, hi],
            "n": len(rows), "r2": r2, "bootstrap": len(boots)}


def sample(skills=("kubernetes", "rust", "go", "react", "terraform"), max_per_year=30_000,
           bootstrap=40) -> dict:
    """Fit once and report a few marginal skill premiums with CIs — a runnable proof."""
    import numpy as np

    rows = load_person_rows(max_per_year=max_per_year)
    print(f"\n=== HEDONIC SKILL PREMIUMS  (n={len(rows):,} SO respondents, "
          f"role×seniority×country×year FE) ===")
    X, y, _all, skill_ix = build_design(rows)
    coef, r2 = _fit_coef(X, y, 1.0)
    targets = [s for s in skills if s.lower() in skill_ix]
    boots = _bootstrap(X, y, skill_ix, [s.lower() for s in targets], B=bootstrap, alpha=1.0)
    out = {"n": len(rows), "r2": r2, "premiums": []}
    for sk in skills:
        if sk.lower() not in skill_ix:
            print(f"  {sk:14} — not in top-K (too rare)")
            continue
        p = math.exp(coef[skill_ix[sk.lower()]]) - 1.0
        lo, hi = np.percentile(boots[sk.lower()], [2.5, 97.5])
        print(f"  {sk:14} {p*100:+6.1f}%   95% CI [{lo*100:+5.1f}%, {hi*100:+5.1f}%]")
        out["premiums"].append({"skill": sk, "premium": p, "ci95": [float(lo), float(hi)]})
    print(f"  (model R²={r2:.3f})")
    return out


def _staging_file():
    from backend.core.config import settings
    d = settings.staging_dir / "analytics"
    d.mkdir(parents=True, exist_ok=True)
    return d / "skill_premiums.json"


def run(top_skills: int = 60, max_per_year: int = 40_000, **kw) -> dict:
    """Fit the hedonic model on cached SO person rows → per-skill marginal premium →
    staging json (the serving materialize reads it). Pure derivation on cached data —
    no download/ingestion. Registered as a collect_all stage."""
    import json

    from backend.warehouse.build import slug
    rows = load_person_rows(max_per_year=max_per_year)
    if not rows:
        log.info("hedonic: no person rows cached — skip [run so_survey first]")
        return {"premiums": 0, "written": False, "note": "no person rows"}
    model = fit_hedonic(rows, top_skills=top_skills)
    out = []
    for s in model.skills:
        p = model.premium(s)
        if p is None:
            continue
        out.append({"skill_id": slug(s), "skill_name": s, "premium_pct": round(p * 100, 1),
                    "n": model.n, "r2": round(model.r2, 3)})
    _staging_file().write_text(json.dumps(out), encoding="utf-8")
    log.info("hedonic skill premiums → staging: %d skills (n=%d, R2=%.3f)",
             len(out), model.n, model.r2)
    return {"premiums": len(out), "n": model.n, "r2": round(model.r2, 3), "written": True}


def load_premiums() -> list[dict]:
    import json
    f = _staging_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


if __name__ == "__main__":  # pragma: no cover
    sample()
