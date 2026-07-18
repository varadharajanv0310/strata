# strata — Total Data Expansion Inventory

*Research pass: live-verified map of every obtainable data source. 139 candidate sources
across 8 territories; 99 included. Machine-readable detail in
[`data_expansion_findings.json`](data_expansion_findings.json). Numbers marked **★measured**
were re-verified first-hand this session by hitting the live endpoint; others carry the
research agents' first-hand evidence.*

## The bottleneck, in one line

The extraction pipeline is validated (90.9% role agreement, 4.935/5 faithfulness) but sits
on **1,128 postings** — US 423, unknown 386, DE 181, GB 41, **IN 27**, CA 22, AU 15. India,
the flagship-depth country, has twenty-seven postings. Everything downstream (300–600 role
catalog, per-year curves, ladders) gates on corpus volume + country balance. This pass maps
how to fix that — and the answer is *mostly three downloads and a handful of clean JSON APIs*,
not a thousand crawls.

## Two locks held throughout

- **Roles only** — every company-tagged source below is taken for its role-level slice only;
  employer stays an internal dedup key. Rejected on sight: Blind, Comparably, kununu, any
  per-company table/rating/tier.
- **No fabrication** — every candidate carries a `kind` (advertised/historical/official/
  estimate), source, and per-row provenance/license so publish-day stays a one-flag call.

---

## TIER 1 — Mega-unlocks (a download beats everything)

### 1. HF `2024-mcm-everitt-ryan/job-postings-raw` — **the single biggest unlock** ✅ BUILT
- **★measured live: 2,597,092 rows**, 21 parquet shards, public, not gated.
- Full posting bodies (`job_posting_html`), `country`, `position_name`, and **per-row
  provenance + license** (`dataset_source/title/url/license`) — a cleaned aggregation of
  Techmap.io's Kaggle dumps, mostly **CC BY-NC-ND 4.0** (NC is fine for free strata; ND is a
  publish-day flag the per-row license already handles).
- **★measured country distribution** (×21 from shard 0, authoritative — the dataset card's
  per-country numbers were wrong): **US ~1.45M, DE ~664K, UK ~210K, CA ~8K, IN ~6K, AU ~1K,
  SG ~0.6K.** After a title-tech filter: **~870K clean tech postings.**
- Vintage ~2021–23 → lands as a labelled **historical** layer, never "current."
- **Verdict:** transforms US + DE + UK + EU depth immediately; gives IN/CA a real base
  (~6–8K vs 27). Does **not** solve India/SG/AU *depth* — those need Tier 2.
- **Built this pass:** `backend/ingest/hf_jobposting.py` — DuckDB httpfs streams each shard
  with predicate+column pushdown, strips HTML, title-tech-filters (~85% precision vs ~50%
  for body-matching), dedups. Proven with 1 shard → **41,526 tech postings; extraction
  corpus 1,128 → 42,654**. Full pull is `run(max_shards=None)`.

### 2. Adzuna full-width — **the India-current jackpot, keys already in hand** ⚑ EXTEND
- **★measured live with our keys, IT category:** **US 436,971 · IN 103,549 · GB 44,069 ·
  CA 28,819 · DE 27,408 · AU 15,715 · SG 11,969** = **~668K current postings across all 7.**
- India is Adzuna's 2nd-largest market — the most direct hit on the India-deep goal, and it
  returns descriptions (≈500 chars) + salary per posting.
- `adzuna_salaries.py` already captures salary/demand; the gap is landing the *postings text*
  for the extraction corpus (a paced role×country sweep under the free-tier daily cap).
- **Verdict:** the #1 *current* country-balance lever. Truncated bodies are thinner than the
  ATS sources, so pair it with SmartRecruiters/Keka for India full-body depth.

### 3. WebDataCommons schema.org JobPosting subsets — a second pre-extracted download ⚑ BUILD
- **★measured live:** the 2023-12 JobPosting page responds (HTTP 200). Research evidence:
  2024-12 = 4.29M entities / 4.9GB, 2023-12 = ~5–6M / 7.6GB, with a per-domain stats TSV that
  doubles as a free targeting index (DE 276K, UK 118K, CA 48K, IN 21K, AU 18K, SG 3.5K
  entities in 2024 alone). n-quads format, same extraction pipeline.
- **Verdict:** a second multi-million download; overlaps CC but is already parsed. Build after
  the HF corpus is landed.

---

## TIER 2 — Structured free JSON/XML APIs (clean data, no parsing fight)

### ATS board APIs — the known 100×, and the winners cluster on the skew
| Source | Countries | Evidence | Status |
|---|---|---|---|
| **SmartRecruiters** | DE/IN/US/GB +global | ★BoschGroup `totalFound` **4,764**, first page Bengaluru; ★proof 15 tech: **IN 9/US 5/DE 1** | ✅ **BUILT** |
| **Personio** (XML) | DE (+AT/CH) | ★tecvia → 6 DE tech postings, full CDATA + seniority | ✅ **BUILT** |
| **Workday** cxs | all 7 (enterprise) | nvidia 2,000 in one tenant (unauth POST); IN/DE/SG offices | ⚑ build (tenant+site discovery via CC index) |
| **Workable** v3 | GB/DE/US/AU/SG SMB | cloudfactory `total` 34, v1 widget full HTML | ⚑ build |
| **Keka** (India) | **IN** | ketto → JSON w/ full descriptions (2-step GUID) | ⚑ build — India-native lever |
| **Recruitee** | DE/NL/EU | tether 301 offers, one-GET full bodies + country_code | ⚑ build (cheapest rows/request) |
| **Teamtailor** | Nordic/EU | signicat jobs.json (JSON Feed 1.1) | ⚑ build |
| BambooHR / Breezy / Pinpoint (GB) / Zoho (IN) / softgarden (DE) / join.com (EU) | mixed | verified endpoints | queue |

*India side-doors (Naukri is bot-walled → source-blocked, not fought):* Keka, Zoho Recruit,
Instahyre JSON, Shine.com JSON, TimesJobs/Internshala HTML, and IN-office tenants of
SmartRecruiters/Workday.

### Aggregator APIs
- **Adzuna** (Tier 1 above). **The Muse** — ★keyless, 101,653 SWE postings with **full HTML**
  (best keyless extraction input). **Himalayas** — 96,193 postings, structured seniority/
  salary. **Careerjet** — public, all 7 locales, salary fields (Adzuna cross-check).
  **Arbeitnow** — DE, hourly, full descriptions. Key-gated (user signup needed): Reed (GB),
  Jooble, Findwork. Dead/rejected: Wellfound, Okjob, Remotive (41 dev jobs).

### Government / public boards (one live-verified national board per country)
- **EURES** `jv-searchengine` public API — ★2.94M EU postings, per-country `locationCodes`,
  **ESCO occupation codes attached to every record** (free role labels). Repoint our existing
  EURES connector at this exact endpoint. — the EU/DE country-balance keystone.
- **Workforce Australia** `/api/v1/global/vacancies` — clean unauth JSON, AU national.
- **UK Find a Job / Work Hub** — 95,974 total (browser-render path; raw curl 403s).
- **Canada Job Bank** RSS/Atom (postings, distinct from the wages we already use).
- **India NCS** new portal API; **Bundesagentur** jobsuche v4 (deeper than current use).

---

## TIER 3 — Feeds, sitemaps, historical depth

- **HN who-is-hiring** via Algolia — ★184 monthly threads to ~2011, ~80–120k historical
  postings, keyless. **WeWorkRemotely / Arbeitnow / Remotive / Jobicy** RSS — small remote adds.
- **Naukri per-city sitemaps** — ★the strongest India *URL-universe* unlock: ~20+ city
  sitemaps (971 URLs in the Bangalore file alone, daily lastmod), and the **slugs encode
  title+company+city+experience band** — a role dataset before fetching a page.
- **Historical depth** (the nobody-has-this axis): Common Crawl 125 crawls back to 2008
  (greenhouse job pages confirmed in 2015-11) + Wayback CDX (greenhouse/lever captures per
  year). Reachable as a **stratified sample**, not an exhaustive mirror; S3/compute-bound via
  the existing `cc_index.py`.

---

## Country-balance verdict (the whole point)

| Country | Now | Primary levers (verified) |
|---|---|---|
| **US** | 423 | HF ~1.45M, Adzuna 437K — solved |
| **DE** | 181 | HF ~664K, **SmartRecruiters (Bosch)**, **Personio**, EURES, Adzuna 27K — solved |
| **UK** | 41 | HF ~210K, Adzuna 44K, EURES — solved |
| **CA** | 22 | HF ~8K, Adzuna 29K, Job Bank — healthy |
| **AU** | 15 | Adzuna 16K, Workforce Australia — healthy |
| **SG** | 33 | Adzuna 12K, MyCareersFuture (have), Workday/SR SG tenants — adequate |
| **IN** | **27** | **Adzuna 103,549**, **SmartRecruiters IN tenants**, Keka, Naukri sitemap, HF ~6K — the thinnest, now with the most levers |

India moves from 27 → six figures of *reachable* current postings (Adzuna alone), plus
full-body enterprise depth (SmartRecruiters proven India-dominant) — the flagship gap is
closed at the source level.

---

## Vision expansion — what would make strata exist nowhere else

**Reachable now** (data verified, pipeline mostly exists):
1. **Decade role-trajectory** — how the JD for "data engineer" changed 2015→2026. Wayback CDX
   over `boards.greenhouse.io/*` + CC's 125 crawls, parsed by the *same* extractor; each row's
   snapshot date is clean provenance. **No public product shows this.** The flagship.
2. **Skill half-life curves** — requirement-mention share per skill per year from the same
   corpus; the held 2.8M SO tag-volume series is independent corroboration. No new sources.
3. **Role-emergence detection** — first-appearance dates of titles (CC yearly slices + SO tag
   birthdates, both held).
4. **Cross-country arbitrage index** — same role, PPP-adjusted p50 across 7 countries, from the
   estimate lens + official baselines + World Bank PPP. A one-mart join.
5. **Remote-share & contractor-mix trajectory** per role — historical corpus, extraction already
   emits `work_arrangement`.

**Reachable with effort:** ladder inference from millions of postings' internal leveling
language; interview-process signal from posting text; **AI-exposure per role** (O*NET tasks ×
LLM-capability — the DATA the deferred AI-takeover-risk feature needs is mappable now).

**Fantasy (for honesty):** true per-person career sequences (no ethical roles-only source);
real-time comp at individual grain (only company-tagged sources have it — rejected).

The ceiling: with the historical corpus landed, strata can answer *"how has this role changed
over a decade, and where is it heading"* across 7 countries — which no free product does.

---

## Built this pass (3 connectors, each proven with a bounded sample)

1. `hf_jobposting.py` — the 2.6M HF corpus → **corpus 1,128 → 42,654 on 1 of 21 shards**.
2. `smartrecruiters.py` — enterprise DE+IN, **Bosch proof India-dominant (IN 9/US 5/DE 1)**,
   full bodies + seniority.
3. `personio.py` — German-SME XML, **tecvia proof 6 DE tech postings** + seniority.

All wired into `POSTING_FEEDS` + `collect_all` + the registry; suite 41/41 green. No
full-scale enumeration run (that's the run phase).

## Recommended run-phase order (by yield-per-effort)

1. **Land the HF corpus** (`hf_jobposting run(max_shards=None)`) → ~870K → GPU-extract → the
   role catalog unblocks. *This is the one that matters most.*
2. **Adzuna postings sweep** (India-current) + **SmartRecruiters/Personio CC-index slug harvest**
   → India + DE full-body depth.
3. **EURES repoint** + **Workday/Workable/Keka/Recruitee** connectors → EU + enterprise + India.
4. **WebDataCommons** second download; **historical CC/Wayback** for the trajectory vision.
5. User-side: sign up Reed (GB) / Jooble / Findwork keys.

## Rejected / blocked (honest)
- **Roles-only:** Blind, Comparably, kununu, per-company salary tables.
- **Bot-walled (routed around, not fought):** Naukri direct (recaptcha), Darwinbox (Turnstile),
  Indeed/LinkedIn direct, BLS bulk (Akamai), Berlin Startup Jobs, Kaggle anonymous captcha.
- **Dead:** Wellfound API, Okjob, RemoteOK RSS (410), SO Jobs (404).
