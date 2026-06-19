# strata — Scaling-Connector Build + Bounded Proof (2026-06-25)

Build-and-verify pass: turn the three hard ingestion stubs into real, runnable,
verified code, and **prove them on a SMALL bounded sample** (a few thousand
tech-only postings across 7 countries). **No full-scale run. No publish. No push.**
Everything local on `build-pass`.

The three stubs being implemented:
- `backend/ingest/cc_index.py` — `enumerate_ats_slugs()` (CC columnar/CDX index → ATS board slug universe).
- `backend/ingest/ats.py` — Greenhouse/Lever/Ashby `parse` + `fetch_fleet` (public JSON board APIs → postings).
- `backend/ml/fingerprint.py` — `cluster_fingerprints` + `dedup_postings` (composite-fingerprint clustering + cross-board dedup).

---

## Council — bounded-proof strategy (logged)

🧠 **Convened** a 3-voice council (data-eng realist · labor-market coverage expert ·
skeptic/proof-methodologist) on the fragile call: *how to build a bounded proof that
is country-balanced, tech-rich, AND honest (not curated-to-look-clean).* Verdict
(unanimous shape):

- **Two-arm proof, metrics reported SEPARATELY.**
  - **Curated arm (control):** a hand-verified, country-diverse seed of live
    tech-company ATS boards. Proves the parser + clustering mechanics and guarantees
    a usable balanced sample even if CC underdelivers. It is the *control*, not the proof.
  - **Blind CC arm (the honesty channel):** boards enumerated from the Common Crawl
    index, polled blind. Proves *discovery in the wild*. **The "ready to scale"
    verdict keys on this arm.** If only the curated arm is clean, we proved the
    parser, not the pipeline — and we say so.
- **Country balance** = diverse seed + **post-poll location→country filtering** +
  **per-country caps**. Honest expectation: US/IN over-deliver; GB/CA/AU mid (Canva,
  Shopify, Monzo carry them); SG/DE thin. Goal = *presence + clustering in all 7*,
  not parity. Report the real per-country counts and the skew ratio; don't launder it.
- **Hard caps (never-hangs):** ≤~150 boards polled, ≤200 postings/board, ~5k-posting
  global stop, per-host pacing + backoff + circuit-breaker + checkpoint (the existing
  `polite_fleet` harness), CC enumeration on a ~10-min budget, ~40-min global
  wall-clock, manifest-driven + resumable.
- **Tech-filter baked in at ingest** (reuse `tech_filter.classify`) so retail/nursing/
  sales/etc. never reach clustering — the failure mode that produced "Submarine
  Engineering" / HVAC noise last time.
- **Must-report metrics:** blind-vs-curated split · dead-board rate per vendor · raw→
  tech-filtered counts + **a sample of what the filter REMOVED** · disclosure rate per
  vendor · per-country distribution + max:min skew · #clusters above floor + the floor
  used (disclose any lowering) · noise rate · **a sample of derived role NAMES** ·
  embed-vs-lexical mode actually used.
- **Honest failure is a valid verdict:** "mechanics proven; role catalog / country
  balance not yet proven on a thin sample" is legitimate and reportable. The failure
  to avoid is a clean-looking deck built only on curated boards with no blind slice,
  no dead-board rate, no filter-removal sample, no human-readable role names.

---

## Build log — connectors implemented

- **`cc_index.py`** (`208710d`) — `enumerate_ats_slugs()` reuses the proven
  `CommonCrawlConnector` index access (CDX → columnar fallback, bounded retries) and
  extracts board slugs from captured ATS URLs. One recent crawl, per-host cap,
  wall-clock budget; `{}` gracefully if CC unreachable. Verified on CC-MAIN-2026-25:
  greenhouse 12 (job-boards host — old host is robots.txt-only), ashby 7, workday 1,
  lever sparse — ~6s. (greenhouse `for=` embed-param + robots.txt filtering fixed.)
- **`ats.py`** (`5bf5aec`) — real Greenhouse/Lever/Ashby parsers written against the
  LIVE JSON (probed). `location_to_country` maps free-text + US states + a tech-city
  dict → our 7 ISO codes. `fetch_fleet` polls boards over the `polite_fleet` harness
  (per-host pacing/backoff/circuit/checkpoint), tech-filters at ingest, caps per-board
  + global + wall-clock, tracks dead-board/raw/tech/disclosed + a removed-by-filter
  sample. Curated country-diverse seed (multinationals for spread). Verified: stripe
  496→260 tech across ALL 7 countries; 1password 52→37.
- **`fingerprint.py`** (`cbd6121`) — `cluster_fingerprints` embeds the composite
  document (title⊕skills⊕dept⊕band) on the 5080 + AgglomerativeClustering (lexical
  fallback flagged, never sold as GPU); `extract_skills` builds the skill-bag;
  `dedup_postings` MinHash over shingles blocked on (employer, country, norm-title).
  Verified on 297 real postings: embed device=cuda:0; dedup 3.7% (true reposts).
- **`tech_filter.py`** — added a VETO list (checked first) for gig/crowdwork
  ("AI Trainer", data-annotation) + retail ("Sales & Service Consultant") that rode
  the bare `ai`/`data scientist` tokens. Found leaking into clusters on the proof.

## Bounded proof — RESULTS (real numbers; floor=8, NOT a scale run)

Sample: **3,331 tech postings** (curated 2,500 + blind 831), **3,015 unique** after
dedup (9.5% collapse), in **56s**. GPU embed path confirmed: **`device=cuda:0`**
(both clusterers). Two-arm, reported separately.

**Per-vendor (curated control arm)** — dead-board + disclosure are REAL:
| vendor | boards | live | dead% | tech | disclosure% |
|---|---|---|---|---|---|
| greenhouse | 54 | 33 | 38.9 | 1,643 | 0.0 |
| ashby | 27 | 19 | 29.6 | 851 | 0.0 |
| lever | 11 | 4 | 63.6 | 6 | 0.0 |

**Per-vendor (blind CC-enumerated arm — the honesty channel):**
| vendor | boards | live | dead% | tech |
|---|---|---|---|---|
| greenhouse | 40 | 39 | **2.5** | 400 |
| ashby | 23 | 20 | 13.0 | 431 |
| lever | 0 | 0 | — | 0 |

→ The **blind arm yielded 831 tech postings with only 2.5% dead greenhouse boards** —
discovery in the wild works; CC-enumerated boards are overwhelmingly live + tech-rich.

**Disclosure = 0.0% across every vendor.** A real, important finding: ATS boards give
demand/role/skill signal but **essentially no salary** (Adzuna/SO/H-1B carry pay).

**Per-country (overall):** IN 128 · US 1,519 · GB 334 · CA 279 · AU 20 · SG 25 ·
DE 91 · (none/remote 935). **7/7 present**, but **US-skewed (skew 76:1; blind arm
258:1)**. Presence in all 7 ✓; parity ✗ — exactly as the council predicted.

**Derived role clusters (post-veto, composite-fingerprint, GPU):** 75 clusters /
1,778 noise. The top clusters are **genuine tech roles, multi-country**:
`Infrastructure Security Engineer` (US/GB/CA/DE/IN) · `AI Engineer` (6 countries) ·
`AI Engineer / Forward-Deployed` (spark+databricks, 6 countries — an *emerging* role) ·
`Software Engineer Agent` (llm, 6 countries) · `Product Designer` · `Product Manager` ·
`Offensive Security Engineer` · `Machine Learning Engineer` · `Data Scientist` ·
`Data Analyst` · `Solutions Architect` · `Member of Technical Staff (AI)`. The
composite fingerprint even split **two distinct Data Engineer clusters** (spark/airflow
vs sql/aws) — working as designed.

**Honest residuals (NOT dressed up):**
- **Tech-filter leaks (pre-veto):** `AI Trainer Freelance` (gig) + `Sales & Service
  Consultant` (Apple retail) formed clusters. The VETO fixed those; **residual** sales-
  leadership leaks survive (`Director Market Sales` via reversed-order "Director,
  Client Sales" not caught by the `sales (director)` negative), plus a few vague
  mixed clusters (`Agency`, `Strategist Agent Development`). A precision pass is needed
  before scale.
- **Country skew is heavy** (76:1; AU/SG/DE thin at 20/25/91). Balance needs per-
  country caps + a larger regional seed; the curated regional candidates (razorpay,
  meesho…) were mostly **dead on greenhouse** (38.9% dead) — they use other ATS.
- **Cluster labels are rough** (`Ai Engineer Fde Forward Deployed E`, `Software
  Engineer Agent`) — the canon-title labeller is verbose; a labelling pass is future work.
- **Lever is sparse** in the CC index (0 blind slugs) and high-dead curated — low ROI vs
  greenhouse/ashby.
- **Floor disclosure:** clusters use floor=8 (config's 200 can't be cleared on 3k
  postings). At scale the floor rises and the small noise clusters vanish.

## Verdict — are the connectors ready for a scale run?

**The three connectors are implemented, real, and proven end-to-end on the GPU:**
enumerate (cc_index) → poll + tech-filter + country-tag (ats, polite-fleet) → dedup +
cluster on cuda:0 (fingerprint) → real tech role clusters across 7 countries. The
*mechanics* are a confident flip-on. **What is NOT yet proven / needs work before a
scale run is worth launching:**
1. **Country balance** — add per-country caps + expand the regional seed (the harder
   countries need the right ATS per region), or the corpus stays US-skewed.
2. **Tech-filter precision** — one more pass (sales-leadership reverse-order, vague
   clusters) so scale clusters are clean.
3. **Cluster labelling** — a canon-label cleanup for human-readable role names.
4. **Promotion wiring** — derived clusters are *computed* but not yet wired into the
   taxonomy/`dim_role_birth` (the emergent-role miner remains a TODO).

So: **the connectors themselves are scale-ready; the corpus-quality knobs (balance,
filter precision, labelling) are tuning that should land before — or be tuned during —
the first scale run.** Honest bottom line: this is "mechanics proven on a clean small
sample; quality tuning identified and partly fixed" — not "junk dressed as success,"
and not "100% done." Everything local on `build-pass`, unpushed, nothing published.
