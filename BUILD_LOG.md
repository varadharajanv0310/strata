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

## Phase 0 — Scaffold

_(entries appended as work proceeds)_
