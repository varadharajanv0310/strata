

# Overnight run — 2026-07-19 00:20:53

- `2026-07-19 00:20:53` — 🌙 START (43 stages; sample-scale extraction; populate-only, no push/publish)
- `2026-07-19 00:20:53` — 🛡 warehouse backed up → `warehouse.duckdb.bak-20260719-002053` (257MB)
- `2026-07-19 00:20:54` — ✅ **baselines** 0s — 73 anchors
- `2026-07-19 00:21:03` — ✅ **so_survey** 9s — 1886 cells
- `2026-07-19 00:24:52` — ✅ **h1b** 228s — 210 US wage cells
- `2026-07-19 00:24:52` — ✅ **ilostat** 0s — 324 earnings rows, 4 countries
- `2026-07-19 00:24:52` — ✅ **google_trends** 0s — 112 interest rows
- `2026-07-19 00:24:53` — ✅ **gh_archive** 0s — 60 demand records
- `2026-07-19 00:24:53` — ✅ **package_registries** 0s — 38 rows landed
- `2026-07-19 00:24:54` — ✅ **wikidata_occupations** 0s — 1538 rows landed
- `2026-07-19 00:24:54` — ✅ **wikipedia_pageviews** 0s — 2112 rows landed
- `2026-07-19 00:24:54` — ✅ **huggingface** 0s — 22350 rows landed
- `2026-07-19 00:24:56` — ✅ **onet_trajectory** 1s — 87 adjacency edges, 1316 skill-importance rows
- `2026-07-19 00:28:04` — ✅ **role_ladders** 187s — 14 role pay ladders
- `2026-07-19 00:28:17` — ✅ **hedonic** 12s — 60 skill premiums
- `2026-07-19 00:28:17` — ✅ **adzuna** 0s — 112 rows landed
- `2026-07-19 00:31:25` — ✅ **ambitionbox** 188s — 37 rows landed
- `2026-07-19 00:31:26` — ✅ **itjobswatch** 0s — 18 rows landed
- `2026-07-19 00:31:26` — ✅ **seek_jobstreet** 0s — 25 rows landed
- `2026-07-19 00:33:23` — ✅ **talent** 117s — 15 rows landed
- `2026-07-19 00:33:49` — ✅ **payscale** 25s — 194 rows landed
- `2026-07-19 00:34:12` — ✅ **levelsfyi** 23s — 190 rows landed
- `2026-07-19 00:34:12` — ✅ **gehalt_de** 0s — 25 rows landed
- `2026-07-19 00:34:13` — ✅ **canada_jobbank** 0s — 15 rows landed
- `2026-07-19 00:34:13` — ✅ **roberthalf** 0s — 459 rows landed
- `2026-07-19 00:37:14` — ✅ **gov_projections** 181s — 1000 rows landed
- `2026-07-19 00:37:15` — ✅ **arxiv** 0s — 48468 rows landed
- `2026-07-19 00:37:15` — ✅ **eures** 0s — 0 rows landed
- `2026-07-19 00:37:21` — ✅ **bundesagentur** 6s — 10 rows landed
- `2026-07-19 00:37:22` — ✅ **mycareersfuture** 0s — 10000 rows landed
- `2026-07-19 00:37:22` — ✅ **usajobs** 0s — 2394 rows landed
- `2026-07-19 00:37:26` — ✅ **cedefop_ovate** 4s — 0 rows landed
- `2026-07-19 00:37:27` — ✅ **hn_hiring** 0s — 74447 rows landed
- `2026-07-19 00:37:27` — ✅ **remoteok** 0s — 100 rows landed
- `2026-07-19 00:57:53` — ✅ **stack_exchange** 1226s — 3002985 rows landed
- `2026-07-19 00:57:57` — ✅ **smartrecruiters** 3s — ?
- `2026-07-19 00:58:12` — ✅ **personio** 15s — ?
- `2026-07-19 01:20:51` — ✅ **hf_jobposting** 1358s — 215745 tech postings (historical corpus)
- `2026-07-19 01:42:04` — ✅ **gpu_normalize** 1268s — derived_roles:7, employers:86, posting_dedup:1128, posting_skills:1097
    · catalog after role-derivation: **7 derived role clusters**
- `2026-07-19 03:33:01` — ✅ **llm_extract** 6652s — 2128 postings extracted, 61 abstained
- `2026-07-19 03:33:10` — ✅ **extract_validate** 5s — role_agreement=0.908883826879271, faithfulness=4.935 (n_scored=200)
    · validation: **role_agreement=0.908883826879271, faithfulness=4.935 (n_scored=200)**
- `2026-07-19 03:33:11` — ✅ **posting_attributes** 0s — 25 concordance, 17 role-attr, 70 demand-yoe rows
- `2026-07-19 03:34:36` — ✅ **role_layer** 81s — 16 roles generated (labelled generated)
- `2026-07-19 03:35:23` — ✅ **fuse** 47s — salary_person 2034, demand 1014, interest 224, salary_job 1008, dim_role 23
- `2026-07-19 03:35:30` — ✅ **materialize** 6s — 23 roles materialized to app.db

- `2026-07-19 03:35:30` — 🏁 COMPLETE in 194m
- **final warehouse:** dim_role=23 · fact_salary_person=2,034 · fact_salary_job=1,008 · fact_salary_official=76 · fact_salary_yoe_obs=652 · fact_demand=1,014 · fact_interest=224 · fact_role_outlook=10 · fact_skill_adoption=2,862,809
- **catalog:** 7 derived role clusters
- **ok (43):** baselines, so_survey, h1b, ilostat, google_trends, gh_archive, package_registries, wikidata_occupations, wikipedia_pageviews, huggingface, onet_trajectory, role_ladders, hedonic, adzuna, ambitionbox, itjobswatch, seek_jobstreet, talent, payscale, levelsfyi, gehalt_de, canada_jobbank, roberthalf, gov_projections, arxiv, eures, bundesagentur, mycareersfuture, usajobs, cedefop_ovate, hn_hiring, remoteok, stack_exchange, smartrecruiters, personio, hf_jobposting, gpu_normalize, llm_extract, extract_validate, posting_attributes, role_layer, fuse, materialize
- **degraded/skipped (0):** none
- **note:** generated descriptive layer landed to staging/generated/role_layer.json (labelled generated); its warehouse-surfacing is the documented follow-on. Nothing pushed, no site cutover.

## Corrective clustering re-run — 2026-07-19 03:42:42

- `2026-07-19 03:42:42` — 🛡 backed up → `warehouse.duckdb.bak-precluster-20260719-034242`
- `2026-07-19 04:03:17` — ✅ **gpu_normalize** 1231s — derived_roles:136, employers:86, posting_dedup:1128, posting_skills:1413
- `2026-07-19 04:04:07` — ✅ **fuse** 49s — salary_person 2034, demand 1094, interest 224, salary_job 1008, dim_role 102
- `2026-07-19 04:04:13` — ✅ **materialize** 5s — 102 roles materialized to app.db
- `2026-07-19 04:04:13` — 🏁 corrective re-run done in 21m — **catalog now 102 roles (102 derived, was 7)**
