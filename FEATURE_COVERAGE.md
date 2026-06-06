# strata — Feature Coverage

Per the locked v1 inventory (brief §5–§7). Columns:
- **Frontend** — present in the existing UI (we did **not** add/remove UI).
- **API** — a backend endpoint serves it.
- **Data** — real (ingested) / seed (representative, tagged) / computed.
- **Status** — ✅ wired end-to-end · ◑ partial · ⚠ flagged gap.

> Data is **seed** today (representative, bit-identical to the original mock, tagged
> `is_seed=true`). The ingestion + GPU pipeline is built and developer-launched to
> replace seed with real data (retires seed by design). No UI was invented for gaps.

## Explore
| Feature | Frontend | API | Data | Status |
|---|---|---|---|---|
| Explore canvas (free axes) | ✅ | `/api/dataset` | seed | ✅ |
| Market Pulse (hottest/top-pay/rising/top-score) | ✅ | `/api/explore/pulse`, `/api/dataset` | computed (marts) | ✅ |
| Interactive globe → redraws page | ✅ | (client) | seed | ✅ |

## Roles
| Feature | Frontend | API | Data | Status |
|---|---|---|---|---|
| Role search / browse | ✅ | `/api/roles?q=&family=` | seed | ✅ |
| Role Dashboard (median, salary-over-time, skills+proficiency, durability, ladder, demand-vs-interest, demand trajectory) | ✅ | `/api/roles/{id}` | seed | ✅ |
| Skill durability green→yellow→red | ✅ | role payload (`skills[].dura/trend`) | seed | ✅ |
| Demand forecast w/ confidence band | ✅ | role payload (`forecast`) | seed + **real back-tested compute available** (`ml/forecasting`) | ✅ |
| Job Score board (bare score + percentile + clickable components) | ✅ | `/api/jobscore?country=` | computed (`ml/job_score`, §4) | ✅ |

## Compare (fully unrestricted)
| Feature | Frontend | API | Data | Status |
|---|---|---|---|---|
| Role vs Role (pin up to 4) | ✅ | `/api/compare?roles=…` | seed | ✅ |
| Country vs Country (one role; all-7) | ✅ | `/api/compare?roles=…&countries=…` | seed | ✅ |
| Then vs Now | ✅ | dataset series | seed | ✅ |
| Market Mirror (country-as-market) | ✅ | `/api/countries/{code}` + dataset | seed | ✅ |
| Role convergence/divergence (option) | ✅ | dataset series | seed | ✅ |
| Nominal / PPP toggle | ✅ | `pppRate` in payload (no FX) | seed | ✅ |

## Résumé
| Feature | Frontend | API | Data | Status |
|---|---|---|---|---|
| Résumé → market value (whole profile, per country) | ✅ | client prices `/api/resume/parse` profile | computed | ✅ |
| Upload → parse (PDF/DOCX/TXT) | ✅ dropzone | `POST /api/resume/parse` | real parse | ◑ — endpoint live; the dropzone currently uses the sample profile from the API. Wiring the dropzone's file picker to POST the upload is a small, isolated frontend change (deliberately **not** invented here per §3). |
| Auto-match 3–5 roles (toggleable) | ✅ | parse profile `matchRoles` | computed | ✅ |
| Résumé A vs B (opt-in, never fabricated) | ✅ | per-résumé parse | — | ✅ user-data rule enforced backend + frontend (B only when the user adds it) |
| Recommendation engine (skills-gap, learn-next, adjacent) | ✅ | client from dataset + profile | seed | ✅ |
| Best Market for Profile | ✅ | client (PPP) | seed | ✅ |

## Countries
| Feature | Frontend | API | Data | Status |
|---|---|---|---|---|
| Per-country dashboards | ✅ | `/api/countries/{code}` | seed | ✅ |
| Pay Transparency Index | ✅ | `transparency` in payloads | seed | ✅ |

## Cross-cutting
| Feature | Frontend | API | Data | Status |
|---|---|---|---|---|
| Provenance lookup (source/sample/freshness) | ✅ confidence badge | `/api/provenance` + per-row fields | seed-tagged | ✅ |
| Confidence tiers (high/med/low) | ✅ | `conf` on every figure | seed | ✅ |
| Favourites (saved shelf) | ✅ | `/api/favourites` (authed) + localStorage (anon) | app DB | ✅ |
| Accounts (optional) | ✅ (client shelf) | `/api/auth/*` | app DB | ✅ |

## Ingestion sources (brief §6)
| Source | Module | Status |
|---|---|---|
| Common Crawl (postings spine) | `ingest/common_crawl.py` | ✅ full (cc-index → WARC byte-range → JSON-LD); developer-launched at scale |
| Adzuna (salary calibration) | `ingest/adzuna.py` | ✅ full; **skips+flags** without `ADZUNA_APP_ID/KEY` |
| SO Survey, H-1B/PERM, GH Archive, Stack Exchange, Google Trends, PyPI/npm, Lightcast, ESCO, O*NET, OECD PPP, World Bank ICP, Numbeo, BLS OEWS, company enrichment | `ingest/scaffold_sources.py` | ◑ registered, credential-graceful, documented extraction plan; promote to full extractors at run time |

## GPU pipeline (brief §4)
| Stage | Module | Status |
|---|---|---|
| Job Score (§4 composite) | `ml/job_score.py` | ✅ runnable (GPU-free), validated |
| Forecasting (back-tested + intervals) | `ml/forecasting.py` | ✅ runnable (GPU-free baseline; darts/statsmodels if installed), validated |
| Skill normalization (taxonomy NN) | `ml/skill_norm.py` | ◑ real body, needs `requirements-ml` + ingested data |
| Entity resolution (dedup) | `ml/entity_resolution.py` | ◑ real body, needs `requirements-ml` + ingested data |
| Role derivation (clustering + volume floor) | `ml/role_derivation.py` | ◑ real body, needs `requirements-ml` + ingested data |

## Known flagged gaps (decide post-build)
1. **Résumé upload wiring** — backend `POST /api/resume/parse` is complete; the frontend dropzone still shows the sample profile (from the API). Connecting the file picker to the endpoint is a small isolated change, intentionally not invented (§3).
2. **Real data** — all figures are tagged seed until the ingestion + GPU pipeline is run at scale (needs source credentials + GPU-days; configured + documented).
3. **Postgres** — local default is SQLite; production is a one-line `DATABASE_URL` swap (see BUILD_LOG D1).
