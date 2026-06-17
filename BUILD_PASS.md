# strata — Build + Housekeeping Pass (2026-06-25)

No ingestion runs. Architecture/code built against data already in staging/the
warehouse + static taxonomy files. Two git tracks kept strictly separate.

---

## Track A — older history: diagnosed, fixed, PUSHED

**Complaint:** the data-pipeline work "doesn't appear on the remote" and "the date
distribution didn't take."

**Actual diagnosis (council-validated, not a guess):** the work was *never lost and
the dates DID redistribute*. The real fault was that it was **stranded off the
default branch**. `data-pipeline` (48 commits, 2026-06-04→23, author==committer date
on every commit, sole author, no trailers, chronological) was fully pushed to
`origin/data-pipeline`. But GitHub's default view + contribution graph only reflect
`main`, and `main` stopped at `b855760` (SO connector, 06-16). The 10 platform
commits after it (H-1B, GH Archive, collect_all, CC fixes, ml fixes, data-quality,
reports — 06-16→23) lived only on `data-pipeline` → invisible.

**Fix (council: unanimous Plan A — clean fast-forward, NOT a rewrite):** `main` was a
strict ancestor of `data-pipeline`; the diff is purely additive backend/data/docs
(16 files, zero frontend). `git merge --ff-only` + normal `git push origin main`
(`b855760..566b578`). Rejected re-dating into a strict 06-08→23 window — it would
force-push already-shared history and falsify the genuine 06-04→07 original-port
commits for a 4-day cosmetic shift.

**Result:** `origin/main` now carries the full data platform; all 4 branches in sync
(0/0); the contribution graph reflects 06-04→23. The only commits before 06-08 are
the genuine Vite/React port + backend scaffold (left truthfully dated).

---

## Track B — built this pass (LOCAL ONLY on `build-pass`, never pushed)

All verified against data already present; nothing published to the live site; the
persistent warehouse was never touched (tests use temp DuckDB+SQLite).

| # | Item | Commit | Verification |
|---|------|--------|--------------|
| 1 | Never-dead-end resolver | `70c2db8` | real 16-role db; never empty (asserted) |
| 2 | Role taxonomy / alias graph | `b83c593` | 839 aliases (725 from O*NET zip) |
| 3 | Warehouse→served wiring | `e5c2080` | isolated e2e test, 2 passed |
| 4 | Provenance layer | `e5c2080` | lineage tuple surfaces per-number |
| 5 | Hedonic salary model | `a85cc13` | n=134,270, premiums + CIs |
| 6 | H-1B promotion ladder | `3aec3cf` | 634,841 filings → 719 ladders |
| 7 | DuckDB-WASM /data console | `f4f96f1`/`ab4c740` | browser-verified, 0 errors |
| 8 | Polite-fleet harness | `a9e0b4c` | 6 offline tests, 1.7s |
| 9 | ATS / CC-index / dedup stubs | `fdd6730` | import-clean; clear TODOs |

Full suite: **23 passed** (`3c4623d`).

### 1. Never-dead-end role resolver (`backend/app/resolver.py`)
Killed the `services.list_roles` `if ql in h` empty-list bug. Three-tier cascade:
exact/normalized alias lookup → stdlib fuzzy (trigram + token-set + sequence blend,
no rapidfuzz dependency) → lazy embedding ANN over role centroids (reuses the
MiniLM/faiss path, degrades gracefully if GPU/libs absent). Confidence-driven honest
copy. New `/api/roles/resolve` + `/api/roles/typeahead` (declared before
`/roles/{role_id}` so they aren't shadowed). **Structurally cannot return empty.**

### 2. Role taxonomy / alias-graph schema (`backend/warehouse/taxonomy.py`)
Canonical-node + alias-graph model; seniority + specialization as **orthogonal axes,
not nodes**; 7-system government crosswalk structure (O*NET-SOC/UK-SOC/NOC/ANZSCO/
SSOC/KldB/ESCO, seeded with O*NET-SOC + ISCO-08); **append-only** role-birth ledger.
`normalize_surface` strips seniority but keeps `c++`/`node.js`/`ci/cd`. Loads O*NET
Alternate Titles from the staging zip; ESCO/Lightcast/crosswalk-file loaders are
graceful stubs.

### 3+4. Warehouse→served path + provenance (`backend/pipelines/publish.py`)
`publish_served()` runs staging → warehouse → taxonomy → compute (Job Score +
forecast) → marts → alias mart → provenance, atomically with heartbeats — the two
halves existed but never ran end-to-end. Provenance threads
`(source_id, snapshot_hash, transform_version, row_count, as_of)` (sha1 over the
staging snapshot + fact row-counts) into `mart_provenance`, surfaced per-number in
`/api/provenance` and the new `/api/provenance/sources`.

### 5. Hedonic skill-premium model (`backend/ml/hedonic.py`)
`log(comp_usd) ~ skills + (role × seniority × country × year) cell fixed effects`,
Ridge on the per-person SO data already in staging (reads the per-respondent skill
columns the salary aggregate throws away). The interacted cell FE absorbs PPP/price
level → a skill coefficient is its within-market marginal premium; bootstrap over
respondents for CIs. Design built once, bootstrap resamples row-indices (14s).

### 6. H-1B within-employer promotion ladder (`backend/analytics/promotion_ladder.py`)
Pure derivation on cached LCA xlsx (no download): group certified filings by
`(employer, title-family)`, rank rungs by PW_WAGE_LEVEL I-IV, median wage-step
between adjacent rungs — the dollar value of a promotion at named employers.

### 7. DuckDB-WASM /data console (`backend/marts/export_parquet.py` + `public/data.html`)
The browser *is* the warehouse: marts compiled to Parquet (role×country
range-partitioned by country) read-only; `public/data.html` runs DuckDB-WASM in-tab
over the Parquet via HTTP — real OLAP, no server. Browser-verified.

### 8. Polite-fleet harness (`backend/ingest/polite_fleet.py`)
The CC IP-block recovery generalized into composable infra: TokenBucket (per-host
pacing), CircuitBreaker, BackoffPolicy, Watchdog (global re-block trip),
ParquetCheckpoint (resumable), PoliteFleet (bounded-concurrency orchestrator). No
network itself; injectable clock → 6 offline tests. Refactor only.

### 9. Stubs (interface + TODOs only)
- `backend/ingest/ats.py` — Greenhouse/Lever/Ashby public-board-API endpoint map +
  `Posting` schema + `AtsConnector` (fetch via injected `http_get` → PoliteFleet);
  per-vendor `_parse_*` raise NotImplementedError with the documented JSON shape.
- `backend/ingest/cc_index.py` — CC columnar-index `DISTINCT url_host_name`
  address-book query sketch (DuckDB-over-S3) + ATS host suffixes.
- `backend/ml/fingerprint.py` — `composite_document` (title⊕skills⊕dept⊕salary-band)
  **implemented + verified**; clustering + MinHash dedup guts stubbed (need the
  at-scale corpus to tune).

---

## Sample outputs (proof it runs on real data)

**Resolver** (live 16-role db; never empty):
```
'SDET'           -> [high] Showing QA / Test Engineer        (exact)
'RoR developer'  -> [high] Showing Backend Engineer          (exact)
'data scientsit' -> [high] Showing Data Scientist            (fuzzy 0.93)
'appsec'         -> [med ] Closest match: Security Engineer   (fuzzy 0.80)
'blockchain wizard' -> [low] nearest roles we cover           (never empty)
```

**Hedonic marginal skill premiums** (n=134,270 SO respondents, role×sen×country×year FE, R²=0.59):
```
kubernetes  +6.4%  [5.6, 7.6]      aws         +7.4%  [5.7, 8.4]
go          +6.9%  [5.2, 7.9]      typescript  +5.6%  [3.9, 6.4]
react       +5.2%  [3.7, 6.2]      terraform   +3.0%  [2.1, 4.4]
rust        +2.8%  [1.9, 4.3]   (lower after de-confounding seniority — the point)
```

**H-1B promotion ladders** (634,841 filings → 719 ladders):
```
Google LLC · "software engineer"
   I  (entry)        $116,500
   II (qualified)    $185,000   ▲ +$68,500 (+58.8%)
   III(experienced)  $220,000   ▲ +$35,000 (+18.9%)
   IV (senior)       $267,000   ▲ +$47,000 (+21.4%)
Microsoft · "software engineering"  I $133k→II $156k→III $185k→IV $220k (+65%)
```

---

## What's left for future ingestion runs (deferred — needs a run)
- **ATS fleet ingestion** — wire `CONNECTORS` + `PoliteFleet` against live board JSON
  (parse mapping per vendor); needs the slug universe.
- **CC columnar-index slug enumeration** — the remote S3 Parquet scan (`enumerate_ats_slugs`).
- **Fingerprint clustering + cross-board dedup** — tune `min_cluster_size`/distance
  and the MinHash-LSH/cosine thresholds against the at-scale corpus.
- **Emergent-role miner** — promote dense no-match clusters to `dim_role_birth`.
- **ESCO / Lightcast / official crosswalk files** — drop the reference files in;
  loaders are ready.
- **Publishing to the live marts** — `publish_served()` against the real env (a
  deliberate, separate act; never done implicitly).

## Git state
- **Track A:** `main`/`data-pipeline`/`ui-polish`/`ui-redesign` all synced to origin.
- **Track B:** branch `build-pass` (11 commits on top of `566b578`), **local only,
  unpushed** — push is the user's explicit call.
