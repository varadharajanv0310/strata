# Build ledger

Every change made while building the ecosystem (strata + InternshipOS), so any piece can be found,
understood and removed later. Newest entries go at the bottom of **Entries**. Each entry says what
was built, why, where it lives, which commits carry it, what depends on it, and how to remove it.

- **strata** = `D:\A1- Job market` (github.com/varadharajanv0310/strata). This file is the master ledger.
- **InternshipOS** = `D:\Internship tracker Chat gpt` (github.com/varadharajanv0310/InternshipOS).
  Its changes are made on a separate branch and worktree, never in the owner's working copy, and are
  listed here with the InternshipOS commit hashes.
- Plan these entries implement: [`docs/ECOSYSTEM_BUILD_SPEC.md`](ECOSYSTEM_BUILD_SPEC.md).

Removing an entry: revert its commits (`git revert <hash>`, newest first), then apply its
"To remove" notes for anything that is data rather than code (generated files, database rows,
served marts that need a rebuild).

## Index

| # | Date | Repo | Item | Spec | Commits |
|---|---|---|---|---|---|
| L-001 | 2026-07-19 | strata | Generated catalog is the product (the role spine) | — | `baaedaa` `b5f670a` |
| L-002 | 2026-07-19 | strata | Early-career salary headline + full pay progression | — | `1c6f98d` `572b53c` |
| L-003 | 2026-10-11 | strata | Ecosystem build spec | all | `d11e26d` |
| L-004 | 2026-10-11 | strata | Shared trait profiles (11 dimensions, all roles) | §5, 1b | `3ae170f` |
| L-005 | 2026-10-11 | strata | Roadmap templates (all roles) | §5, 1b | `3ae170f` `b460ddb` |
| L-006 | 2026-10-11 | strata | Role-content API + role progression fix | §2, 1a/1b | `ce694a6` |
| L-007 | 2026-10-12 | strata | Title aliases + internship classifier + `/api/roles/classify` | 1a | `ffdb62a` |
| L-008 | 2026-10-12 | strata | Merge 7 duplicate catalog roles (346 → 339) | — | `cc5a459` |
| P-001 | 2026-10-12 | strata | Rebased 42 local commits onto `origin/main`; pushed to `main` | — | process |

## Entries

### L-001 — Generated catalog is the product
**What:** strata's role catalog is generated from model knowledge (roles × 7 countries with
skills, ladder, demand, salary and career paths), stored as
`backend/warehouse/generated_catalog.json`; measured market data upgrades individual cells.
**Why:** the earlier approach (deriving roles from datasets/taxonomies) capped breadth at ~65 roles.
**Files:** `backend/warehouse/generated_catalog.json`, `backend/warehouse/generated_build.py`,
`backend/warehouse/real_build.py`, `backend/tests/test_generated_catalog.py`.
**Commits:** `baaedaa`, `b5f670a`.
**Depends on it:** everything below.
**To remove:** not removable on its own — it is the spine every later entry builds on.

### L-002 — Early-career salary headline + full pay progression
**What:** the headline salary on every role is the early-career (first stage) figure; the full
generated progression (stages by years of experience) and career trajectory are served; the UI
labels the headline "early-career" and counts roles live.
**Files:** `backend/warehouse/generated_build.py` (`fuse_generated_progression`, `_early_median`,
`_ladder_from_stages`), `backend/warehouse/real_build.py`, `backend/warehouse/build.py`,
`backend/app/services.py` (`get_role` progression fields), `src/app/explore.jsx`,
`src/app/roles.jsx`, `src/app/mobile.jsx`.
**Commits:** `1c6f98d`, `572b53c`.
**To remove:** revert both commits, then rebuild the served layer (`python -m backend.cli publish`).

### L-003 — Ecosystem build spec
**What:** the plan for one product across two repos: who owns what, free/paid defaults, removed
and deferred items, feature details, shared per-role content, build phases, rules.
**Files:** `docs/ECOSYSTEM_BUILD_SPEC.md`.
**Commits:** `d11e26d` (later edits ride along with the commits that implement them).
**To remove:** delete the file (documentation only).

### L-004 — Shared trait profiles
**What:** every catalog role scored 0-100 on 11 dimensions (interests: build, analyze, math,
design, operate, people; work style: ambiguity, interruptions, collaboration, research,
customer_contact) describing a typical early-career version of the role. The quiz will measure
students on the same dimensions so roles and students compare directly. Eight anchor roles fix
the scale. Stamped `generated`.
**Files:** `backend/warehouse/role_content.py` (dimensions, anchors, validator, merge CLI),
`backend/warehouse/generated/role_traits.json`, `backend/tests/test_role_content.py`.
**Commits:** `3ae170f`.
**Depends on it:** the per-role insights rating (spec §4.2), the quiz (§4.1), `GET /api/role-content/traits`.
**To remove:** revert `3ae170f` (also removes L-005's first half) or delete
`generated/role_traits.json` + the trait parts of `role_content.py` and the traits endpoints.

### L-005 — Roadmap templates
**What:** per role, 3-6 phases of ordered steps (prerequisites, then core, important, nice-to-have
skills), each step with topics, tools, hours and grade, each phase ending in something to build.
Every core skill is covered by name. Country-agnostic. Stamped `generated`.
**Files:** `backend/warehouse/generated/role_roadmaps.json`, roadmap validation in
`backend/warehouse/role_content.py`, `backend/tests/test_role_content.py`.
**Commits:** `3ae170f` (first 286 roles), `b460ddb` (all roles + full-coverage test).
**Depends on it:** the roadmap UI and personal roadmaps (spec §4.3), resource lists (1b).
**To remove:** delete `generated/role_roadmaps.json` and the roadmap parts of `role_content.py`,
the `roadmap` field of `GET /api/role-content/{role_id}`, and the roadmap tests.

### L-006 — Role-content API + role progression fix
**What:** read-only endpoints InternshipOS consumes: `GET /api/role-content/dimensions`,
`GET /api/role-content/traits`, `GET /api/role-content/{role_id}`. Also fixed
`GET /api/roles/{role_id}`, whose response model silently dropped `experienceCurve` and `coverage`.
**Files:** `backend/app/routers/role_content.py`, `backend/app/main.py` (router include),
`backend/app/schemas.py` (`RoleOut` fields), `backend/tests/test_role_content.py`.
**Commits:** `ce694a6`.
**To remove:** revert `ce694a6` (keep the `schemas.py` fix — it is a bug fix, not a feature).

### L-007 — Title aliases + internship classifier
**What:** generated job titles and internship "fields" (discipline names such as "Machine
Learning", "Penetration Testing", German "Softwareentwicklung") for every role; a reworked
resolver (surface weighting name > generated title > field > legacy seed > corpus table;
word-level fuzzy matching weighted by word distinctiveness; posting-noise stripping; segment and
bracket handling; umlaut folding; compound rejoining; derived corpus clusters excluded); and
`POST /api/roles/classify` (batch titles → role, confidence, runner-ups; no embedding tier).
Measured on real internship titles from the corpus: ~77% classified at high/med confidence,
~89% of those correct.
**Files:** `backend/warehouse/generated/role_aliases.json`, alias validation in
`backend/warehouse/role_content.py`, `backend/app/resolver.py`, `backend/app/services.py`
(`classify_titles`), `backend/app/routers/roles.py` (`/roles/classify`),
`backend/tests/test_resolver_titles.py`, spec §2 endpoint list.
**Commits:** `ffdb62a`.
**Depends on it:** InternshipOS internship → role mapping (Phase 1a client), matching, insights.
**To remove:** revert `ffdb62a`. The search box falls back to the previous resolver behaviour.

### L-008 — Merge 7 duplicate catalog roles (346 → 339)
**What:** seven roles existed twice under two ids with the identical name. Each removed id was
folded into a survivor (the id more career paths pointed at; higher demand broke ties): the
removed record dropped, career paths remapped, job titles and fields unioned into the survivor,
traits and roadmap of the survivor kept. The catalog records the map in `merged_ids`.

| Removed | Survivor | Name |
|---|---|---|
| `cloud-eng` | `cloud-engineer` | Cloud Engineer |
| `platform-engineer` | `platform-eng` | Platform Engineer |
| `php-developer` | `php-engineer` | PHP Developer |
| `network-security-eng` | `network-security-engineer` | Network Security Engineer |
| `tpm` | `technical-program-manager` | Technical Program Manager |
| `staff-eng` | `staff-engineer` | Staff Engineer |
| `principal-eng` | `principal-engineer` | Principal Engineer |

**Files:** `backend/warehouse/catalog_merge.py` (merge tool, `MERGES`), the catalog and the three
`generated/role_*.json` files, `backend/tests/test_generated_catalog.py`
(`test_tracked_catalog_has_no_duplicate_roles`), README role count. Served layer rebuilt with
`python -m backend.cli publish`. Pre-merge backups:
`backend/data/app.db.bak-premerge-20261012-084712`, `backend/data/warehouse.duckdb.bak-premerge-20261012-084712`
(local only, not in git).
**Commits:** `cc5a459`.
**To remove:** revert `cc5a459` (restores all 346 records and the four data files), then
`python -m backend.cli publish`.

### P-001 — Rebase and push (process, not a feature)
`origin/main` had one commit (`6c3dd15`, 2026-07-31: README screenshots, MIT licence removed,
package.json UNLICENSED) that the local `build-pass` lacked. The 42 unpushed local commits were
rebased on top of it (only never-pushed commits were rewritten; no file overlap, no conflicts),
keeping V Varadharajan as sole author and committer with no co-author trailers, then pushed to
`main` (and `build-pass`) as a fast-forward.

## Also in the 2026-10-12 push: earlier strata data-pipeline work (July)

Pre-ecosystem commits that were local and went out in the same push. Each is self-contained;
remove one with `git revert <hash>`.

| Commit | Date | What |
|---|---|---|
| `1cb3def` `29f6888` `1324973` `b0b8da1` `6f91eaf` `93d54d0` `b755663` `f481580` `156efba` | 07-05 | Salary-estimate connector fleet (ITJobsWatch, SEEK/JobStreet, Talent.com, PayScale, levels.fyi, Gehalt.de, Canada Job Bank, Robert Half) + registration |
| `b1d855e` `1a0c2c0` | 07-05 | Estimate fuse: system-aware occupation crosswalk, multi-source loader |
| `1e0923d` `1f2ea2c` | 07-05 | Corpus loader + validation reading real posting text |
| `c956315` `7a6a49b` | 07-05 | Experience-curve + coverage marts; jobscore null-median fix |
| `ae2b3c3` `8fae30f` `284531d` `1502499` `4779b51` `73d1bfd` | 07-17 | Judge fix, `collect_all --only`, run launcher, Canada outlook, Stack Exchange parser, run log |
| `348b6a8` `ad55df7` `8fb3d00` | 07-18 | HF job-posting corpus loader, SmartRecruiters + Personio connectors, data-expansion inventory |
| `f46a0e7` `ee24ff2` `7ee48a0` | 07-18 | Extraction throughput fix, decision record, sparse-LLM scope |
| `38af547` `1dd9177` `c5f8947` `9608190` `ce99d66` `5438b10` | 07-19 | ML pre-run fixes, overnight launcher, leader clustering, run logs |
