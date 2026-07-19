# Decoupling the catalog from the corpus

**Charter for this pass:** the catalog is **search-defined**, not corpus-bounded. If a
person could learn about a role from a simple web search, strata failing to show that role
is a failure. The corpus is an **input** that *fills a node's facts*, never a **boundary**
that decides *whether the node exists*. Every cell carries a provenance stamp
(measured vs researched vs generated) — a quiet label, never a gate. Run nothing this pass;
implement the code, leave it ready, commit locally.

This document is the validated design. It is grounded in a code-level audit of the live
repo (workflow `wf7mfpdic`, four parallel readers over `seed.py` / `taxonomy.py` /
`build.py` / `materialize.py` / `role_derivation.py`).

---

## 1. What the audit actually found (the model was wrong in my favour)

Two beliefs going in turned out to be **false**, which makes the fix smaller and safer:

1. **"The fuse REPLACES the curated catalog with derived clusters."**
   False. `build.py:1082` inserts derived clusters with
   `INSERT INTO dim_role ... ON CONFLICT (role_id) DO NOTHING`, under a disjoint
   `derived:<country>:<slug>` id namespace. The curated 16 are inserted first
   (`build.py:910`) and **never removed**. The `16 → 102` we saw was `16 curated + 86
   derived` — a **union already**. There is no replace to undo.

2. **"There is a `role_volume_floor` boundary AND a catalog-collapse boundary."**
   Confirmed — these are the *only* two corpus-boundaries, and both are narrow:
   - **The floor** (`role_derivation.py:198/204/227`, `config.role_volume_floor=200`):
     a cluster becomes a node *only if it clears 200 postings, per country*. On the
     US-skewed corpus this yields ~122 US clusters but **~1 each for India / Singapore /
     Canada**. This is the literal "corpus decides existence" logic.
   - **The O*NET collapse** (`taxonomy.py:313`): the O*NET zip on disk holds **1,016
     occupations / 55,120 alternate titles**, but the loader keeps only titles whose SOC
     matches one of **10 curated crosswalk anchors** — so 867 occupations collapse onto 16
     nodes *as aliases*, and ~96% of O*NET is discarded. The occupations never become
     **nodes**. This is the "corpus (here, our own 16-role crosswalk) is the boundary" logic
     applied to a taxonomy.

Everything else the correction wants **already exists**:

| Requirement | Already in code |
|---|---|
| Union of contributors, curated never lost | `ON CONFLICT DO NOTHING`, disjoint id namespaces (`build.py:1082`, `EMERGING_PREFIX`) |
| Per-cell provenance stamp | `MartRoleCountry.kind` + `.source` + `.conf` + `.is_seed` (`materialize.py:250`), node-level `family_id` |
| No-data node still surfaces (never dropped) | null-in-place cascade; a bare node appears with `countries={}` (`test_a4_bare_derived`) |
| Resolver never dead-ends | `dim_role_alias` + `match_title_to_role()` (`taxonomy.py:492`) |
| Cross-market comparability per node | `dim_role_crosswalk` (7 official systems) |
| Emergent clusters as a third contributor | `mine_emergent_roles()` → append-only `dim_role_birth` ledger |

So the decoupling is **two moves plus honest reporting**, not a rebuild.

---

## 2. The two moves

### Move A — kill the floor (retire corpus-as-boundary)

`role_derivation.py` stops using `role_volume_floor` as a market-volume gate. A cluster's
existence no longer depends on posting volume or on which country it surfaced in. The only
survivor is a tiny **denoise** threshold (drop singleton/2-member clusters that are
embedding noise, not a market signal) — a data-quality filter, explicitly *not* a market
boundary. `settings.role_volume_floor` is retired from the derivation path (kept in config
only for backward-compat, no longer load-bearing).

Consequence: India's 9k raw postings cluster into *many* roles instead of one; every
country contributes its full clustered breadth. The corpus is now a **contributor**.

### Move B — add a corpus-free taxonomy node contributor

A new assembler, `backend/warehouse/role_universe.py`, mints **nodes** (not aliases) from
the O*NET occupation tables already on disk — zero fetch, zero corpus, identical for all 7
countries including India:

- Read `Occupation Data.txt` (1,016 occupations, with official titles + descriptions).
- Filter to the **tech occupations**: SOC major group `15-*` (Computer & Mathematical) plus
  `11-3021` (Computer & Information Systems Managers) and `17-2061` (Computer Hardware
  Engineers) → **40 government-defined tech occupations**.
- **Dedup against the 10 curated SOC anchors** (a curated role already owns that SOC; the
  hand-curated node wins) → **~29 new nodes**.
- Attach **real O*NET skills** to each node from `Technology Skills.txt` (the recognized
  Hot / In-Demand tools per SOC — Python, SQL, Kubernetes, …), so a taxonomy node ships
  genuine, non-fabricated skills, not a placeholder.
- Emit each as a node dict: `role_id = onet:<slug>` (disjoint namespace), `name`, official
  `blurb` (O*NET description), `family_id='onet'`, its SOC crosswalk anchor, provenance
  `onet-occupation`.

These are admitted via the **same additive fuse** the derived clusters use
(`ON CONFLICT DO NOTHING`), plus a `dim_role_crosswalk` row (its own SOC, high confidence)
and its O*NET skills into `dim_skill` / `bridge_role_skill` (source `onet`).

The nodes this unlocks are exactly the "simple web search" roles missing today:
**Computer Systems Analysts, Database Administrators, Database Architects, Computer
Programmers, Penetration Testers, Information Security Engineers, Digital Forensics Analysts,
Blockchain Engineers, Computer Systems Engineers/Architects, IT Project Managers,
Business Intelligence Analysts, Statisticians, Operations Research Analysts, Actuaries,
Bioinformatics, Clinical Data Managers, Video Game Designers, Health Informatics
Specialists, Computer & Information Research Scientists, Network & Computer Systems
Administrators, Web Administrators, GIS Technologists, Computer User/Network Support
Specialists, Telecommunications Engineering Specialists, Computer Hardware Engineers, …**

---

## 3. The catalog node set, after the two moves

```
node set  =  union(
    curated 16          [always present, corpus-free, is_seed spine],
    O*NET tech occ ~29  [corpus-free, all 7 countries, family='onet'],
    ESCO ICT ~150-200   [corpus-free, multilingual; loader ready, file absent → 0 today],
    derived clusters    [corpus contributor; floor killed → all countries, was US-only],
    emergent (birth)    [cross-country miner; unchanged]
)
```

The node axis grows via **taxonomy union + unfloored clustering**; seniority (10) and
specialization (14) stay **orthogonal axes** (not nodes), so the browsable surface is
multiplicative without exploding the node count into near-dupes.

---

## 4. All-source cell fill + the provenance stamp

Every field of a role×country cell is stitched from a distinct fact table at materialize
time onto one `MartRoleCountry` row, and **absence is null-in-place, never a dropped cell**
(headline median cascades advertised→realized→official→None; a no-salary cell survives as
`kind='demand-only'`). The **provenance stamp already exists** and needs no schema change:

- **Node level:** `family_id` (`eng`/`data`/… curated · `onet` · `derived`) + `is_seed`.
- **Cell level:** `MartRoleCountry.kind` + `.source` + `.conf`.

The fill hierarchy, highest-trust first, each stamped:

1. **measured** — a real fact landed (Adzuna advertised · Stack Overflow realized · official
   baseline). `kind ∈ {job-level, person-level, official}`, `is_seed=false`, real source.
2. **researched** — no measured fact, but a principled non-fabricated estimate exists
   (e.g. OEWS SOC→US wage; a PPP-scaled cross-country anchor). `kind='researched'`,
   `source='researched-…'`. *Insertion points are wired-ready but fire only when the
   researched overlay is populated — see §6; not populated this pass (no fabrication).*
3. **generated** — descriptive facts a model produced (skills / blurb / seniority bands),
   already stamped `source='llm-generated'`, `kind='generated'` by `role_layer.py`. For
   O*NET nodes this pass, the descriptive layer is **real O*NET skills**, which is stronger
   than generated.
4. **no data** — nothing anywhere. Honest null-in-place; the node still appears and is
   searchable, its cells read "not enough data". This is a correct state, not a failure —
   the provenance tag says so.

The stamp is a **quiet label, never a gate**: no branch anywhere withholds a node or a cell
because its best available provenance is researched or generated.

---

## 5. India / Singapore / Canada get full breadth — by construction

The node set is now taxonomy-defined and corpus-free, so **India gets the same ~45 base
nodes as the US** on day one, plus its own unfloored clustered breadth. A node with no
measured Indian fact shows honest "not enough data" for salary/demand while still carrying
its description, skills, crosswalk and (when wired) a researched estimate — never a blank or
a US number borrowed silently. The old failure mode ("India = 1 cluster because only one
cleared the 200-posting floor") is gone: existence no longer depends on Indian posting
volume.

---

## 6. Honest consequences (what this pass does and does NOT deliver)

**Delivered now, code + committed, runs nothing:**
- The floor is retired from role derivation (Move A).
- The `role_universe` assembler mints corpus-free O*NET tech-occupation nodes with real
  skills + crosswalk + provenance (Move B), and the fuse admits them additively.
- Node- and cell-level provenance vocabulary is in place (`family='onet'`; researched/
  generated kinds recognized downstream).
- The next `build_warehouse_from_staging` (populate) run yields the decoupled catalog:
  **16 → ~45 base nodes for all 7 countries**, plus unfloored clustered contributors.

**Honest limits (no overclaim):**
- With only **on-disk** data and **no run**, the base breadth is ~45 corpus-free nodes plus
  whatever the (unfloored) corpus clusters into on the next run. It is **not** yet the
  300–600 target — that needs the **ESCO ICT file** (loader ready, file absent) and/or the
  **AmbitionBox designation sitemap** (~15,941 slugs, a single fetch), both of which are
  *fetches* and out of scope for a "run nothing" pass. The assembler is built so those drop
  in as additional node contributors with no further plumbing.
- The **researched salary fill** (OEWS SOC→wage, PPP cross-country) is **designed and its
  exact insertion points are recorded** (`build_warehouse_from_staging` overlay sibling to
  `_official_salary_rows`; the `elif researched:` branch in the `materialize.py` cascade),
  but is **not populated this pass** — populating it without the OEWS/PPP basis wired would
  be fabrication. Until then, taxonomy nodes with no measured wage read honest "not enough
  data" per country.
- Completing the **generated descriptive-layer fuse** (`role_layer.py` is a dead wire today
  — `load_role_layer()` only feeds a status string, `collect_all.py:246`) is the documented
  landing zone for descriptions/skills on nodes lacking an O*NET skill source. Deferred to
  keep this pass free of half-built generation and circular catalog dependencies.

**Nothing is run, nothing is pushed, nothing is published.** The suite stays green; the
decoupling is inert until the next populate run.
