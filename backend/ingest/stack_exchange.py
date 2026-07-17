"""Stack Exchange data dump — the skill **durability / emergence** axis plus skill
**adjacency**, mined from Stack Overflow's full question history (council source: the
long-memory signal nobody else in the fleet carries).

Every other connector sees only the recent present (live job boards, this year's
GH Archive sample). Stack Overflow's public data dump reaches back to **2008**, and
each *question* is tagged with the technologies it concerns. The monthly count of
questions per tag is therefore a 17-year time series of *developer attention* per
skill — which lets us separate a **durable** skill (steady question volume for a
decade: SQL, Python) from an **emerging** one (a hockey-stick from near-zero: Rust,
Kubernetes) from a **fading** one (a long decline: jQuery, Flash). That durability /
emergence axis is exactly what ``fact_skill_adoption`` needs and what spot job-board
demand cannot give. As a bonus, the **co-occurrence of tags on the same question**
is a clean skill-adjacency graph ("people who ask about React also ask about
TypeScript") — the same shape strata uses to draw skill neighbourhoods.

Obtained from the **Internet Archive** mirror of the official Stack Exchange data
dump (https://archive.org/details/stackexchange), files ``stackoverflow.com-Posts.7z``
and ``stackoverflow.com-Tags.7z``, licensed **CC-BY-SA 4.0** — the legitimate,
intended-for-research distribution channel (no scraping). The Posts archive is
**huge** (~20GB compressed, ~100GB+ XML), so ``land_raw`` streams the download with a
flushing heartbeat and the on-disk archive IS the checkpoint. ``build_staging`` parses
``Posts.xml`` row by row (``PostTypeId=1`` = questions only: ``Tags`` + ``CreationDate``
→ ``YYYY-MM``) into monthly per-tag question volume and tag co-occurrence pairs.

GLOBAL signal — Stack Overflow has no per-country attribution on questions, so every
row lands ``country=""`` (global), never faked geography. ROLES-ONLY: tags are skills;
no employer/org data exists in this source. No credentials. ``.7z`` extraction needs
``py7zr`` — imported lazily and flagged (not crashed) if absent. Coded for the later
run; not executed in this pass.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from collections import defaultdict
from itertools import combinations

from backend.core.config import settings
from backend.core.logging import get_logger

log = get_logger("ingest.stack_exchange")

# Internet Archive item + the two members we need from inside it. The dump is
# published as a collection of per-site .7z archives; Stack Overflow's are split out
# into Posts / Tags (others — Comments, Users, Votes — we don't touch: roles-only,
# no PII). The IA "download" path serves the individual file from the item.
IA_ITEM = "stackexchange"
IA_BASE = f"https://archive.org/download/{IA_ITEM}"
DUMP_FILES = {
    "posts": "stackoverflow.com-Posts.7z",   # ~20GB compressed — the big one
    "tags":  "stackoverflow.com-Tags.7z",    # small — tag id/name/count catalogue
}
HEADERS = {"User-Agent": "strata/1.0 (+research; roles-only job-market explorer)"}

# Wall-clock budget for the (huge) Posts.7z download. MUST stay below the
# collect_all subprocess budget for this stage (21600s, see pipelines/collect_all.py)
# so a capped run stops itself and leaves the .part behind cleanly, rather than being
# SIGKILLed mid-write with no checkpoint. ~600s of headroom is left for parse + teardown.
STAGE_TIME_CAP_S = 21000.0

# Only count tags we can plausibly tie to a tracked technology skill. The dump has
# ~60k tags (many one-off / meta); the warehouse fuse does the tag→skill crosswalk,
# so here we land the RAW tag string and a generous volume floor, and let the fuse
# decide. We DO drop obvious meta/noise so the adjacency graph stays clean.
_META_TAGS = {
    "beginner", "homework", "discussion", "subjective", "not-programming-related",
    "career-development", "tags", "faq", "off-topic",
}

# A question with this many tags is almost always a mis-tagged grab-bag; skip it for
# co-occurrence (it would create a dense junk clique) but still count each tag's volume.
_MAX_TAGS_FOR_ADJ = 5


def _staging_dir():
    d = settings.staging_dir / "stack_exchange"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _raw_dir():
    d = _staging_dir() / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _volume_file():
    return _staging_dir() / "tag_volume.json"


def _adjacency_file():
    """Tag co-occurrence graph — INTENTIONALLY STAGED FOR LATER.

    ``tag_adjacency.json`` is computed and landed but has no warehouse reader yet: the
    fuse currently consumes only ``tag_volume.json`` (the durability/emergence axis).
    The co-occurrence pairs are a clean skill-adjacency signal earmarked for a future
    skill-neighbourhood feature (see this module's header). It is *cheap to produce
    alongside* the volume pass (same single SAX sweep), so we keep it staged rather
    than recompute it later — this is a known, deliberate parked artifact, not an orphan.
    """
    return _staging_dir() / "tag_adjacency.json"


# ---------------------------------------------------------------------------
# land_raw — stream the (huge) .7z archives to disk; the file IS the checkpoint
# ---------------------------------------------------------------------------
def _download_file(name: str, fname: str, force: bool, time_cap_s: float,
                   chunk: int = 1 << 20) -> str | None:
    """Stream one dump member to staging/stack_exchange/raw/<fname>.

    The on-disk archive is the checkpoint: if it already exists (and is non-trivially
    sized) and not ``force``, we skip the download entirely. Streams in 1MiB chunks
    with a flushing heartbeat roughly every ~256MiB so an unattended multi-hour pull
    shows progress. Network-graceful: any failure logs + returns None (the run goes on
    with whatever else landed).
    """
    dest = _raw_dir() / fname
    if dest.exists() and dest.stat().st_size > 1_000_000 and not force:
        log.info("stack_exchange: %s already present (%d bytes) — skip download",
                 fname, dest.stat().st_size)
        return str(dest)
    url = f"{IA_BASE}/{fname}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    # RESUMABLE: a 20GB archive.org pull over a home line WILL be interrupted. Keep the
    # .part across attempts and resume with an HTTP Range from where it stopped, so a
    # capped/failed run makes cumulative progress instead of re-fetching 20GB each time.
    resume_at = tmp.stat().st_size if (tmp.exists() and not force) else 0
    if force and tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            pass
        resume_at = 0
    t0 = time.time()
    got = resume_at
    next_beat = ((resume_at // (256 << 20)) + 1) * (256 << 20)  # next ~256MiB boundary
    try:
        headers = dict(HEADERS)
        if resume_at:
            headers["Range"] = f"bytes={resume_at}-"
            log.info("stack_exchange: resuming %s from %.1fGB", fname, resume_at / 1e9)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=120) as r:
            status = getattr(r, "status", 200)
            # 206 = server honored the Range (append); 200 = full body (restart the file)
            append = resume_at and status == 206
            if resume_at and not append:
                log.warning("stack_exchange: server ignored Range (status %s) — "
                            "restarting %s from 0", status, fname)
                got = 0
                next_beat = 256 << 20
            clen = r.headers.get("Content-Length")
            clen = int(clen) if clen and clen.isdigit() else None
            total = (resume_at + clen) if (append and clen) else clen
            print(f"[stack_exchange] downloading {fname} "
                  f"({'%.1fGB' % (total / 1e9) if total else 'unknown size'}"
                  f"{', resume' if append else ''})", flush=True)
            with open(tmp, "ab" if append else "wb") as fh:
                while True:
                    if time.time() - t0 > time_cap_s:
                        log.warning("stack_exchange: time cap %ss hit downloading %s at "
                                    "%.1fGB — .part kept, resumes next run", time_cap_s,
                                    fname, got / 1e9)
                        return None
                    buf = r.read(chunk)
                    if not buf:
                        break
                    fh.write(buf)
                    got += len(buf)
                    if got >= next_beat:
                        pct = f" ({100 * got / total:.0f}%)" if total else ""
                        print(f"[stack_exchange] {fname}: {got / 1e9:.1f}GB{pct} "
                              f"in {time.time() - t0:.0f}s", flush=True)
                        next_beat += 256 << 20
        tmp.replace(dest)
        log.info("stack_exchange: downloaded %s (%d bytes total in %.0fs this run)",
                 fname, got, time.time() - t0)
        return str(dest)
    except Exception as e:  # noqa: BLE001 — one member must not sink the run; .part is KEPT
        log.warning("stack_exchange: download of %s interrupted (%s) — .part kept at "
                    "%.1fGB, resumes next run", fname, e, got / 1e9)
        return None


def land_raw(force: bool = False, time_cap_s: float = STAGE_TIME_CAP_S,
             which: tuple[str, ...] = ("posts",)) -> dict:
    """Download the requested dump members into the raw cache. Returns {name: path|None}.

    Defaults to just ``posts`` (Tags.7z is a convenience catalogue we don't strictly
    need — question Tags strings are self-describing). ``time_cap_s`` defaults to
    ``STAGE_TIME_CAP_S`` (just under the collect_all subprocess budget) so a capped
    download stops itself cleanly instead of being SIGKILLed mid-write; lower it for
    a probe run.
    """
    out: dict[str, str | None] = {}
    for key in which:
        fname = DUMP_FILES.get(key)
        if not fname:
            log.warning("stack_exchange: unknown dump member %r — skip", key)
            continue
        out[key] = _download_file(key, fname, force=force, time_cap_s=time_cap_s)
    return out


# ---------------------------------------------------------------------------
# build_staging — parse Posts.xml -> monthly tag volume + tag co-occurrence
# ---------------------------------------------------------------------------
def _parse_tags(raw: str) -> list[str]:
    """'<python><pandas><numpy>' (or '|python|pandas|') -> ['python','pandas','numpy'].

    Handles both the historical pipe-delimited form and the angle-bracket form the
    current dump uses. Drops meta/noise tags so the signal stays technology-focused.
    """
    s = (raw or "").strip()
    if not s:
        return []
    if s.startswith("<"):
        parts = [p for p in s.strip("<>").split("><") if p]
    else:
        parts = [p for p in s.split("|") if p]
    out = []
    for p in parts:
        t = p.strip().lower()
        if t and t not in _META_TAGS:
            out.append(t)
    return out


def _extract_posts_xml(archive_path: str):
    """Extract Posts.xml from the .7z to a temp file on disk and return its Path.

    The member is ~104GB uncompressed — FAR too big for an in-memory BytesIO — so we
    stream it to disk with py7zr's ``extract()`` (bounded memory) and the caller
    SAX-parses the file. py7zr 1.x exposes ``extract``/``extractall`` (to the
    filesystem), NOT a ``read`` that returns a file object, and even if it did a 104GB
    member could not be held in RAM. An already-extracted file is reused. Raises
    RuntimeError (caught by the caller) if py7zr is absent or the member is missing.
    """
    from pathlib import Path
    try:
        import py7zr  # lazy: heavy/optional dep, only needed at parse time
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "py7zr not installed — needed to read the Stack Exchange .7z dump. "
            "`pip install py7zr` then re-run build_staging."
        ) from e
    out_dir = _raw_dir() / "_extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive_path, mode="r") as z:
        member = next((n for n in z.getnames() if n.lower().endswith("posts.xml")), None)
        if member is None:
            raise RuntimeError(f"Posts.xml not found inside {archive_path} "
                               f"(has {z.getnames()})")
        target = out_dir / member
        if target.exists() and target.stat().st_size > 1_000_000:
            log.info("stack_exchange: reusing extracted %s (%.0fGB on disk)",
                     member, target.stat().st_size / 1e9)
            return Path(target)
        log.info("stack_exchange: extracting %s (~104GB) from the .7z to disk — "
                 "this takes a while (bounded memory)…", member)
        z.extract(path=str(out_dir), targets=[member])
    return Path(out_dir / member)


def build_staging(max_rows: int | None = None, min_volume: int = 50,
                  build_adjacency: bool = True) -> dict:
    """Parse the landed Posts.7z → tag_volume.json (+ tag_adjacency.json if enabled).

    Args:
        max_rows: cap on <row> elements parsed (for a bounded probe run); None = all.
        min_volume: prune any tag whose TOTAL question volume is below this, and any
            adjacency pair below this — strips one-off/typo tags so the staging files
            stay tractable and the signal is real.
        build_adjacency: accumulate the tag co-occurrence graph too. Memory-heavy over
            full history; off → only the (fuse-consumed) volume signal is built.

    Returns a summary dict. Graceful: if the archive isn't landed or py7zr is absent,
    logs + returns an empty summary (never crashes collect_all).
    """
    archive = _raw_dir() / DUMP_FILES["posts"]
    if not archive.exists():
        log.warning("stack_exchange: %s not landed — run land_raw() first", archive.name)
        return {"questions": 0, "volume_rows": 0, "adjacency_rows": 0, "reason": "no-archive"}

    try:
        xml_path = _extract_posts_xml(str(archive))
    except RuntimeError as e:
        log.warning("stack_exchange: cannot open Posts.xml (%s) — skip", e)
        return {"questions": 0, "volume_rows": 0, "adjacency_rows": 0, "reason": str(e)}

    # FAST line-based parse. The SO dump is exactly one <row .../> per line, so we skip
    # xml.sax entirely (its per-element Python callbacks capped throughput at ~1.7k
    # rows/s → 11h over 70M rows). A binary substring reject of answer-rows before any
    # regex lifts this to tens of thousands of rows/s. Body's own quotes are escaped
    # (&quot;), so Tags="([^"]*)" can't accidentally match inside a Body value.
    re_tags = re.compile(rb' Tags="([^"]*)"')
    re_date = re.compile(rb' CreationDate="(\d{4}-\d{2})')
    volume: dict[tuple[str, str], int] = defaultdict(int)
    adjacency: dict[tuple[str, str], int] = defaultdict(int)
    questions = rows = 0
    t0 = time.time()
    try:
        with open(xml_path, "rb") as fh:
            for line in fh:
                rows += 1
                if max_rows and rows >= max_rows:
                    log.info("stack_exchange: max_rows=%s reached — ending early", max_rows)
                    break
                if rows % 5_000_000 == 0:
                    print(f"[stack_exchange] {rows:,} rows ({questions:,} questions) "
                          f"in {time.time() - t0:.0f}s", flush=True)
                if b'PostTypeId="1"' not in line:      # fast reject: answers/meta/etc.
                    continue
                mt = re_tags.search(line)
                md = re_date.search(line)
                if not mt or not md:
                    continue
                # the raw attribute is XML-escaped ('&lt;python&gt;&lt;pandas&gt;') —
                # SAX used to unescape it for us; the regex path must do it explicitly
                # before _parse_tags, else the whole '<...>' blob is taken as one tag.
                tags = _parse_tags(html.unescape(mt.group(1).decode("utf-8", "replace")))
                if not tags:
                    continue
                period = md.group(1).decode("ascii", "replace")
                questions += 1
                for t in tags:
                    volume[(t, period)] += 1
                if build_adjacency and 2 <= len(tags) <= _MAX_TAGS_FOR_ADJ:
                    for a, b in combinations(sorted(set(tags)), 2):
                        adjacency[(a, b)] += 1
    except Exception as e:  # noqa: BLE001 — a malformed tail must not lose the aggregate
        log.warning("stack_exchange: parse ended on error (%s) — landing partial", e)
    finally:
        # reclaim the ~104GB extracted XML after a FULL parse (the .7z stays as the
        # durable cache). Keep it after a capped probe so a re-probe needn't re-extract.
        if max_rows is None:
            try:
                xml_path.unlink()
                log.info("stack_exchange: removed extracted Posts.xml (reclaimed ~104GB)")
            except OSError:
                pass
    log.info("stack_exchange: parsed %d rows / %d questions in %.0fs",
             rows, questions, time.time() - t0)

    # --- volume rows (pruned to tags clearing the volume floor across all time) ---
    tag_total: dict[str, int] = defaultdict(int)
    for (tag, _period), n in volume.items():
        tag_total[tag] += n
    keep_tags = {t for t, n in tag_total.items() if n >= min_volume}
    volume_rows = [
        {"skill": tag, "period": period, "n": n, "country": ""}
        for (tag, period), n in volume.items()
        if tag in keep_tags
    ]
    volume_rows.sort(key=lambda r: (r["skill"], r["period"]))

    # --- adjacency rows (both endpoints kept tags + pair clears the floor) ---
    adjacency_rows = [
        {"tag_a": a, "tag_b": b, "n": n}
        for (a, b), n in adjacency.items()
        if n >= min_volume and a in keep_tags and b in keep_tags
    ]
    adjacency_rows.sort(key=lambda r: r["n"], reverse=True)

    _volume_file().write_text(json.dumps(volume_rows), encoding="utf-8")
    # tag_adjacency.json is deliberately staged-for-later (no fuse reader yet) — see
    # _adjacency_file()'s docstring. Written here because it's free off the same sweep.
    _adjacency_file().write_text(json.dumps(adjacency_rows), encoding="utf-8")

    summary = {
        "questions": questions,
        "rows_parsed": rows,
        "kept_tags": len(keep_tags),
        "volume_rows": len(volume_rows),
        "adjacency_rows": len(adjacency_rows),
        "min_volume": min_volume,
    }
    log.info("stack_exchange staging: %s", summary)
    return summary


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------
def load_volume() -> list[dict]:
    f = _volume_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def load_adjacency() -> list[dict]:
    f = _adjacency_file()
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def run(force: bool = False, time_cap_s: float = STAGE_TIME_CAP_S,
        max_rows: int | None = None, min_volume: int = 50,
        skip_download: bool = False, build_adjacency: bool = True, **kw) -> dict:
    """Land the dump (unless skipped) then parse it into the staging file(s).

    Args:
        force: re-download even if the archive is already cached.
        time_cap_s: wall-clock budget for the (huge) download.
        max_rows: cap rows parsed — for a bounded probe; None = full history.
        min_volume: tag/pair volume floor for pruning noise.
        skip_download: parse an already-landed archive without touching the network.
        build_adjacency: also build the co-occurrence graph (memory-heavy; the fuse
            reads only tag_volume.json, so it is safe to skip on a RAM-tight box).

    Returns a summary dict (rows = volume + adjacency rows). collect_all calls this.
    """
    if not skip_download:
        land_raw(force=force, time_cap_s=time_cap_s, which=("posts",))
    summary = build_staging(max_rows=max_rows, min_volume=min_volume,
                            build_adjacency=build_adjacency)
    summary["rows"] = summary.get("volume_rows", 0) + summary.get("adjacency_rows", 0)
    return summary


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
