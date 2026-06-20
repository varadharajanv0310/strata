"""Bounded PROOF of the scaling connectors — small sample, never a scale run.

Two-arm design (council decision):
  * CURATED arm (control): hand-picked country-diverse tech boards → proves the
    parser + clustering mechanics and guarantees a usable balanced sample.
  * BLIND arm (the honesty channel): boards enumerated from the CC index, polled
    blind → proves discovery in the wild. The "ready to scale" verdict keys on this.

Pipeline: cc_index.enumerate → ats.fetch_fleet (tech-filtered, country-tagged, capped)
→ fingerprint.dedup_postings → fingerprint.cluster_fingerprints (composite, GPU) +
role_derivation title clustering (GPU) → honest metrics. Hard caps everywhere so it
stays small + fast + never hangs. Writes staging/ats_proof/ (does NOT touch the CC
staging or the warehouse). NO publish, NO scale run.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.ingest import ats, cc_index
from backend.ml import fingerprint

log = get_logger("pipelines.prove_scaling")

OUR7 = ["IN", "US", "GB", "CA", "AU", "SG", "DE"]
MIN_CLUSTER = 8          # disclosed floor for BOTH clusterers (config floor=200 is for
#                          scale; a few-thousand sample can't clear it — see report)


def _proof_dir():
    d = settings.staging_dir / "ats_proof"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _title_cluster_gpu(postings, *, min_cluster_size=MIN_CLUSTER):
    """Run role_derivation's GPU embed title-clustering per country on the sample.

    Uses role_derivation._cluster_embed directly (the real 5080 path) so we prove the
    existing pipeline's clustering without clobbering the CC staging parquet.
    """
    from backend.ml.role_derivation import _canon_title, _cluster_embed, _have_embeddings

    mode = "embed" if _have_embeddings() else "lexical"
    by_country: dict[str, list[str]] = defaultdict(list)
    for p in postings:
        if p.get("country"):
            by_country[p["country"]].append(p.get("title", ""))
    derived = []
    for code, titles in by_country.items():
        if len(titles) < min_cluster_size:
            continue
        try:
            labels = _cluster_embed(titles)
        except Exception as e:  # noqa: BLE001
            log.warning("title-cluster %s embed failed (%s) — skip", code, e)
            continue
        members: dict[int, list[str]] = defaultdict(list)
        for t, lbl in zip(titles, labels):
            if lbl != -1:
                members[lbl].append(t)
        for mt in members.values():
            if len(mt) < min_cluster_size:
                continue
            label = Counter(_canon_title(t) for t in mt if t).most_common(1)
            derived.append({"country": code, "label": (label[0][0].title() if label else "role"),
                            "count": len(mt), "sample": sorted(set(mt))[:4]})
    derived.sort(key=lambda d: d["count"], reverse=True)
    return {"mode": mode, "n_derived": len(derived), "derived": derived}


def _per_country(postings) -> dict:
    c = Counter(p.get("country") for p in postings)
    dist = {k: c.get(k, 0) for k in OUR7}
    nz = [v for v in dist.values() if v]
    dist["_none"] = c.get(None, 0)
    dist["_skew"] = round(max(nz) / min(nz), 1) if len(nz) > 1 else None
    dist["_countries_present"] = len(nz)
    return dist


def run_proof(
    *,
    curated_cap: int = 2500,
    blind_cap: int = 2000,
    per_board_cap: int = 100,
    blind_per_vendor: int = 45,
    cc_time_cap_s: float = 480.0,
    fleet_time_cap_s: float = 720.0,
) -> dict:
    """Run the bounded two-arm proof. Returns the full report dict (also written to
    staging/ats_proof/report.json)."""
    t0 = time.time()
    log.info("=== BOUNDED SCALING PROOF — START (caps: curated≤%d, blind≤%d postings) ===",
             curated_cap, blind_cap)

    # ---- BLIND arm: enumerate slugs from the CC index (bounded) ----
    log.info("[1/5] enumerating blind ATS slugs from CC index (≤%d/host, %ds budget)…",
             blind_per_vendor + 20, int(cc_time_cap_s))
    blind_slugs = cc_index.enumerate_ats_slugs(per_host_limit=blind_per_vendor + 20,
                                               time_cap_s=cc_time_cap_s)
    blind_slugs = {v: s[:blind_per_vendor] for v, s in blind_slugs.items() if v in ats.CONNECTORS}
    n_blind_slugs = sum(len(s) for s in blind_slugs.values())
    log.info("[1/5] blind slugs: %s (total %d)", {v: len(s) for v, s in blind_slugs.items()}, n_blind_slugs)

    # ---- fetch both arms separately (so metrics stay per-arm) ----
    log.info("[2/5] polling CURATED arm (control)…")
    cur_posts, cur_stats = ats.fetch_fleet(
        ats.CURATED_SEED, arm="curated", total_cap=curated_cap, per_board_cap=per_board_cap,
        checkpoint_path=str(_proof_dir() / "fleet_curated.parquet"), time_cap_s=fleet_time_cap_s)
    log.info("[3/5] polling BLIND arm (CC-enumerated, the honesty channel)…")
    blind_posts, blind_stats = ats.fetch_fleet(
        blind_slugs, arm="blind", total_cap=blind_cap, per_board_cap=per_board_cap,
        checkpoint_path=str(_proof_dir() / "fleet_blind.parquet"), time_cap_s=fleet_time_cap_s)

    all_posts = cur_posts + blind_posts
    log.info("[3/5] landed %d tech postings (curated %d + blind %d)",
             len(all_posts), len(cur_posts), len(blind_posts))

    # ---- persist the sample (NEW subdir; does not clobber CC staging) ----
    try:
        import pandas as pd
        pd.DataFrame(all_posts).to_parquet(_proof_dir() / "postings.parquet", index=False)
    except Exception as e:  # noqa: BLE001
        log.warning("could not write proof parquet: %s", e)

    # ---- dedup ----
    log.info("[4/5] cross-board dedup…")
    dd = fingerprint.dedup_postings(all_posts)
    deduped = dd["deduped"]

    # ---- cluster: composite fingerprint (GPU) + title clustering (GPU) ----
    log.info("[5/5] clustering (composite fingerprint + title, GPU)…")
    fp = fingerprint.cluster_fingerprints(deduped, min_cluster_size=MIN_CLUSTER)
    rd = _title_cluster_gpu(deduped, min_cluster_size=MIN_CLUSTER)

    # ---- metrics ----
    def _vendor_rates(stats):
        out = {}
        for v in ats.CONNECTORS:
            s = stats.get(v, {})
            out[v] = {"boards": s.get("boards", 0), "live": s.get("live", 0),
                      "dead": s.get("dead", 0), "raw": s.get("raw", 0), "tech": s.get("tech", 0),
                      "disclosed": s.get("disclosed", 0),
                      "dead_rate": round(100 * s.get("dead", 0) / max(1, s.get("boards", 0)), 1),
                      "disclosure_rate": round(100 * s.get("disclosed", 0) / max(1, s.get("tech", 0)), 1)}
        return out

    report = {
        "elapsed_s": round(time.time() - t0, 1),
        "caps": {"curated_cap": curated_cap, "blind_cap": blind_cap,
                 "per_board_cap": per_board_cap, "min_cluster_floor": MIN_CLUSTER},
        "totals": {"tech_postings": len(all_posts), "curated": len(cur_posts),
                   "blind": len(blind_posts), "blind_slugs_enumerated": n_blind_slugs,
                   "unique_after_dedup": dd["n_unique"], "dedup_collapse_pct": dd["collapse_rate"]},
        "per_arm": {
            "curated": {"vendors": _vendor_rates(cur_stats),
                        "per_country": _per_country(cur_posts)},
            "blind": {"vendors": _vendor_rates(blind_stats),
                      "per_country": _per_country(blind_posts)},
        },
        "per_country_overall": _per_country(all_posts),
        "tech_filter_removed_sample": (cur_stats.get("_removed_sample", [])[:15] +
                                       blind_stats.get("_removed_sample", [])[:15]),
        "cluster_fingerprint": {"mode": fp["mode"], "device": fp.get("device"),
                                "n_clusters": fp["n_clusters"], "n_noise": fp.get("n_noise"),
                                "clusters": fp["clusters"][:18]},
        "cluster_title_role_derivation": {"mode": rd["mode"], "n_derived": rd["n_derived"],
                                          "derived": rd["derived"][:18]},
    }
    (_proof_dir() / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("=== PROOF COMPLETE in %.0fs — report at %s ===",
             report["elapsed_s"], _proof_dir() / "report.json")
    return report


def _print_report(r: dict) -> None:
    t = r["totals"]
    print(f"\n{'='*70}\nBOUNDED SCALING PROOF — {r['elapsed_s']}s  (floor={r['caps']['min_cluster_floor']})")
    print(f"{'='*70}")
    print(f"tech postings: {t['tech_postings']}  (curated {t['curated']} + blind {t['blind']})")
    print(f"blind slugs enumerated from CC index: {t['blind_slugs_enumerated']}")
    print(f"unique after dedup: {t['unique_after_dedup']}  (collapse {t['dedup_collapse_pct']}%)")
    for arm in ("curated", "blind"):
        a = r["per_arm"][arm]
        print(f"\n--- {arm.upper()} arm ---")
        for v, s in a["vendors"].items():
            print(f"  {v:11} boards={s['boards']:3} live={s['live']:3} dead={s['dead']:3} "
                  f"(dead {s['dead_rate']}%)  raw={s['raw']:4} tech={s['tech']:4} "
                  f"disclosure={s['disclosure_rate']}%")
        pc = a["per_country"]
        print(f"  per-country: " + " ".join(f"{k}={pc[k]}" for k in OUR7) +
              f"  | none={pc['_none']} present={pc['_countries_present']}/7 skew={pc['_skew']}")
    pc = r["per_country_overall"]
    print(f"\nOVERALL per-country: " + " ".join(f"{k}={pc[k]}" for k in OUR7) +
          f"  | none={pc['_none']} present={pc['_countries_present']}/7 skew={pc['_skew']}")
    print(f"\ntech-filter REMOVED (sample): {r['tech_filter_removed_sample'][:10]}")
    fpc = r["cluster_fingerprint"]
    print(f"\nCOMPOSITE-FINGERPRINT clusters [{fpc['mode']} {fpc['device']}]: "
          f"{fpc['n_clusters']} clusters, {fpc['n_noise']} noise")
    for c in fpc["clusters"][:12]:
        print(f"   [{c['size']:3}] {c['label'][:34]:34} {c['top_skills'][:3]} {c['countries']}")
    rdc = r["cluster_title_role_derivation"]
    print(f"\nTITLE clustering (role_derivation, {rdc['mode']}): {rdc['n_derived']} derived roles")
    for d in rdc["derived"][:12]:
        print(f"   [{d['count']:3}] {d['country']} · {d['label'][:34]:34} e.g. {d['sample'][:3]}")


if __name__ == "__main__":  # pragma: no cover
    _print_report(run_proof())
