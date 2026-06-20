# strata — scope charter (READ FIRST)

## What strata is
A **free, public web tool for the global tech job market.** A person searches **any
tech-or-adjacent role**, in **their country** (of 7: India — deep — US, UK, Canada,
Australia, Singapore, Germany), and gets **everything they need about that role** —
salary, demand, skills, durability, trajectory — visualized and **freely comparable**
across every major dimension (role vs role, country vs country, over time, by
experience).

## Why it exists
The person who built it spent months struggling to find a career path, spent money on
courses in domains that didn't fit, and couldn't choose confidently because role
information was fragmented and untrustworthy. **strata is the tool he wishes he'd had.**
The user is "past-him": capable but stuck, trying to choose a path. Everything follows:

- **Role breadth is non-negotiable.** Any role someone might consider must resolve to
  something real — *never a dead-end.* That's the exact moment the tool has to work.
- **Honesty over impressiveness.** Real data where it's real, "not enough data" where
  it's thin, never fabricated, never borrowed across countries. A confidently-wrong
  number could send someone down the wrong path — the harm strata exists to prevent.
- **The ambition is "how did a student build this"** — breadth, data depth, and a
  coherent pipeline. Not company intelligence.

**Litmus test for any feature/recommendation:** does it help past-him *choose a role*?
If not, it's at best optional. If it drifts toward companies-as-a-feature, it's wrong.

---

## ROLES ONLY — companies are NEVER a product axis
strata is about **roles, and roles only.** There are **no** company dashboards, no
"top hiring companies," no company-tier/archetype segmentation, no company comparisons
or rankings, no "where do engineers from company X go next," and **no company
enrichment** (Wikidata, GitHub orgs, Crunchbase, company metadata).

**The one allowed exception — employer as invisible internal plumbing only:** the
employer field on a posting may be used internally as (a) a **dedup key** (a company's
reposts collapse) and (b) a **tech-role filter / quality signal**. That's it. It must
**never** become a user-facing dimension, an API to browse companies, a comparison
axis, or an enrichment target. Invisible glue, not a feature.

## Explicitly OUT OF SCOPE (do not resurface these — they surfaced in the brainstorm)
These company-leaning ideas were proposed during brainstorming and are **struck**:
1. **Company-tier / archetype segmentation** — FAANG vs Series-B vs services; "Company Archetypes."
2. **Company-tier as a model fixed-effect** — adding `company-tier` to the hedonic salary regression; a "Series-B→FAANG" salary toggle. (The hedonic model uses role×seniority×country×year only — never a company term.)
3. **Employer career-trajectory graphs** — "where do engineers from company X go next / come from"; company→company transition matrices; within-named-employer promotion ladders.
4. **"Company-intelligence layer"** — the "180K–210K companies" graph; "which companies hire Rust, and how fast"; hiring-velocity per company.
5. **Company count as a headline metric** — "N companies" sold alongside roles.
6. **Company reviews / attrition** — Glassdoor/kununu/AmbitionBox sentiment as a per-company health proxy.
7. **levels.fyi comp-by-company-tier** — total comp keyed to company tier.
8. **Company-affiliation enrichment** — patent/arXiv assignee flows, Wikidata employer graph, GitHub-org inference, CNCF/Apache org-activity as a company dimension.

(Skills, demand, durability, trajectory, geography, salary — all **role-scoped** —
remain fully in scope. The promotion-ladder "value of moving up a level" signal is kept
but **role-level**, with employers pooled away and never named.)

---

## Scope cleanup performed (2026-06-25)
- **Cut** `dim_company` (warehouse schema) — orphan company dimension, never populated/read.
- **Cut** `CompanyEnrich` scaffold (Wikidata/GitHub-org company enrichment).
- **Reworked** `promotion_ladder.py` to **role-level** (SOC→role, employers pooled, never named).
- **Internalized** the employer dedup — dropped the persisted `employers.parquet` registry; dedup block key kept in memory only.
- **Kept** `department`/`team` as an internal role-clustering signal (never surfaced).
- Verified: the served layer (warehouse build, marts, API, frontend) is **company-free**.
