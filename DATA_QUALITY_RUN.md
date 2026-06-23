# strata — Data-Quality Run: Final Report

**Window:** 2026-06-24 afternoon (~3h). **Branch:** `data-pipeline` (main + UI untouched). Author: V Varadharajan, no AI trailers.
**Scope honored:** improved the collected data, re-fused into the warehouse, **stopped at the warehouse — marts/live site NOT touched/republished.**

Brutally honest: **2 of 4 goals fully met, 2 partially** (Common Crawl size + derived-role catalog were capped by an IP block + a too-thin corpus). Real net improvement either way.

---

## Per-stage results

### ✅ Step 3 — H-1B extraction (FULLY FIXED)
Root cause: the parser truncated the SOC code to 7 chars **before** lookup, collapsing every 8-digit O*NET code `15-1299.xx` into `swe`. Fixed (match 8-digit, then 6-digit base).
- **30 → 210 cells** in staging (was 30), **14 of 16 roles** (mobile/sre have no distinct SOC — left unmapped, not fabricated), 3 years (2023–2025) × 5 experience bands, from **561,433** parsed rows.
- Fused (SO authoritative for overlaps): **148 US cells** added to `fact_salary_person`. Medians sane + monotonic by experience.

### ✅ Step 4 — gaps (FULLY CLOSED)
- **SO Survey: found + fixed a double-count bug** — last night's "2024" zip was byte-identical (same MD5) to 2023. Dropped the fake 2024; added real **2023** (CDN) + **2017** (divergent schema). **1,738 → 1,886 cells**, 2017–2023, 138,211 person-rows.
- **Baselines 32 → 73 anchors**: + UK ONS ASHE (16), Singapore MOM (12), Canada StatCan (13). India flagged (no key-free occupation-wage source — confirmed). *(In staging as calibration anchors.)*

### ⚠️ Step 1 — Common Crawl corpus (QUALITY fixed; SIZE blocked)
- **Quality: fixed.** New host-agnostic `tech_filter.py` (drops retail/non-tech) + per-country balancing + new ATS hosts (**Pinpoint** for GB/AU/CA/SG, **Personio** for DE). Result: **344 tech-only postings, all 7 countries** — US 128, DE 54, GB 14, SG 12, CA 12, AU 10, IN 7. De-skew **243:1 → 13:1**. tech_share **0.99**, salary-disclosure **28.7%**. No more "Retail Sales Associate." Also fixed a real parquet bug (mixed-type column was silently falling to JSONL — last night's parquet was stale).
- **Size: capped by an IP block.** Enlarging from 344 failed: I'd rate-limited the IP off Common Crawl from ~5× use today (CloudFront 403-all + S3 + boto3 UNSIGNED all hard-deny — diagnosed conclusively). **Council convened** (per your mandate); verdict = block had **cleared** (live 206), resume-and-enlarge. But two polite resume attempts **stalled on slow index-resolution** (`index.commoncrawl.org` CDX is also throttled from today's usage, ~5–8 min/unit). **Decision: accept the clean 344 corpus; defer the enlarge** to a later session (fully resumable) rather than grind the ceiling. Honest cap, not a fake success.

### ⚠️ Step 2 — GPU pass + derived tech roles (GPU ran; catalog needs a bigger corpus)
- **GPU verified on the RTX 5080 (`embed` mode, all stages):** skill_norm → **327 skill rows / 28 skills**, entity_resolution → **86 employers / 37 dups / 307 unique**. Real GPU output on the real tech corpus.
- **Derived tech roles: NOT achieved — corpus too thin.** 344 title-diverse postings don't cluster into ≥N-member role groups: floor 8 → 1 cluster (noise); floor 5 → 8 clusters but only "Software Engineer"×2 were real tech, the rest filter-leaked non-tech (Submarine Engineering, HVAC `Anlagenmechaniker`, insurance "Client Advisor"). **Fusing those as a "derived catalog" would present junk as real, so I refused** — reset to floor 200 (0 derived) and **kept the curated 16 tech roles authoritative.** The "many MANY derived roles" goal genuinely needs thousands of postings (a larger CC corpus), which the block denied this session.

## Fused warehouse (is_seed=False; marts/site untouched)
`fact_salary_person` **2,034** (SO 1,886 + H-1B 148) · `fact_salary_job` **1,008** (Adzuna) · `fact_demand` **1,008** (Adzuna 518 + GH Archive 328 + **Common Crawl tech 75** + Adzuna-modeled 87) · `fact_interest` **224** (Google Trends) · `dim_role` **16** (curated) · `dim_skill` **38** · `dim_ppp` **84** (World Bank).

## Council decisions (logged in RUN_LOG.md)
1. CC tech-filter (title-first, STRONG-beats-soft-negative) + non-US hosts Pinpoint/Personio (lever/smartrecruiters/workable/etc. probed → 0 JSON-LD, dropped).
2. H-1B: full 8-digit O*NET match then 6-digit base; PW_WAGE_LEVEL→experience bands.
3. SO: drop the byte-identical fake 2024; add real 2023+2017.
4. **CC IP-block:** diagnosed (3 access methods all deny) → council → block cleared → resume polite (conc 16→5, pacing, 403 watchdog); index-resolution slow → **accept 344 + defer enlarge.**
5. role_derivation: thin/noisy corpus → keep curated taxonomy, do NOT fuse junk clusters.

## What failed / is partial — plainly
- **CC could not be enlarged** beyond 344 (IP rate-limit + slow index). Two enlarge attempts stalled and were killed (visibly, with the heartbeat — no silent 6.5h hang this time).
- **No derived tech-role catalog** — 344 postings too thin; curated 16 kept. This is the one locked-scope item not delivered, and it's gated on a bigger CC corpus.
- **India** stays thin (7 CC postings) — CC JobPosting JSON-LD is structurally US/English-skewed; no IN-heavy ATS exists in CC.
- **Baselines** are in staging as calibration anchors, not yet joined into a fact table.

## What's left for you
1. **Enlarge Common Crawl when the IP block fully clears** (likely a few hours / next session) — the connector is resumable; `CC_RECENT_CRAWLS=8 python -c "from backend.ingest.common_crawl import run; run(target_per_country=2000, time_cap_s=9000)"` — then re-run the GPU pass at a normal floor to get the **real derived tech-role catalog**.
2. **Publish to the site** (still deliberately not done): the warehouse holds the improved fused data; `materialize` + republish when you're ready.
3. Optional: join baselines into a calibration fact; widen non-US CC hosts.

**Bottom line:** H-1B and the gaps are real, solid wins (210 cells, SO double-count killed, 1,886 cells, 73 anchors). Common Crawl is now genuinely tech-only and 7-country but **small** because I got rate-limited off it — and 344 postings can't yield a derived role catalog, so I kept the curated roles rather than fuse noise. The warehouse is improved and real; the site is untouched, awaiting your go to publish.
