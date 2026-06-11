# strata — Overnight Data Collection RUN_LOG

Autonomous unattended run. Build all stub connectors → run `collect_all` (sources
sequential, GPU last) → fuse into the warehouse. **Stops at the warehouse — no
marts/site/republish.** Branch `data-pipeline` (main + UI untouched). Budget ≤16h,
graceful degradation, per-stage budgets, checkpointed/resumable. Councils logged inline.

| legend | |
|---|---|
| ✅ | landed real rows (count shown) |
| ⚠️ | partial / degraded (reason) |
| ❌ | failed (reason) |
| 🧠 | council decision |

---

## 2026-06-24

- `01:11` — **RUN START.** Branch `data-pipeline` cut off `main` (main + UI branches frozen).
- `01:12` — ✅ **Test isolation fixed.** `conftest.py` now forces tests into a temp DuckDB+SQLite (`STRATA_DUCKDB_PATH`/`STRATA_DATABASE_URL`) so pytest can never reseed the persistent warehouse. Will NOT run pytest against the real warehouse during the run.
