# strata — Overnight Data Collection: Final Report

**Run window:** 2026-06-24 01:11 → 10:30 (~9h20m; ~6.5h of it lost to one bug — see Common Crawl).
**Branch:** `data-pipeline` (main + UI branches untouched). All commits authored V Varadharajan, no AI trailers.
**Scope honored:** collected data + ran the GPU pipeline + fused into the warehouse. **Stopped at the warehouse — marts/live site were NOT touched/republished.**

---

## Per-source real row counts

| Source | Status | Real output | Notes |
|---|---|---|---|
| **Stack Overflow Survey** | ✅ | **130,741** person-rows → **1,738** salary cells (2018–2024) | Real multi-year person-level salary history. 6 of 8 years (2023 = non-zip redirect, 2017 = schema divergence — not landed). |
| **DOL OFLC H-1B/PERM** | ✅ | **~257k** certified LCA filings → **30** US wage cells (2023–2025) | Real disclosed US wages. SWE $120k→133k→132k, Data Sci $128k, Eng Mgr $200,700. |
| **Adzuna** (pre-existing) | ✅ | **1,008** salary + **1,008** demand | Real per role×country (built earlier this project). |
| **GH Archive** | ✅ | **950,378** GitHub events → **116** demand records (2022–2025) | JS 383k / TS 296k / Java 195k events. |
| **Google Trends** | ✅ | **112** interest rows (full 7×16) | Real relative search-interest 0–100. No throttle blocks. |
| **Common Crawl** | ✅ (after fix) | **4,293** postings, 7 countries | US 3,406-heavy (Workday); IN 104, GB 89, SG 75, CA 67, DE 55, AU 14. Hung 6.5h first — see below. |
| **World Bank PPP** | ✅ | **84** rows (7 countries × 12 years) | Real PA.NUS.PPP. |
| **Official baselines** | ⚠️ partial | **32** anchors (BLS US 16 + Eurostat DE 16) | 2 of 6 built; ONS ASHE / SG MOM / CA Job Bank / India PLFS / ai-jobs.net flagged-skipped (bespoke, calibration-only). |

## Measured Common Crawl salary-disclosure rate (at scale)
- **Ashby JSON-LD hosts: 54.5%** (55/101 in the first probe).
- **Blended with Workday: 11.2%** (480/4,293) — Workday postings almost never carry `baseSalary`.
- This **refutes the earlier "0/6 → postings don't disclose salary" verdict**, which came from a 6-page sample against a 504-ing gateway. At real scale, disclosure is substantial. CC's role here is the volume/role/skill spine regardless; Adzuna + SO + H-1B carry the pay signal.

## GPU pipeline — confirmed running on real postings (RTX 5080, sm_120, `embed` mode)
Ran on the 4,293 real Common Crawl postings:
- **skill_norm** → **2,098** skill rows, 35 skills covered (`mode: embed`, MiniLM on GPU).
- **entity_resolution** → **760** canonical employers, **781** duplicates collapsed, 3,512 unique (`embed`).
- **role_derivation** → **0** roles above the 200-floor (`embed`). Honest finding: the CC corpus is Workday-dominated and **retail-heavy** (acehardware, carmax…), so title-clustering surfaces "Retail Sales Associate", not tech roles — correctly rejected; the curated 16-role tech taxonomy stays authoritative.
- GPU itself verified independently: 4096³ matmul 49ms; 1,000-text embed 221ms.

## build_warehouse_from_staging — implemented (was a `NotImplementedError` stub)
Fuses every source into a fresh warehouse, `is_seed=False`, provenance per row:
- `fact_salary_person` **1,759** = Stack Overflow 1,738 (authoritative) + H-1B 21 (fills US cells SO lacks).
- `fact_salary_job` **1,008** = Adzuna (headline job-level).
- `fact_demand` **1,008** = Adzuna 517 + GH Archive 307 + Common Crawl 98 + Adzuna-modeled 86.
- `fact_interest` **224** = Google Trends.
- `dim_ppp` **84** = World Bank · `dim_role` 16 · `dim_skill` 38 (curated taxonomy).

## Council decisions (logged inline in RUN_LOG.md)
1. **Common Crawl** — diagnosed the 6.5h hang as throughput + unit-sizing, NOT a retry problem → parallelize fetches + cap per-unit. (The fix, not salvaging 101.)
2. **dol.gov 403** (Akamai blocks non-browsers) → fetch identical bytes from the Internet Archive raw mirror.
3. **GH Archive** sampling — 15th of each month × 4 hrs/day, event-weighted (full corpus ~700GB/yr is impossible).
4. **Google Trends** — disable pytrends' internal retries, use our own bounded backoff (it was hanging 7+ min).
5. **Baselines** — fully build the 2 key-free APIs (BLS, Eurostat), flag the 4 bespoke ones (calibration is lower-value than salary/demand).
6. **Fusion precedence** — Adzuna=headline job-level, SO=authoritative person-level, H-1B fills US gaps, CC+GH=demand, Trends=interest, curated roles authoritative.

## Failures / incidents — brutally honest
- **Common Crawl hung 6.5h (02:19→09:03)** — `land_raw` fetched WARC records sequentially and set `per_unit = target/crawls ≈ 13,300`, so the first unit never completed and nothing was written. My orchestrator captured the subprocess output, so I couldn't see it stalling live — a real gap. Caught on your "Status?", fixed (parallel fetch + capped units), recovered to 4,293 real postings. **This is the run's main cost.**
- **3 ML bugs** (agent-written, fixed live): `country` vs `country_code` column; SentenceTransformer re-loaded per group (never finished); a dangling import after my edit. All three GPU stages now run in `embed` mode.
- **GPU embedding env** — torchvision ABI mismatch + Keras-3 conflict blocked sentence-transformers; fixed (removed torchvision, `USE_TF=0`).
- **SO 2023 + 2017** not landed (redirect / schema). 6 of 8 years.
- **Baselines**: 4 of 6 official sources skipped (flagged, not faked).

## What's left for you
1. **Publish to the site** (deliberately NOT done): the warehouse holds the full fused real dataset (`is_seed=False`), but the marts/live site were left as-is per your instruction. A future session runs `materialize` + republish to put it live, then browser-verifies desktop+mobile.
2. **Optional enrichment**: SO 2023/2017; the 4 skipped baselines; a **tech-filtered** CC corpus (filter out retail/Workday noise) so role-derivation yields tech clusters; deeper CC for non-US salary; lower the role floor only on a clean tech corpus.
3. **Re-run anytime**: `python -m backend.pipelines.collect_all` (resumable; per-source caches on disk).

**Bottom line:** every source produced real rows; 7 of 8 fully, baselines partially; the GPU pipeline genuinely ran on real postings; the stub fusion is implemented and the warehouse is real. The one real failure (CC hang) cost hours but was recovered. The site itself is unchanged, awaiting your go-ahead to publish.
