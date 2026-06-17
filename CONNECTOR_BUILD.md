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

## Build log

_(filled in as each connector lands + the proof runs)_
