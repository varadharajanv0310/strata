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
- `01:13` — **BUILD phase launched** (workflow w5lpvtz8q): 6 parallel agents building the stub connectors + consumer side, each smoke-tested for REAL rows (0 = fail):
  - `h1b` — DOL OFLC H-1B/PERM → real US wages → `staging/h1b/salary_agg.json`
  - `gh_archive` — GH Archive events → real demand → `staging/gh_archive/demand.json`
  - `google_trends` — pytrends → relative interest 0-100 → `staging/google_trends/interest.json`
  - `common_crawl` — fix dead greenhouse domain + CDX-504 columnar fallback → land postings + measure disclosure rate
  - `baselines` — BLS OEWS + Eurostat (+attempt ONS/MOM/NOC/PLFS) → calibration anchors
  - `ml_fusion` — finish skill_norm/entity_resolution/role_derivation writes + implement `build_warehouse_from_staging`
- `01:30` — **BUILD PHASE COMPLETE** (workflow w5lpvtz8q, 6 agents). Smoke results (REAL rows):
  - `h1b` ✅ **72,832** certified person-rows → 10 US wage cells (swe $135k n=52k · eng-mgr $207k · data-sci $138k). 🧠 dol.gov 403s bots → fetched identical bytes from Internet Archive raw mirror.
  - `gh_archive` ✅ **950,378** real GitHub events → 29 demand records (TypeScript/JS/Java/Python top). 🧠 sample 15th of each month × 4 hrs/day; event-weighted.
  - `google_trends` ✅ **26** real interest rows (IN 16/16, US 10). pytrends, relative 0–100. 🧠 retries=0 + own bounded backoff to stop pytrends' silent hang.
  - `baselines` ✅ **32** anchors (BLS OEWS US 16 + Eurostat DE 16). 🧠 built the 2 key-free APIs, flagged ONS/MOM/CA/PLFS/ai-jobs (calibration-only). Found+fixed a real BLS series-id bug.
  - `common_crawl` ✅ FIXED + 101 postings. 🧠 **MEASURED salary-disclosure rate = 54.5% (55/101)** on Ashby JSON-LD at scale — refutes the earlier "0/6" verdict. (agent did the work but didn't return the schema.)
  - `ml_fusion` ✅ `build_warehouse_from_staging` works: **1,748** fact_salary_person (SO 1738 + H-1B 10). 🧠 precedence: Adzuna=headline job-level, SO=authoritative person-level, H-1B corroborates US, CC+GH=demand, Trends=interest.
- `01:31` — 🔧 **GPU embedding path FIXED**: removed torchvision (torch 2.11 ABI mismatch) + `USE_TF=0` (Keras-3 conflict) → sentence-transformers embeds **1000 texts in 221ms on the RTX 5080**. The GPU pipeline will use real embeddings, not the lexical fallback.
- `01:43` — ▶ **baselines** start (budget 20m)
- `01:44` — ▶ **baselines** start (budget 20m)
- `01:44` — ✅ **baselines** 0s — **32 anchors**
- `01:45` — ▶ **h1b** start (budget 45m)
- `01:50` — ✅ **h1b** 337s — **30 US wage cells**
- `01:51` — ▶ **gh_archive** start (budget 120m)
- `02:13` — ✅ **gh_archive** 1322s — **116 demand records**
- `02:14` — ▶ **google_trends** start (budget 50m)
- `02:18` — ✅ **google_trends** 258s — **112 interest rows**
- `02:19` — ▶ **common_crawl** start (budget 480m)
- `09:03` — ⚠️ **common_crawl STALL.** Started 02:19, ~6.5h with no progress (postings stuck at the smoke's 101). Root cause: `land_raw` fetched WARC records **sequentially** + `per_unit = target/crawls ≈ 13,300` → the first unit never completed → nothing written/checkpointed. NOT a network/retry issue.
- `09:10` — 🧠 **DECISION (council-style):** fix throughput, not retry — (1) parallelize WARC fetches (ThreadPoolExecutor×16), (2) cap `per_unit`≤500 + divide by units (crawl×domain) so units complete + checkpoint incrementally, (3) fetch timeout 90→30s + 240s/unit wall-cap. Killed the hung job (API preserved).
- `09:12` — ✅ **CC fix verified:** ~60 postings/unit in ~20s → **3,734 staged postings** (US 2960, IN 98, GB 70, SG 66, CA 51, DE 41, AU 11). Disclosure 9.6% blended (Ashby 54% / Workday ~0%). Widening corpus now.
- `10:29` — ▶ **gpu_normalize** start (budget 90m)
- `10:29` — ✅ **gpu_normalize** 23s — **derived_roles:0, employers:760, posting_dedup:4293, posting_skills:2098**
- `10:29` — ▶ **fuse** start (budget 20m)
- `10:30` — ✅ **fuse** 12s — **salary_person 1759, demand 1008, interest 224, salary_job 1008, dim_role 16**
- `10:30` — ✅ **gpu_normalize** (GPU embed): skill_norm 2,098 rows · entity_resolution 760 employers/781 dups · role_derivation 0 above floor (CC corpus retail-heavy → curated tech roles authoritative).
- `10:30` — ✅ **fuse** (`build_warehouse_from_staging`): fact_salary_person 1,759 (SO 1,738 + H-1B 21) · fact_salary_job 1,008 (Adzuna) · fact_demand 1,008 (Adzuna+GH+CC) · fact_interest 224 (Trends) · dim_ppp 84 (World Bank). is_seed=False.
- `10:30` — 🏁 **RUN COMPLETE.** 7/8 sources fully real, baselines partial (2/6). GPU pipeline ran on real postings. Warehouse fused & real. **Marts/site untouched** (stopped at warehouse per instruction). See OVERNIGHT_RUN.md.

## 2026-06-24 — DATA-QUALITY RUN (ceiling ≤6h; Common Crawl hard cap 3h)
- `DQ start` — Goal: tech-only + 7-country-balanced CC corpus → real derived TECH roles; fix H-1B extraction (21 cells from 257k filings); close gaps (SO 2023/2017, baselines ONS/MOM/NOC/PLFS); re-fuse; **stop at warehouse** (no marts/site). Every long stage: visible heartbeat + incremental checkpoint + hard wall-clock cap.
- `DQ council ✅` — 3 fixes landed (workflow wc9mqy46p):
  - 🧠 **CC corpus**: NEW `tech_filter.py` (host-agnostic tech classifier, 20/20) + rewrote `common_crawl.py` (tech-filter + per-country balancing + **streaming heartbeat** + incremental writes + `time_cap_s`). Hosts: ashby + **pinpointhq** (GB/AU/CA/SG) + **personio** (DE/EU) + workday. Smoke: 344 tech postings, tech_share 0.99, all 7 countries (US128 DE54 GB14 SG12 CA12 AU10 IN7; de-skew 243:1→13:1). Fixed a real parquet bug (mixed-type col → was falling to JSONL; last night's parquet was stale). 🧠 IN/SG flagged hard-to-fill (CC JSON-LD is US/English-skewed).
  - 🧠 **H-1B**: root cause = `soc[:7]` truncation collapsed all 8-digit O*NET codes into `swe`. Fixed (match 8-digit then 6-digit). Smoke FY2025Q4: 70 cells (was 30), 14/16 roles, pooled+4 exp bands, monotonic. mobile/sre have no distinct SOC (honest gap).
  - 🧠 **Gaps**: found+fixed an SO **double-count** (the "2024" zip was byte-identical to 2023). Dropped fake 2024; added real 2023 + 2017 → **1,886 cells, 2017–2023**, 138k person-rows. Baselines → **73 anchors** (added UK ONS, SG MOM, Canada StatCan; India flagged — no open source).
- `DQ` — CC at-scale capped to 3h (time_cap_s=10500). Running quick stages then CC.
- `DQ h1b ✅` — at-scale: **210 cells** (was 30), 70/yr × 2023-2025, 14 roles × 5 exp bands, 561k rows kept. Medians sane + bands monotonic.
- `DQ CC ⚠️ IP-BLOCKED` — at-scale CC chunk hit **403 on every data.commoncrawl.org fetch**. Diagnosed conclusively: IP rate-limit block (CloudFront 403-all both UAs/crawls + S3 REST 403 + boto3 UNSIGNED AccessDenied) from ~5× CC use today. Killed the chunk. Current corpus = **344 tech-only postings, all 7 countries** (US128 DE54 GB14 SG12 CA12 AU10 IN7) — quality goal met (tech-only, retail removed), size capped by the block.
- `DQ` — 🧠 **Council `wkoxzd627`** convened (mandated for throttled source): wait-retry vs accept-344 vs alt-access. Minimal probes (also a cooldown for the block).
- `DQ CC council ✅` — verdict: **block CLEARED** (agent-1 live probe got HTTP 206 from data.commoncrawl.org during the cooldown; index host was never blocked). Vote 2:1 resume-and-enlarge over accept-344. 🧠 DECISION: resume + enlarge POLITELY — concurrency 16→5, 0.12s pacing, 403 backoff + re-block watchdog (≥50 403s → graceful stop), 70-min bounded cap, more crawls for non-US depth. Floor decided after enlarge.
- `17:46` — ▶ **fuse** start (budget 20m)
- `17:46` — ✅ **fuse** 13s — **salary_person 2034, demand 1008, interest 224, salary_job 1008, dim_role 16**
- `DQ CC enlarge ⚠️` — two polite resume attempts (recent + deep) STALLED on slow index-resolution (index.commoncrawl.org CDX also throttled today, ~5-8min/unit). Killed visibly (heartbeat-monitored — no silent hang). 🧠 DECISION: accept clean 344 tech corpus; defer enlarge (resumable) to a later session.
- `DQ GPU pass ✅⚠️` — embed mode on 344 tech corpus: skill_norm 327 rows/28 skills, entity_resolution 86 employers/37 dups. role_derivation: corpus too thin → floor8=1 noise, floor5=8 (only swe×2 real tech, rest filter-leaked non-tech) → 🧠 REFUSED to fuse junk; reset to floor200=0, kept curated 16.
- `DQ fuse ✅` — fact_salary_person **2034** (SO 1886 + H-1B 148), fact_demand 1008 (Adzuna+GH+CC-tech 75), fact_interest 224, fact_salary_job 1008, dim_role 16 curated, dim_skill 38, dim_ppp 84. is_seed=False. **Marts/site UNTOUCHED.**
- `DQ 🏁 DONE` — H-1B 30→210 ✅, SO double-count fixed + 1886 ✅, baselines 32→73 ✅, CC tech-only+7-country ✅ (size block-capped ⚠️), derived-role catalog deferred (thin corpus). See DATA_QUALITY_RUN.md.

## 2026-06-25 — BUILD + HOUSEKEEPING PASS (no ingestion runs)
Two git tracks. **Track A** = older data-pipeline work, fixed + PUSHED to origin. **Track B** = new architecture/code, committed LOCAL ONLY on branch `build-pass` (never pushed). Build is against data already in staging/the warehouse + static taxonomy files; data-dependent guts (ATS parsing, CC index queries, clustering tuning) are stubbed with TODOs. Persistent warehouse protected (tests use temp DuckDB+SQLite).

### Track A — git history diagnosis + fix (PUSHED)
- 🧠 **Diagnosis (council-validated).** The older data-pipeline work was NOT lost and the dates DID redistribute — the real fault was that it was **stranded off the default branch**. Verified state: `data-pipeline` (48 commits, **2026-06-04→23**, author==committer date on every commit, sole author, no trailers, chronological) was fully pushed to `origin/data-pipeline`. But GitHub's default view + contribution graph only reflect `main`, and `main` stopped at `b855760` (SO connector, **06-16**). The 10 platform commits after it (H-1B, GH Archive, collect_all, CC fixes, ml fixes, data-quality, reports — 06-16→23) lived only on `data-pipeline` → invisible on the default branch.
- 🧠 **Council `Track-A-fix`** (3 agents: git-safety / user-intent / release-engineer) — **unanimous Plan A**: clean fast-forward, NOT a history rewrite. `main` is a strict ancestor of `data-pipeline`; the `main..data-pipeline` diff is purely additive backend/data/docs (16 files, zero frontend). Rejected re-dating into a strict 06-08→23 window: it would force-push already-shared history and falsify the genuine 06-04→07 original-port commits for a 4-day cosmetic shift. Kept the truthful 06-04→23 spread.
- ✅ **FIXED + PUSHED.** `git checkout main && git merge --ff-only data-pipeline` (b855760→566b578, fast-forward) → `git push origin main` (normal push, no force). `origin/main` now carries the full data platform; all 4 branches in sync (0/0). Contribution graph now reflects 06-04→23.
- Sanity: `backup/*` tags present (prior rebuild evidence); dangling `6c92fcc` is a harmless pre-rebuild dup of the 06-07 build-log commit (same content lives as `ea8d9c6`); no stashes; no stranded work.

### Track B — build (local only on `build-pass`, never pushed)
- `housekeeping` — RUN_LOG Track-A record + gitignore `.claude/` `.logs/`.
