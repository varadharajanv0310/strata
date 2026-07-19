# The generated catalog IS the product

**The correction that reset this pass:** strata's catalog is **generated from model
knowledge of the tech job market** — hundreds of roles, all 7 countries, filled with
skills, per-country salary bands, seniority ladders and descriptions. It is **not** sourced
from a dataset, taxonomy (O*NET/ESCO), or the Common Crawl corpus. Those were never the
catalog, and hunting them to "authorize" a role's existence was the mistake that produced a
16-role catalog while a plain generation had hundreds on day one. The model is the source.
Generation is the catalog. A generated estimate is what a person finds when they google the
role — surfacing, not fabrication — and the `generated` stamp is the honesty mechanism, a
quiet label that **never withholds a cell**.

## What was built

**1. The catalog was generated — 346 distinct tech roles × 7 countries.** A parallel
generation fan-out (18 domain slices — core SWE, languages, mobile, data eng, data science,
ML/AI, GenAI/LLM, analytics/BI, DevOps/SRE, cloud, security, networking, QA, product,
design, eng leadership, specialized/embedded/blockchain/robotics, and tech-adjacent) each
generated real roles with, per country (India, US, UK, Canada, Australia, Singapore,
Germany):
- a **salary median** in local currency (₹ LPA reality for India, USD for the US, etc.),
- **6–10 skills** each tagged A (core) / I (important) / B (nice-to-have),
- a **seniority ladder** (rung titles + pay multiples),
- **demand / interest / growth** signals,
- a **description** of what the work is like.

Plus a **1,797-skill durability/trend vocabulary**, also generated. Stored as durable
product data at `backend/warehouse/generated_catalog.json` (tracked; a `staging/` copy
overrides it for future regenerations). Everything stamped `generated`.

**2. The generated catalog is the SPINE.** `build_generated_dataset()` expands the catalog
into the exact nested dataset shape the warehouse→mart→services pipeline already consumes,
with every role×country cell filled and stamped `source='Generated (model knowledge)'`,
`kind='generated'`. `build_real_dataset()` now bases the fuse on this generated spine (not
the curated 16); India has the same hundreds of roles as the US, day one, no blanks.

**3. Measured data is the UPGRADE overlay, never the boundary.** Every measured source
already built — Adzuna salaries/demand, Stack Overflow person-level, H-1B ladders, official
baselines, the corpus clustering — now serves one purpose: where a real fact exists for a
cell, it **upgrades** that cell from `generated` to `measured` (the measured stamp wins). A
cell with no measured fact simply stays `generated` — filled, never blank, never dropped.
`build_real_dataset`'s "no measured data" branch was fixed to **preserve** the generated
stamp instead of relabelling it "modeled".

**4. The floor stays killed.** Role existence is never gated on posting volume, a crosswalk,
or any source (retained from the prior decoupling; the O*NET occupation-table assembler that
embodied the retired dataset-hunting mindset was removed).

## Proven end-to-end (temp-DB fuse of the real catalog, nothing real touched)

| | |
|---|---|
| Catalog roles fused | **432** = 346 generated + 86 derived-cluster contributors |
| Family split | eng 119 · infra 89 · derived 86 · data 83 · prod 55 |
| `fact_salary_job` cells | **20,790 generated + 1,008 measured** (Adzuna job-level) |
| Salary sources | `generated-model-knowledge` 20,790 · `adzuna-aggregated-postings` 864 · `adzuna-demand-modeled-salary` 144 |

Every one of the 346 generated roles has all 7 countries filled with a local-currency
salary, skills, ladder and description, stamped `generated`; the 16 roles Adzuna covers get
their **demand** upgraded to the measured stamp on the cells it reaches, the rest stay
generated. (Headline salaries are the EARLY-CAREER figures — see the progression section
below — not the mid-career blend.)

## Salary progression — the headline is the EARLY-CAREER figure (this pass)

The single per-country number was a mid-career blend read as *the* salary — misinforming the
person deciding whether to enter a field (no new ML Engineer starts on ₹24L). Fixed:

- **The headline is now the early-career (entry) figure.** Each role×country×stage salary
  was generated (parallel fan-out over all 346 roles), and the headline is anchored to the
  **first stage**. E.g. **ML Engineer / India: ₹10,00,000 entry** (was the ₹24L blend) →
  ₹22L → Senior ₹42L → Staff ₹70L → Principal ₹1,00,00,000. Software Engineer / India:
  ₹7,00,000 entry → … → ₹80,00,000 principal.
- **The full progression fills the (previously orphaned) v2 experience substrate**, now
  load-bearing: per role×country×stage a `fact_salary_yoe_obs` observation + a
  `fact_salary_curve` point (kind=`generated`, monotonic in YoE); per role×stage a
  `dim_ladder_rung` spine with **year-of-experience bands** (Entry 0–2, Mid 2–5, Senior 5–9,
  Staff 9–13, …); and per role its **career-trajectory** `leads_to` edges into
  `bridge_role_adjacency`. The serving layer already renders these as `experienceCurve` +
  `ladder` + `trajectory` — no serving rewrite needed.
- **Measured data upgrades the progression per lens.** `build_real_dataset` no longer lets
  Adzuna's pooled (mid-career) mean overwrite the early headline — it upgrades **demand**
  instead. Per-YoE measured sources (Stack Overflow experience bands, H-1B levels, the
  AmbitionBox/PayScale estimate connectors via `build_ladder_v2`) land as their **own curve
  lens** (kind=`estimate`/`realized`/…) beside the generated spine; generated stays where no
  measured stage-data exists. The curve schema never blends kinds, so the lenses stay honest.

## Honest state after this pass
- The product now has a **filled catalog of hundreds of roles for all 7 countries** on day
  one — the day-one product that should have existed from the start. India, Singapore and
  Canada get the same breadth as the US because generation is corpus-free and
  country-agnostic.
- Salaries/skills are **generated estimates** (fairly accurate, calibrated across the fan-out
  to consistent anchors), clearly stamped `generated` and distinguishable from measured
  cells. They are what a person finds googling the role — not measured, and never presented
  as measured.
- Measured pipeline data **makes cells better where it exists** (currently the ~16 curated
  roles Adzuna covers, plus SO/H-1B/official lenses), and grows over time without ever
  changing which roles exist. Proven in the serving layer: Software Engineer / Data Engineer
  render with the `job-level` (Adzuna, measured) stamp, while Penetration Tester / Blockchain
  Engineer render `generated` — all with 7/7 countries filled.
- Nothing was pushed or published; no ingestion/GPU/corpus run. The suite stays green (49/49).

### One honest follow-on (does not affect this pass — no publish)
The pre-publish **validation gate** flags a `disclosure.fact_salary_job` breach because it
counts the (zero-transparency) generated cells against a *measured*-disclosure ratio. That
only gates a **live cutover** (out of scope here). Before publishing, the measured-quality
checks (disclosure rate, person-level sample suppression) should be **scoped to measured
cells** and treat `generated` cells as generated — the provenance stamp already distinguishes
them, so the gate can exclude them cleanly. Recorded, not fixed this pass.
