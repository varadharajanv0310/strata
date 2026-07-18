# Extraction throughput decision — executed cascade + recommendation

*Decision: on the current stack, **stratified-adaptive ~100–250K**, not extract-all. The
binding constraint turned out to be two mis-tuned configs, now fixed; the validation gate
confirms quality holds, so the corpus can be extracted with confidence.*

## The cascade, as executed

**Step 0 — no-think.** Closed: the extractor already sends `"think": false`. No free win;
the cost was real, not reasoning-token waste.

**Step 1 — vLLM, bounded hour.** Closed with a clean abort. GPU passthrough into WSL2
Ubuntu **works** (nvidia-smi sees the 5080), but the bounded-hour premise fails: no
fittable pre-quantized `Qwen3-8B` exists (HF hits are vision variants), so the path needs a
~16GB fp16 download + vLLM Blackwell-fp8 dynamic quant + sm_120 kernels — not an hour, with
16GB VRAM capping the payoff at ~3–8×. Per the pre-declared abort → the Ollama branch.

**Envelope check — clustering at scale.** `role_derivation` is genuinely sub-quadratic
(FAISS kNN-graph, country-blocked) so the algorithm survives 870K. Two real caveats found:
`entity_resolution` dedup is O(n²) *within* (employer, country) blocks — a giant
null-`companyName` block from the HF corpus would degenerate; and `sentence_transformers`
only imports under `collect_all`'s `USE_TF=0` (else the quality-clustering path silently
falls back to title grouping). Both fixable; both confirm the downstream re-imposes limits
the GPU speed-up doesn't touch.

**Steps 2/3 — extract current sources + German validation gate.** Ran a 121-posting
German-weighted gate (60 HF-DE + 40 HF-US + 15 SmartRecruiters incl. India + 6 Personio-DE),
grading German with the **multilingual gpt-oss:20b adjudicator** (ESCO's German occupation
labels are not landed — a gap the execution surfaced — so we used the model that reads
German natively, which is more robust than brittle label-matching anyway).

## The finding that reframed everything

The whole session's timing pain was **not the model or the hardware ceiling** — it was two
configs sitting on a hardware cliff:

1. **`MAX_DESC_CHARS` 6000 → 2000.** Hard prefill cliff at ~2000 prompt tokens: ~1800 tok =
   ~5s, ~2150 tok = >90s (~17× for +350 tok), robust to `num_ctx`, flash-attention AND
   KV-cache quantization. At 6000 chars every long posting was deep in the cliff (>190s /
   hang). Fixed: the 3 longest postings that timed out now extract in 4.5/5.2/3.9s.
2. **`ollama_concurrency` 4 → 1.** Concurrency-4 batches prompts into a combined prefill
   that re-crosses the cliff — the gate's OK postings averaged **37s with 24% timing out**,
   ~8× worse than single-stream. On 16GB, concurrency 1 (~4.5s clean) is optimal.

Net: **~4.5s/posting single-stream (~800/hr)** — extraction went from months/impossible to
a tractable rate, entirely on the current stack. (An additional lever, Ollama flash-attention
+ q8 KV cache, was enabled but did NOT move the cliff — kept because it's benign.)

## Quality gate — PASSED

The 2000-char truncation and the German/2021 distribution do **not** degrade quality:
- **German role agreement 90.6%** (53 postings, multilingual adjudicator — the trustworthy
  cross-lingual number). E.g. "Webdeveloper / Frontend-Entwickler" → `frontend developer`.
- English ~87%; abstain rate 7.6% (roles-only abstain healthy).

## Run-time math on the fixed stack (~4.5s/posting single-stream)

| Option | Postings | Time (continuous) |
|---|---|---|
| **Stratified 100K** | 100,000 | **~5 days** |
| **Stratified 250K** | 250,000 | **~13 days** |
| Extract-all | 870,000 | **~45 days** |

## Recommendation

**Stratified-adaptive, ~100–250K, on the Ollama stack.** Rationale:
- Even after the ~40× fix, extract-all is ~45 days of continuous GPU (resumable, so
  spreadable over ~2 months wall-clock — but the machine can do little else meanwhile).
  Stratified gives ~all the coverage value in ~1 week.
- The corpus skew still makes stratified strictly dominant: take the thin countries whole
  (IN/SG/AU/CA), cap the fat US/DE common-role cells, extract the current sources in full.
- The gate confirmed the extractor holds on the hard (German) distribution, so we extract
  with confidence rather than betting weeks on unvalidated quality.
- Extract-all only becomes the simplicity-wins default if the stack changes (vLLM on a
  bigger card, or WSL-vLLM done properly as its own task) — not reachable in a bounded hour.

**Before any large run, two fixes still owed:** the `entity_resolution` null-employer O(n²)
guard (cap block size / MinHash blocking), and ensuring the extraction subprocess carries
`USE_TF=0` so the embedding clustering path runs.
