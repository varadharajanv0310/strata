# strata — sparse LLM-extraction plan (validated against the code)

*Reasoning + planning pass. Nothing was run. This is the code-grounded confirmation of the
decision, the load-bearing verdict, the generation + sample-extraction designs, the drop-list,
and the honest consequences. Implementation left ready + committed locally.*

## The decision (recap)

Stop running per-posting LLM extraction over the **whole** corpus (~100K–870K, ~5–45 days).
Instead: **generate** the descriptive layer per canonical role (skills, "what the role is
like" description, seniority **band** — ~87 roles); keep extraction on a **representative
validated sample** (~few thousand, hours) to produce the quotable 90.6% / 4.9 numbers; and
**drop** as product features skill-frequency-by-country, exact seniority numbers, and
per-country skill-difference surfaces.

## 1. The split — confirmed against the code

Audited every consumer of the extraction output (`load_extracted` / `postings_extracted.parquet`)
and every field the API serves (`services.assemble_dataset` → marts → warehouse). Result:

| Surfaced field | Source | Depends on full-corpus LLM? |
|---|---|---|
| role name / family / **blurb** / **skills** / **ladder** | SEED-CURATED (canonical) · DERIVED-CLUSTERING (derived roles, regex `extract_skills`) | **No** |
| median / salaryLenses | STRUCTURED (Adzuna / SO-survey / official anchors) | **No** |
| demand / postings / interest / forecast | STRUCTURED (Common Crawl raw counts / Adzuna / GH Archive / Google Trends) | **No** |
| experienceCurve / coverage | STRUCTURED-ESTIMATE (`fact_salary_yoe_obs` from AmbitionBox/PayScale/levels.fyi) — the curve fitter **ignores level-only rows and does not read the LLM seniority bridge** | **No** |
| payLadder / trajectory / importance / outlook | STRUCTURED-OFFICIAL (H-1B / O*NET / ESCO / Wikidata / BLS-EP / COPS / JSA) | **No** |
| skillPremiums | STRUCTURED (`hedonic.py` on SO **survey** zips — not extraction) | **No** |
| skillAdoption | STRUCTURED (registries / Stack Exchange / arXiv / HF / Wikipedia) | **No** |
| seniority | curated **ladder bands** + H-1B level labels (there is **no served exact-years field**) | **No** |

**`grep backend/warehouse` for `postings_extracted|load_extracted|disambiguated_role` → zero
matches.** The fuse never reads the extraction output. Salary and demand are structurally
independent of the LLM (confirmed): `fact_demand` counts **is_unique raw Common-Crawl postings**,
not extracted ones.

**The only consumers of full-corpus extraction:**
1. `analytics/posting_attributes.py:53` — reads the whole parquet → `bridge_seniority_yoe`,
   `fact_role_attributes`, `fact_demand_yoe`. **These three tables are written but read by NO
   mart and NO service** (verified: zero references in `backend/marts` + `backend/app`). They
   are computed-but-never-served, and they map **1:1 onto the features the decision drops**.
2. `extract_validate.py` — the 90.6% numbers; **already samples** (`JUDGE_SAMPLE` /
   `SELF_CONSISTENCY_SAMPLE = 200`). A sample suffices — this is decision branch (b).
3. `collect_all.py:218` — a row-counter only.

**Nothing surfaced breaks if full-corpus extraction stops.** The three extraction-derived
tables lose coverage, but they surface nothing today and are exactly what we're dropping.

## 2. Load-bearing check — breadth SURVIVES (adversarially confirmed)

`role_derivation.run()` reads `common_crawl/postings.parquet` (RAW titles/descriptions) and
clusters on the **composite fingerprint** `title ⊕ regex-skills ⊕ department ⊕ salary`.
`fingerprint.extract_skills` is a **regex over `SKILL_VOCAB`**, not the LLM. The catalog's
breadth (300–600 roles) is produced by **cheap embedding+FAISS clustering of raw postings**,
with **zero dependency** on `disambiguated_role` or `postings_extracted.parquet`. Cutting
full-corpus extraction does **not** shrink the catalog.

**One real sub-finding folded into the implementation:** `role_derivation` today reads *only*
`common_crawl/postings.parquet`, so breadth currently derives from 1,128 postings. To scale
breadth to the ~870K HF corpus (+ ATS), point it at the **full raw corpus** (the same
`POSTING_FEEDS` the extractor reads) — still LLM-free clustering. Implemented (see below).

## 3. Generation layer — design

The descriptive fields the decision wants generated **already exist as the curated layer** in
`seed.py ROLE_DEFS` (16 roles: `blurb`, `sk` = skills with A/I/B importance, `ladder` =
[title, pay-multiple]). Generation is a **refresh + expansion** of that layer to ~87 roles,
produced by the LLM (one generation per role), **not** hand-curated and **not** extracted.

- **Skills** — a clean static list per role with A/I/B importance (mirrors `sk`). Generated.
- **Description** — the "what this role is like" `blurb`. Generated.
- **Seniority band** — a generated **years range per ladder rung** (e.g. Senior = 5–8y),
  landing in the schema-defined-but-empty **`dim_ladder_rung`** (`yoe_min`/`yoe_max`/
  `yoe_confidence`) — NOT an exact number.

**Where it lives:** `backend/ml/role_layer.py` (new) generates the layer and writes
`staging/generated/role_layer.json`; a fuse loader layers it into `dim_role.blurb` /
`bridge_role_skill` / `bridge_role_ladder` / `dim_ladder_rung`, all flowing through the
existing seed→warehouse→mart→services pipeline.

**Honesty / provenance (the critical requirement):** generated content must never masquerade
as measured. Mechanism (mirrors the existing `dim_skill.taxonomy_source` / `dim_source`
convention): a `dim_source` row `code='llm-generated', default_kind='generated'`, and every
generated role's `dim_role.source_id='llm-generated'`. `is_seed` is **not** reused (wrong
lifecycle — seed is retired when real data lands; generated is durable). `services.provenance`
then reports `source='generated'` for these fields. The generation output JSON also stamps
`kind='generated'` + `generated_at` + the model id per row.

## 4. Sample-extraction path — design

Keep extraction on a **representative stratified sample**, not the corpus:
- **Size:** ~3,000 postings (config `extraction_sample_size`, default 3000) — hours at the
  ~4.5s/posting single-stream rate, not days.
- **Draw:** stratified by `(_source, country)` so it spans every feed and **includes the
  German/multilingual case** (HF-DE + Personio) — the validation stays honest across languages,
  which the 90.6% German number depends on. Deterministic seed for reproducibility.
- **Output:** the same `postings_extracted.parquet`, over the sample only; `extract_validate`
  runs on it (already sampled) → the quotable 90.6% / 4.9 numbers, now honestly described as
  *"validated structured extraction on a representative multilingual sample."*
- Implemented as `select_representative_sample()` in `llm_extract.py` + the config knob;
  `run(sample_size=...)` extracts the sample. Full-corpus extraction is simply never invoked.

## 5. Explicit drop-list (recorded in SCOPE.md)

Dropped as product features — do not surface, do not re-add:
1. **Skill-frequency-by-country** ("Stack 65% more demanded in Germany") — manufactured
   urgency, erodes credibility; a clean static skill list is more honest. (`fact_role_attributes`
   shares — unsurfaced.)
2. **Exact seniority years** ("4.56 years") — false precision; a generated **band** (3–5y) is
   more truthful. (`bridge_seniority_yoe` — unsurfaced.)
3. **Per-country skill differences** — same friction/noise; not what a path-decider wants.
4. **Full-corpus per-posting extraction** — outputs are either generatable or the noise above;
   not worth ~5–45 days. Extraction survives at **sample scale** only.
5. **Emerging-role detection via LLM over the corpus** — the big corpus is 2021-vintage (can't
   surface 2026 roles), and novel-title detection is cheaper via clustering (titles that don't
   map to the taxonomy), which role-derivation already does.

## 6. Honest consequences

- **Corpus-pipeline time:** ~5–45 days → **~hours** (a 3K sample). The GPU is now justified by
  the **hedonic** model, **embedding clustering** (role-derivation / entity-resolution), and
  the **sample extraction** — not a whole-corpus run.
- **The flex shrinks honestly:** "validated structured extraction over the whole corpus" →
  **"built and validated a structured-extraction system on a representative multilingual
  sample (90.6% role agreement vs ground truth, 4.9/5 faithfulness)."** Still a real,
  defensible GPU/interview differentiator — just not oversold.
- **Breadth (role catalog): unaffected** — derives from raw-posting clustering, now pointed at
  the full corpus (LLM-free).
- **Generated ≠ measured:** the descriptive layer is provenance-labeled `generated` and
  reported as such; it never claims to be extracted-from-postings truth.

## Implemented this pass (ready, not run)
- `select_representative_sample()` + `extraction_sample_size` config (sample-extraction path).
- `role_layer.py` — the generation module (runnable later) + its fuse loader + `llm-generated`
  provenance source.
- `role_derivation` pointed at the full raw corpus (breadth scaling, LLM-free).
- SCOPE.md drop-list. Suite green. Local commits only.
