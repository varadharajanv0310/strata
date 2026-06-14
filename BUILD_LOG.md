# strata — BUILD_LOG

Running audit trail for the backend & data-platform build. Newest entries at the bottom of each phase.

---

## Environment (probed 2026-06-07)

| Component | Found | Decision |
|---|---|---|
| OS | Windows 11 Pro, `D:\A1- Job market` | Local-first build, no cloud assumptions (per brief §2). |
| Python | 3.13.7 | Brief said 3.11+; 3.13 is fine. A few ML libs lag on 3.13 — pinned conservatively in `requirements-ml.txt`. |
| GPU | RTX 5080, 16 GB VRAM, driver 596.49 | Matches brief. GPU pipeline (`ml/`) targets this; runs are developer-launched on real data. |
| Postgres | **not installed** (`psql` absent) | **Decide-and-continue (§14, environment reality):** serving/app DB is a configurable SQLAlchemy URL, **defaulting to local SQLite** so the stack runs now. Postgres is a one-line `DATABASE_URL` swap — schema is authored with SQLAlchemy/Alembic to be portable. See decision D1. |
| Git | 2.51.0 | repo not yet initialised; frontend present. |

---

## Key architectural decisions

- **D1 — App/serving DB driver.** Brief locks Postgres (§2). Postgres is not installed locally and silently installing a system service is too intrusive to do autonomously. The serving + application database is therefore accessed through SQLAlchemy 2.0 with `DATABASE_URL` from config, **defaulting to `sqlite:///backend/data/app.db`**. All models/migrations are DB-agnostic; pointing `DATABASE_URL` at a Postgres instance is the only change needed for production. The DuckDB/Parquet analytical warehouse is unchanged (embedded, exactly as specified).
- **D2 — ML dependencies split.** `requirements.txt` (core: API + warehouse + ingestion) installs without the multi-GB CUDA stack so the API/warehouse run immediately. `requirements-ml.txt` (torch-cuda, sentence-transformers, faiss, darts, scikit-learn) is installed separately for the GPU pipeline. `ml/` modules import-guard their heavy deps so the API never depends on them.
- **D3 — Frontend is the already-ported Vite app.** The brief described the raw handoff bundle (`job-market/project/`, `data/mock.js`). That bundle was already implemented as a real Vite + React app at the repo root (`src/…`) in the previous task. The "frontend contract" is therefore `src/data/mock.js` (which exports `STRATA`) and the per-surface components under `src/app/`. Wiring targets that app. No UI/feature changes — only the data source is repointed.
- **D4 — Seed reproduces the existing dataset exactly.** The Python seed generator (`backend/warehouse/seed.py`) is a faithful port of the frontend's deterministic `mock.js` generator (mulberry32 RNG + FNV-1a hash, 32-bit-exact). So "API on seed data" renders byte-identically to the current app — proving the wiring without any visual change. All seed rows are tagged `is_seed=true` in the provenance layer and are retired when real ingested data lands (Phase 6).

---

## Version control

- **Repo:** initialised locally on branch `main` (2026-06-07). Remote `origin` = `https://github.com/varadharajanv0310/strata`.
- **Local-only until approved.** Nothing is pushed. The push to the public remote is gated behind explicit developer approval after Phase 8 (see DoD). At that point the build will stop, show the `.gitignore` + a summary of exactly what would go public (confirming no secrets/`.env`/large artifacts are tracked), and wait for go-ahead.
- **Granular history.** Commit after every meaningful step — a module, a schema table, a connector, an endpoint, a wired surface, a passing test, a config addition — not just at phase boundaries. Conventional-commit messages (`feat(...)`, `chore(...)`, `test(...)`, `fix(...)`, `docs(...)`), tagging the phase where useful (e.g. `[P3]`).
- **Authorship:** commits are authored solely by the developer's git identity (`V Varadharajan`). No co-author/AI/tool attribution trailers on any commit.
- **`.gitignore`** excludes secrets (`.env*` except the example), generated data (`backend/data/`, `*.duckdb`, `*.sqlite`), large ingest artifacts (`*.parquet`, `*.warc[.gz]`, `raw/`, `staging/`), model weights (`*.pt/.pth/.safetensors/.faiss`), `node_modules/`, `dist/`, and `__pycache__/`.

---

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 0 — Scaffold | in progress | repo structure, config, DB wiring |
| 1 — Warehouse & app schema | pending | |
| 2 — API + frontend wiring (seed) | pending | the early live-app win |
| 3 — Ingestion connectors | pending | credential-graceful |
| 4 — GPU pipeline | pending | developer-launched on real data |
| 5 — Analytics & marts | pending | |
| 6 — Full wiring & features | pending | resume, accounts, provenance |
| 7 — Validation + full-scale config | pending | |
| 8 — Reports & handoff | pending | |

---

## Phase 0 — Scaffold  ✅

- Repo structure created under `backend/` (`app/ core/ ingest/ warehouse/ marts/ ml/ pipelines/ alembic/ tests/`) + `cli.py`. Frontend left untouched at repo root.
- `core/config.py` — typed pydantic-settings (DB URLs, crawl scope, Job-Score weights, volume floor, source keys, GPU/batch). `core/logging.py`, `core/db.py` (SQLAlchemy + DuckDB sessions), `core/security.py` (bcrypt + PyJWT).
- `cli.py` — argparse dispatcher (`init-db`, `seed`, `serve`, `ingest`, `warehouse-build`, `marts-materialize`, `ml`, `jobscore`, `pipeline`); stages import lazily.
- Core deps installed (API + warehouse stack); Prefect + Great-Expectations deferred to their phases (import-guarded), torch/faiss/etc. in `requirements-ml.txt`.
- **CHECK passed:** `backend.core` imports; SQLAlchemy `SELECT 1` ✓; DuckDB connects (v1.5.3) ✓; bcrypt + JWT round-trip ✓. (Fixed: ensure `backend/data/` exists before SQLite opens its file.)
- Installed runtime versions may differ slightly from the pins in `requirements.txt` (unpinned install for resolver flexibility on py3.13); pins reconciled in Phase 8.

## Phase 1 — Warehouse & app schema  ✅

- **Warehouse (DuckDB/Parquet)** — `warehouse/schema.py`: 16 tables. Dims: `dim_country, dim_role, dim_experience, dim_skill, dim_time, dim_company, dim_source, dim_ppp`. Facts: `fact_salary_job` (job-level), `fact_salary_person` (person-level, **separate by construction**), `fact_demand`, `fact_interest`, `fact_demand_forecast`, `fact_forecast_backtest`. Bridges: `bridge_role_skill`, `bridge_role_ladder`. Grain Role×Country×Experience×Time; native currency; PPP via `dim_ppp` (no FX). Provenance (source/sample/freshness/confidence/`is_seed`) carried at fact-row grain.
- **App DB** — `app/models.py`: `Account`, `Favourite`, `Resume` (resumes stored only for accounts).
- **Serving marts** — `marts/models.py`: `mart_country/family/role/role_skill/role_ladder`, `mart_role_country` (the dashboard spine; salary/demand/forecast as JSON per Role×Country), `mart_market_pulse`, `mart_meta`. Denormalized to the exact `mock.js` shapes.
- **Alembic** wired (`alembic.ini` + `alembic/env.py`); URL injected from `DATABASE_URL` so SQLite/Postgres share one history. Initial migration `b190df9a91d8` autogenerated + applied.
- **CHECK passed:** 16 warehouse tables create clean; a hand-inserted `fact_salary_job` joins through `dim_role/dim_country/dim_source` with full provenance; 12 app/marts tables present via Alembic.

## Phase 2 — API + frontend wiring (seed)  ✅  ← live app

- **Seed generator** (`warehouse/seed.py`): bit-exact Python port of the frontend `mock.js` (mulberry32 + FNV-1a, identical r() call order). Verified: ML Engineer · IN = ₹19,00,000, score 7.2 (D 9.7 / P 4.7 / O 6.2), rank 1, top 1% — **identical to the live UI**.
- **Pipeline:** `seed → warehouse → marts` runs via `python -m backend.cli seed`. Loads 1008 salary, 1008 demand, 336 forecast, 112 job-score rows; materializes 7 countries, 16 roles, 112 role×country marts, 140 market-pulse rows. Market Pulse is a computed aggregate (not carried from seed).
- **API** (`app/`): FastAPI with `/health`, `/api/dataset` (full bundle), `/api/roles`, `/api/roles/{id}`, `/api/jobscore`, `/api/countries`, `/api/countries/{code}`, `/api/explore/pulse`, `/api/compare` (unrestricted), `/api/provenance`, `/api/resume/sample`. Pydantic schemas, CORS, OpenAPI docs at `/docs`. Shapes equal `mock.js`.
- **Frontend wiring (only permitted change):** `data/mock.js` refactored to hydrate the same `STRATA` object from `/api/dataset` (pure helpers kept); new `data/api.js` client (`VITE_API_BASE`); entry points `await loadDataset()` before render with a friendly "start the backend" fallback. Client-side generation fully removed (seed lives server-side, tagged).
- **CHECK passed:** desktop (`/`) and mobile (`/mobile.html`) both render real API data with **zero console errors**; data identical to pre-wiring; `is_seed` flows through (`/health` → `dataset_is_seed: true`). Seed is retired by Phase 6 when real ingested data lands.

## Phase 3 — Ingestion connectors  ◑ (framework + 2 full + 14 scaffold)

- **Framework** (`ingest/base.py`, `checkpoint.py`, `__init__.py`): `BaseConnector` (RAW immutable → STAGING), `ScaffoldConnector`, JSON checkpoints (resumable — Common Crawl never restarts from zero), lazy registry, `run_connector(name|"all")`. Status vocabulary: `ok | skipped (missing creds) | scaffold (impl pending) | error`. **Never halts** the build for one source.
- **Full connectors:** `common_crawl` (cc-index query → byte-range WARC fetch → `schema.org/JobPosting` JSON-LD extraction; bounded by config; checkpointed) and `adzuna` (salary histograms per market; real API; **skips+flags** without `ADZUNA_APP_ID/KEY`).
- **Scaffold connectors (14, documented plans):** so_survey, dol_oflc, gh_archive, stack_exchange, google_trends, pypi_npm, lightcast, esco, onet, oecd_ppp, worldbank_icp, numbeo, bls_oews, company_enrich. Each carries its real extraction plan + join keys + the new signal it adds (inclusion rule).
- **CHECK passed:** 16 connectors registered; `adzuna` → skipped (clear reason), scaffolds → report their plan; no source halts the run. Full-scale Common Crawl scan is config-driven and developer-launched.

## Phase 4 — GPU pipeline  ◑ (2 runnable + 3 GPU-guarded)

- **`ml/job_score.py` (runnable, GPU-free):** §4 formula — normalize each component 0–1 **within country**, weighted sum (`w_demand·demand + w_interest·(1−interest) + w_salary·salary`, weights from config), PPP-normalized salary, output 0–10 + percentile vs the country's **full** distribution, components persisted. Validated on the warehouse: ML Engineer ranks #1 (top 1%).
- **`ml/forecasting.py` (runnable, GPU-free):** **back-tested** — holds out the last K periods, scores predicted-vs-actual (`fact_forecast_backtest`), then forecasts the horizon with a **confidence band derived from real back-test error that widens with horizon**. Validated: MAE 1.42 on held-out 2023–2025. Uses darts/statsmodels if the ML extras are present.
- **GPU-guarded (need `requirements-ml` + ingested data):** `skill_norm` (sentence-transformers + FAISS taxonomy NN), `entity_resolution` (embedding + blocking dedup), `role_derivation` (title clustering with the volume floor). Real algorithm bodies; import-guarded; report clearly when run without extras/data.
- **CHECK passed:** `job_score` + `forecasting` run on the warehouse and produce sane, validated output; guarded stages report cleanly; seed restored after validation so the live UI stays identical.
