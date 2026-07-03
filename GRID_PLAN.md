# GRID_PLAN — role × country × experience × ladder (master plan)

> Council output, 2026-07-04: 6 rooms (2 live-probing source hunters, role-catalog,
> progression-ladder, experience-curve, blue-sky) + architect synthesis. The user-approved
> aggregator-estimate strategy is in effect (rows land kind=estimate, source-labeled).
> Full per-room detail incl. every probed URL/parse location: reports/council_grid_universe.json

# STRATA MASTER PLAN — Grid Completion: role × country × YoE × ladder

Synthesized from all six rooms. Grounded against `backend/warehouse/schema.py`, `backend/warehouse/taxonomy.py`, `backend/marts/models.py` (MartRolePayLadder, mart_role_ladder), `backend/analytics/promotion_ladder.py`, `backend/marts/materialize.py`.

---

## 1. NEW SOURCES — the definitive connector build order

One list, merging both hunter rooms with the three already-specced connectors. AmbitionBox (built) is extended, not rebuilt. Connectors always land staging-first (fetch-once cache), so collection can begin before the schema wave in section 3 fuses them.

**Weak-country verdicts (decisive):** CA = Canada Job Bank CSVs (primary, official) + Robert Half CA (secondary). AU = SEEK career-advice pages. SG = JobStreet SG (free rider on the SEEK connector) + MyCareersFuture API (backstop). DE = Gehalt.de (primary — only probed source with explicit DE YoE brackets) + kununu /gehalt/ (cross-check).

| # | Connector | Grid cells filled | Why this rank |
|---|---|---|---|
| 1 | **Canada Job Bank (open.canada.ca CSVs)** | CA × all NOC roles × low/median/high × 13 annual vintages, kind=official | Zero scraping — ~14 CSV downloads. Fills thinnest official gap AND patches the known gov_projections/official-baseline hole. Hours of work. |
| 2 | **ITJobsWatch** | GB × extreme role AND skill long-tail × percentiles (10/25/75/90 = junior/senior proxy) + trends + contractor day rates + skill co-occurrence | Confirmed open static HTML, no bot wall, thousands of pages. Best effort-to-value in the entire hunt. |
| 3 | **SEEK AU + JobStreet SG (one domain-generic connector)** | AU × role × state salary; SG × role × area salary; growth projections; how-to-become ladder text | Confirmed `window.__APOLLO_STATE__` JSON in static HTML. Two thinnest countries, one parser. Employer-disclosed advertised-kind, complements Adzuna. |
| 4 | **Gehalt.de** | DE × role × explicit YoE brackets (<3 / 3-6 / 7-9 / >9) × region | The only probed DE YoE axis. Slug discovery via sitemap (wrong slugs soft-redirect to JS page); parse JSON-LD + HTML, keep headless fallback. |
| 5 | **AmbitionBox extension (built connector)** — sitemap harvest + senior/lead/principal/staff/manager slug variants | IN × 15,941-designation universe (tech-filtered) × per-year YoE 1-8 × rungs above L3 | One 2.5 MB sitemap fetch enumerates the whole IN designation universe; ~160 extra cached fetches give IN the most complete empirical ladder of any country. Ignore company-salary sitemaps (charter). |
| 6 | **PayScale (specced)** | US/GB/CA/AU/SG/DE/IN × role × 5 YoE brackets, kind=estimate | The cross-country YoE backbone — closes the "IN-only per-year curve" asymmetry for the other six markets. |
| 7 | **Robert Half (US + CA)** | US/CA × ~100 curated roles incl. emerging AI titles × Low/Mid/High experience tiers | Confirmed open static tables; half-day connector; cheapest credible CA salary supplement. |
| 8 | **levels.fyi (specced — amend spec NOW to capture base/stock/bonus components, not just TC)** | 7 countries × level-ladder roles × rung × comp mix | The level-ladder source; levels get placed on the years axis only via the seniority concordance (sec. 3). Company tags pooled to role×level×country at staging — never persist. |
| 9 | **talent.com (specced)** | Query-driven long-tail across markets | Long-tail breadth; tiers enter as level labels, no invented years. |
| 10 | **MyCareersFuture API (SG govt)** | SG postings × mandatory salary × min-YoE × position level | Public JSON API, feeds the existing postings/LLM-extract machinery unchanged. Build if/when SG still looks thin after #3. |
| 11 | **ONE local browser-probe session** deciding three wave-2 builds: **Zippia** (US long-tail + YoE splits + true career-path graphs — highest prize, Cloudflare-gated), **SalaryExpert** (only single-scheme all-7-countries × any-role × entry/senior source, 403 likely UA-filterable), **StepStone.de** (DE YoE, 403) | Build whichever pass the local residential-IP probe | All three failed only from datacenter IPs — worst-case results. One session, three verdicts. |
| 12 | **kununu /gehalt/ role pages only** | DE cross-check (two independent self-report sources make DE cells defensible) | Charter caution: never company pages. |
| 13 | **NodeFlair (browser automation)** | SG × role × seniority-level pay, payslip-verified | Only if #3 + #10 leave SG thin; Cloudflare wall makes it a Playwright job. |
| Later | Malt + Upwork (freelance-rate lens), Wellfound (equity axis), ZipRecruiter (only if Zippia fails), Michael Page / Hays (only if an open XHR is found) | New employment-type/equity dimensions | After the core grid fills. |

**Permanently skipped (do not re-litigate):** Comparably, Blind (company-axis charter violation), LinkedIn Career Explorer (dead), Hired (dead), SalaryExplorer (junk), Naukri/India cluster (redundant with AmbitionBox), Indeed (bot-wall), progression.fyi (directory of company-branded ladders — use offline once as reference for the canonical rung vocabulary), CareerOneStop/Dice/Reed/BuiltIn (redundant with existing lenses or wave-1 winners), Glassdoor interview data, layoffs.fyi, OECD work-hours (blue-sky kills).

---

## 2. ROLE CATALOG — chosen design

**Target size (planning commitment):** 300–600 canonical base roles, 1,000–2,500 specializations, 30,000–60,000 alias surfaces. "Almost every tech role" is delivered at the **surface layer** — every title resolves via the resolver — not by minting a node per title. Axes (dim_seniority, dim_specialization) stay orthogonal; that is what keeps the catalog from exploding into near-dupes.

**Data model v2 (additive, no breaking migration):** `dim_role` gains `role_kind` (base|specialization), `parent_role_id`, `status` (canonical|emerging|estimate-only|extinct). `dim_role_alias` is superseded by `dim_role_surface(surface, norm, role_id, source, source_key, lang, weight, first_seen)` — `source_key` holds connector-native ids (AmbitionBox slug, ESCO conceptUri, O*NET SOC, levels.fyi family id) so every connector joins by key, never re-matches titles. `dim_role_birth` stays the append-only audit ledger.

**Enumeration feeds (in order of cost):** (1) O*NET alternate titles — the zip is already on disk, `load_onet_alternate_titles()` already parses it: zero-fetch. (2) AmbitionBox sitemap — verified single fetch, 15,941 designation slugs, gated through the existing `tech_filter.py`. (3) ESCO ICT subset (~150–200 occupations + multilingual altLabels; extend `load_esco()` to emit unmapped ICT occupations as base-node candidates instead of discarding). (4) levels.fyi taxonomy when that connector lands. (5) Zippia only as wave-2 alias enrichment.

**Admission ladder (one deterministic pipeline):** normalize + exact alias hit → alias; word-bounded containment → alias (lower weight); MiniLM cosine vs node centroids: ≥0.85 → alias, 0.70–0.85 with shared skill core → specialization of nearest node, else → base-node candidate under the emerging criteria. Every admission writes `dim_role_birth`.

**Emerging intake (4 signals + lifecycle):** derived_roles clusters (exists), AmbitionBox sitemap diffs, levels.fyi taxonomy diffs, emerging-skill clusters attached to unresolved titles. Lifecycle: candidate → emerging (servable behind badge) → canonical after 2 quarters above volume floor → or extinct (ledger keeps it). This is how "AI Agent Engineer" gets in without a human.

**Sparse-coverage rendering (prerequisite for all of the above):** materialize `mart_role_coverage(role_id, country, lens, yoe_bucket, n_datapoints, n_sources, last_updated)`; API payloads always include every resolvable role with per-cell coverage stamps; **remove the silent role-drop at `src/app/explore.jsx` lines 66 and 78 (`if (!cd) return null`)** and render neutral "no data yet" cells (distinct from zero), coverage strips on role pages, estimate-only roles with kind=estimate badges. Without this, growing the catalog blanks the UI — the exact regression ec41ca7 papered over.

---

## 3. LADDER + EXPERIENCE — one unified data model

The two rooms' proposals are merged into a single five-layer stack. Existing tables are untouched or migrated additively: `fact_salary_job` / `fact_salary_person` / `fact_salary_official` remain the three lens facts; `bridge_role_ladder` (role_id, ord, title, mult) migrates into the new rung spine as curated seed rows (mult kept as fallback); `promotion_ladder.py`'s H-1B I–IV output and `MartRolePayLadder` keep working and become one source feeding the crosswalk; `materialize.py`'s `experience_code='pooled'` limitation is superseded for this surface.

**Layer 1 — Observations (ingest truth).** `fact_salary_yoe_obs(role_id, country_code, yoe_min, yoe_max, level_label, year, median, p25, p75, currency_code, sample_size, kind, confidence, source_id, retrieved_at)`. Interval-native: AmbitionBox `?experience=N` → [N,N]; PayScale brackets → [0,1)…[20,30]; SO YearsCodePro → per-year where n≥8; Robert Half tiers → declared intervals. **Level-labeled sources (levels.fyi, talent tiers, H-1B I–IV) enter with `level_label` and NULL years — never invented years.** Each connector declares one SOURCE_YOE_MAP dict. `dim_experience` becomes display-only.

**Layer 2 — Rung spine.** `dim_ladder_rung(role_id, rung_code, ord, title, track ic|mgmt, yoe_min, yoe_max, yoe_confidence, fork_from, confidence, source_id)` with canonical vocabulary L1_entry…L6_distinguished / M1_lead…M4_director_plus. `bridge_rung_crosswalk(source_id, native_level, role_family, rung_code, weight)` (~50 hand-curated rows) maps every source's native levels onto canonical rungs — crosswalk maps LEVELS only, salaries stay per-source. `fact_rung_salary(role_id, rung_code, country_code, year, median, p25, p75, currency, sample_size, kind, source_id)` is materialized from Layer 1 through the crosswalk. IC/mgmt fork is data: `track` + `fork_from` within-role; cross-role forks (swe → eng-mgr) are `bridge_role_adjacency` edges with new `edge_type='promotion_fork'`.

**Layer 3 — Concordance (the adapter between the two axes).** `bridge_seniority_yoe(role_id, country_code, seniority, yoe_p25, yoe_p50, yoe_p75, n)` aggregated from the LLM-extracted `seniority × years_required` fields we already have — the market's own per-country definition of "senior." This is the ONLY mechanism that places level-labeled observations onto the years axis (as widened intervals feeding Layer 4), and it fills `dim_ladder_rung` yoe bounds cross-validated against H-1B level overlaps and AmbitionBox curve inflections; disagreement >2 years → confidence=low.

**Layer 4 — Fitted curves + derived metrics.** `fact_salary_curve(role_id, country_code, kind, yoe 0..20, fit_median, lo, hi, support, n_effective, method)`: weighted isotonic regression (monotone, plateau-tolerant) + PCHIP smoothing, per lens, **never blended across kinds**; hierarchical shape-borrowing (role×country → family×country → global rescaled) with shrinkage; support flags observed|interpolated|borrowed; **hard rule: no extrapolation beyond max observed YoE + 1 — the curve ends**. `fact_demand_yoe` (share of postings asking each YoE bucket — the demand lens on the experience axis). `fact_progression` (early_momentum CAGR 1→5y, plateau_year, pay_multiple_10v1, per-rung step premiums) computed only over observed|interpolated years.

**Layer 5 — Marts + API + UI.** `mart_experience_curve(role_id, country_code, payload JSON)` — one row renders the whole experience tab: per-lens curve arrays + bands, seniority-band overlay, demand histogram, momentum/plateau badges, per-point source chips. `mart_role_ladder` v2: rungs ordered with per-lens salaries, steps (abs + pct deltas, promotion_ladder.py math reused), fork card `{at, options[]}`. Endpoints: `/roles/{id}/experience?country=&years=` and `/roles/{id}/ladder?country=`. Lateral transitions ("Career Explorer"): query-time composition of `bridge_role_adjacency` (O*NET/ESCO/Wikidata edges, already loaded) + same-rung `fact_rung_salary` deltas + `bridge_role_skill_importance` skill-gap chips; optional `mart_next_moves` cache.

For IN, the AmbitionBox per-year series draws a continuous line inside the rung bands; other countries show stepped rung medians honestly until PayScale/wave-2 fills them.

---

## 4. EXTRA DIMENSIONS — what makes the cut (core/high only)

1. **Remote vs onsite pay gap** — zero new collection (llm_extract `work_arrangement` × salary); cheapest high-value item in the whole plan, do first.
2. **Demand–supply tension index** — fact_demand × fact_interest already share the grain; "crowded vs underrated" quadrant, labeled as an index with stated proxy.
3. **Education & certification ROI** — zero new collection (`education_requirement`, `certifications_required` × salary); "do I need a Masters / is CKA worth it" panel.
4. **Skill pay premium (hedonic)** — skills × salary on our own posting rows + PayScale by-skill pages; green +% badges per skill, feeds "learn this next."
5. **AI-exposure score per role** — one-time static ingest (Felten-Raj-Seamans, OpenAI, Anthropic Economic Index, ILO) keyed to SOC codes we already bridge; sources side-by-side, never blended.
6. **City dimension** — dim_city + fact_salary_job_city; AmbitionBox pages already carry per-city stats (near-zero marginal crawl for IN); COL-adjusted toggle via existing col_index.
7. **Comp composition (base/stock/bonus)** — decided NOW as a levels.fyi spec amendment (capture components, not just TC); stacked bars per ladder rung.

Freebie: **language requirements** ships inside the same posting-attributes mart pass as items 1 and 3 (field already extracted, marginal cost ~zero). **Cut/parked:** freelance-rate lens (EU-heavy coverage — parked to "later" with Malt/Upwork), gender pay gap (official-source-only, 2–3 countries — parked), interview difficulty / layoff exposure / work-hours (killed, charter or data-quality grounds).

---

## 5. BUILD ORDER — single sequenced plan, each step ships independently

**Step 0 — Honest sparse rendering (2–3 days). Ships: no more silent role drops.**
`mart_role_coverage` + per-cell "no data yet" rendering; delete the `if (!cd) return null` drops in explore.jsx. Prerequisite for everything that grows the catalog or adds sparse sources.

**Step 1 — Zero-collection analytics wave (~1 week). Ships: 5 new product surfaces from data already on disk.**
(a) Rung empirics from LLM extraction (seniority × management_scope × years_required → rung YoE bounds + advertised rung salaries); (b) `bridge_seniority_yoe` concordance; (c) `fact_demand_yoe`; (d) posting-attributes mart: remote gap + credential ROI + language share; (e) tension index; (f) AI-exposure static ingest. All pure derivation.

**Step 2 — Cheap connector wave for the four weakest countries (~1–1.5 weeks). Ships: CA/GB/AU/SG/DE grid cells.**
Job Bank CA CSVs → ITJobsWatch GB → SEEK AU/JobStreet SG (domain-generic) → Gehalt.de DE. Staging-first; Job Bank fuses immediately into fact_salary_official (also fixes the gov_projections gap).

**Step 3 — Schema v2 landing (~1 week). Ships: the unified ladder+experience substrate, IN ladder complete.**
`fact_salary_yoe_obs` + `dim_ladder_rung` + `bridge_rung_crosswalk` + `fact_rung_salary`; catalog data model v2 (`dim_role` kind/parent/status + `dim_role_surface`); migrate bridge_role_ladder seed and promotion_ladder H-1B rungs through the crosswalk; AmbitionBox sitemap harvest + senior-slug extension (~160 cached fetches); O*NET alternate-titles load (zero fetch).

**Step 4 — Specced aggregator wave (~2 weeks). Ships: cross-country YoE brackets + level ladders + comp mix.**
PayScale (7-country YoE backbone) → Robert Half US/CA → levels.fyi (with comp-mix amendment) → talent.com → MyCareersFuture if SG still thin. All write fact_salary_yoe_obs directly, kind=estimate.

**Step 5 — Curve fitting + progression metrics (~1 week). Ships: "what at 5 years?" answerable everywhere data exists.**
Isotonic+PCHIP fitter → `fact_salary_curve`; `fact_progression`; `mart_experience_curve` + `/experience` endpoint.

**Step 6 — UI wave (~1.5 weeks). Ships: the goal-grid product surfaces.**
Experience tab (curve + band + lens toggle + year scrubber + seniority overlay + demand bars); role-page Progression section (rung bands, IN per-year line, Fork card); catalog browser (family→base→specialization tree with coverage chips) + Emerging-roles rail; Career Explorer (next rung / fork / lateral moves with pay deltas + skill gaps).

**Step 7 — Probe-gated wave 2 (1 probe session + builds as unlocked).**
One local browser session probes Zippia, SalaryExpert, StepStone; build the winners (Zippia's career-path graphs enrich lateral edges; SalaryExpert fills any remaining all-7 holes as kind=estimate); kununu /gehalt/ DE cross-check; admission-ladder pipeline + emerging auto-intake (needs the enlarged surface flow from steps 3–4); NodeFlair only if SG still thin.

**Step 8 — Dimension mop-up.**
Skill pay premium (hedonic), city dimension (schema + AmbitionBox city payloads), then parked items (freelance lens, equity axis) only after the core grid is dense.

Total to a fully-shipped goal grid: roughly 9–10 weeks of sequenced work, with user-visible value landing at every step from Step 0 onward.
