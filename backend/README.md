# strata — backend & data platform

Local-first data platform behind the strata frontend: a **DuckDB/Parquet warehouse**
(star schema) + **Postgres/SQLite serving marts**, a **FastAPI** read API matching the
frontend contract, an **idempotent ingestion pipeline** (Common Crawl + many sources),
a **GPU pipeline** (skill normalization, entity resolution, role derivation, back-tested
forecasting, Job Score), the **résumé** feature, and **accounts/favourites**.

Everything runs locally on Windows (no cloud). Postgres is optional locally — the
serving/app DB defaults to SQLite and is a one-line `DATABASE_URL` swap for Postgres.

## 1. Setup

```bash
# from the repo root (D:\A1- Job market)
python -m venv .venv && .venv\Scripts\activate        # optional but recommended
pip install -r backend/requirements.txt                # core API + warehouse + ingestion
cp .env.example .env                                    # then fill optional keys

# GPU pipeline extras (RTX 5080 = Blackwell, needs CUDA 12.8 wheels):
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r backend/requirements-ml.txt
```

## 2. Bring up the app (seed data — instant)

```bash
python -m backend.cli init-db        # create app/serving schema (or: alembic upgrade head)
python -m backend.cli seed           # seed → warehouse → marts  (representative, tagged is_seed)
python -m backend.cli serve          # FastAPI at http://127.0.0.1:8000  (docs at /docs)

# frontend (separate terminal, repo root)
npm install
npm run dev                          # desktop http://localhost:5173/   mobile /mobile.html
```

The frontend fetches `/api/dataset`; set `VITE_API_BASE` if the API isn't on `:8000`.

## 3. Pipeline stages (CLI)

```bash
python -m backend.cli init-db                 # app/serving schema
python -m backend.cli seed                    # seed dataset → warehouse → marts
python -m backend.cli ingest <source> [--limit N]   # one connector (or: ingest all)
python -m backend.cli warehouse-build         # build warehouse from ingested staging (real data)
python -m backend.cli ml <stage>              # skill_norm|entity_resolution|role_derivation|forecasting|job_score
python -m backend.cli jobscore                # recompute Job Score (§4)
python -m backend.cli marts-materialize       # refresh serving marts
python -m backend.cli pipeline <flow>         # seed | ingest | compute | marts | full
python -m backend.cli serve [--reload]        # run the API
```

Connectors are **idempotent, resumable, and credential-graceful** — missing keys or a
pending extractor **skip+flag** instead of halting. The Common Crawl scan checkpoints
per (crawl, domain) and never restarts from zero.

## 4. Full-scale run (real data — developer-launched)

Real ingestion + GPU work needs source credentials and GPU-days, so it's configured and
documented rather than auto-run:

1. Put credentials in `.env` (e.g. `ADZUNA_APP_ID/KEY`, `LIGHTCAST_*`). Public sources
   (Common Crawl, SO survey, GH Archive, O*NET/ESCO, OECD/World Bank PPP) need none.
2. Set the crawl scope in `.env`: `CC_RECENT_CRAWLS`, `CC_HISTORICAL_YEARS`, `CC_TARGET_DOMAINS`,
   plus `ROLE_VOLUME_FLOOR` and the `JOBSCORE_*` weights.
3. Install the ML extras (§1) for the GPU stages.
4. Run the full flow (long-running; resumes on restart):
   ```bash
   python -m backend.cli pipeline full
   ```
   → ingest all → GPU normalize (skill/ER/role) → build warehouse from staging →
   compute (forecasts + Job Score) → materialize marts. Seed is replaced by real,
   provenance-tagged data; the API + frontend pick it up with no code change.

For production serving, point `DATABASE_URL` at Postgres and `alembic upgrade head`.

## 5. Tests

```bash
python -m pytest backend/tests -q
```
Covers API contract conformance to the frontend shapes, the Job Score math, the
forecasting back-test/band, the résumé user-data rule, warehouse aggregation, and
auth/favourites.

## 6. Layout

```
backend/
  app/        FastAPI app, routers, schemas, services, deps, resume parsing
  core/       config (.env), db sessions (Postgres/SQLite + DuckDB), logging, security
  warehouse/  DuckDB star schema, seed generator, warehouse builder
  marts/      serving-mart models + materializer
  ingest/     connector base + checkpointing + one module per source
  ml/         job_score, forecasting (runnable) + skill_norm/ER/role_derivation (GPU)
  pipelines/  Prefect-optional orchestration flows
  alembic/    migrations (Postgres-portable)
  tests/      unit + integration
  cli.py      run any stage or the whole flow
```

See `../BUILD_LOG.md` for decisions and per-phase status, and `../FEATURE_COVERAGE.md`
for the feature matrix.
