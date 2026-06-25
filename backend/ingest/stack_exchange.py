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

import json
import time
import urllib.request
import xml.sax
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
    t0 = time.time()
    got = 0
    next_beat = 256 << 20  # heartbeat every ~256 MiB
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as fh:
            total = r.headers.get("Content-Length")
            total = int(total) if total and total.isdigit() else None
            print(f"[stack_exchange] downloading {fname} "
                  f"({'%.1fGB' % (total / 1e9) if total else 'unknown size'})", flush=True)
            while True:
                if time.time() - t0 > time_cap_s:
                    log.warning("stack_exchange: time cap %ss hit downloading %s — "
                                "partial left as .part (resume not supported, will "
                                "re-fetch)", time_cap_s, fname)
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
        log.info("stack_exchange: downloaded %s (%d bytes in %.0fs)",
                 fname, got, time.time() - t0)
        return str(dest)
    except Exception as e:  # noqa: BLE001 — one member must not sink the run
        log.warning("stack_exchange: download of %s failed (%s) — skip", fname, e)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return None


def land_raw(force: bool = False, time_cap_s: float = 36000.0,
             which: tuple[str, ...] = ("posts",)) -> dict:
    """Download the requested dump members into the raw cache. Returns {name: path|None}.

    Defaults to just ``posts`` (Tags.7z is a convenience catalogue we don't strictly
    need — question Tags strings are self-describing). ``time_cap_s`` defaults to 10h
    because Posts.7z is enormous; lower it for a probe run.
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
class _PostsHandler(xml.sax.ContentHandler):
    """SAX handler over Posts.xml. Each <row> is a post; PostTypeId=1 are questions.

    We accumulate, in memory:
      * volume[(tag, 'YYYY-MM')] -> question count
      * adjacency[(tag_a, tag_b)] -> co-occurrence count (sorted pair, tag_a < tag_b)

    SAX (event streaming) — never builds a DOM — so a 100GB+ XML is processed in
    constant memory aside from the aggregate dicts (bounded by #tags × #months and
    #tag-pairs, both modest after the volume floor prune in build_staging).
    """

    def __init__(self, max_rows: int | None = None,
                 heartbeat_every: int = 2_000_000):
        super().__init__()
        self.volume: dict[tuple[str, str], int] = defaultdict(int)
        self.adjacency: dict[tuple[str, str], int] = defaultdict(int)
        self.questions = 0
        self.rows = 0
        self.max_rows = max_rows
        self.heartbeat_every = heartbeat_every
        self._t0 = time.time()
        self._stop = False

    def startElement(self, name, attrs):  # noqa: N802 — SAX API name
        if name != "row":
            return
        self.rows += 1
        if self.heartbeat_every and self.rows % self.heartbeat_every == 0:
            print(f"[stack_exchange] parsed {self.rows:,} rows "
                  f"({self.questions:,} questions) in {time.time() - self._t0:.0f}s",
                  flush=True)
        if self.max_rows and self.rows >= self.max_rows:
            self._stop = True
            raise StopIteration  # caught by the driver to end the parse early
        # questions only
        if attrs.get("PostTypeId") != "1":
            return
        raw_tags = attrs.get("Tags")
        created = attrs.get("CreationDate")  # e.g. '2010-08-23T19:24:53.000'
        if not raw_tags or not created or len(created) < 7:
            return
        period = created[:7]  # 'YYYY-MM'
        tags = _parse_tags(raw_tags)
        if not tags:
            return
        self.questions += 1
        for t in tags:
            self.volume[(t, period)] += 1
        # adjacency only for sanely-tagged questions (skip grab-bags)
        if 2 <= len(tags) <= _MAX_TAGS_FOR_ADJ:
            for a, b in combinations(sorted(set(tags)), 2):
                self.adjacency[(a, b)] += 1


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


def _open_posts_xml(archive_path: str):
    """Yield a binary file object for Posts.xml streamed out of the .7z (lazy py7zr).

    py7zr can extract a single member to a file-like object without inflating the whole
    archive to disk. If py7zr is missing we raise a clear, catchable error (the caller
    flags it) rather than crashing the whole collect_all.
    """
    try:
        import py7zr  # lazy: heavy/optional dep, only needed at parse time
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "py7zr not installed — needed to read the Stack Exchange .7z dump. "
            "`pip install py7zr` then re-run build_staging."
        ) from e
    with py7zr.SevenZipFile(archive_path, mode="r") as z:
        names = z.getnames()
        member = next((n for n in names if n.lower().endswith("posts.xml")), None)
        if member is None:
            raise RuntimeError(f"Posts.xml not found inside {archive_path} (has {names})")
        # read() returns {name: BytesIO} for the requested members
        data = z.read(targets=[member])
        bio = data[member]
        bio.seek(0)
        return bio


def build_staging(max_rows: int | None = None, min_volume: int = 50) -> dict:
    """Parse the landed Posts.7z → tag_volume.json + tag_adjacency.json.

    Args:
        max_rows: cap on <row> elements parsed (for a bounded probe run); None = all.
        min_volume: prune any tag whose TOTAL question volume is below this, and any
            adjacency pair below this — strips one-off/typo tags so the staging files
            stay tractable and the signal is real.

    Returns a summary dict. Graceful: if the archive isn't landed or py7zr is absent,
    logs + returns an empty summary (never crashes collect_all).
    """
    archive = _raw_dir() / DUMP_FILES["posts"]
    if not archive.exists():
        log.warning("stack_exchange: %s not landed — run land_raw() first", archive.name)
        return {"questions": 0, "volume_rows": 0, "adjacency_rows": 0, "reason": "no-archive"}

    handler = _PostsHandler(max_rows=max_rows)
    try:
        bio = _open_posts_xml(str(archive))
    except RuntimeError as e:
        log.warning("stack_exchange: cannot open Posts.xml (%s) — skip", e)
        return {"questions": 0, "volume_rows": 0, "adjacency_rows": 0, "reason": str(e)}

    parser = xml.sax.make_parser()
    parser.setFeature(xml.sax.handler.feature_namespaces, False)
    parser.setContentHandler(handler)
    try:
        # feed the stream in chunks so we control memory + can stop early
        while True:
            buf = bio.read(1 << 20)
            if not buf:
                break
            try:
                parser.feed(buf)
            except StopIteration:
                log.info("stack_exchange: max_rows=%s reached — ending parse early", max_rows)
                break
        try:
            parser.close()
        except Exception:  # noqa: BLE001 — close after an early stop can complain
            pass
    except Exception as e:  # noqa: BLE001 — a malformed tail must not lose the aggregate
        log.warning("stack_exchange: parse ended on error (%s) — landing partial", e)

    # --- volume rows (pruned to tags clearing the volume floor across all time) ---
    tag_total: dict[str, int] = defaultdict(int)
    for (tag, _period), n in handler.volume.items():
        tag_total[tag] += n
    keep_tags = {t for t, n in tag_total.items() if n >= min_volume}
    volume_rows = [
        {"skill": tag, "period": period, "n": n, "country": ""}
        for (tag, period), n in handler.volume.items()
        if tag in keep_tags
    ]
    volume_rows.sort(key=lambda r: (r["skill"], r["period"]))

    # --- adjacency rows (both endpoints kept tags + pair clears the floor) ---
    adjacency_rows = [
        {"tag_a": a, "tag_b": b, "n": n}
        for (a, b), n in handler.adjacency.items()
        if n >= min_volume and a in keep_tags and b in keep_tags
    ]
    adjacency_rows.sort(key=lambda r: r["n"], reverse=True)

    _volume_file().write_text(json.dumps(volume_rows), encoding="utf-8")
    _adjacency_file().write_text(json.dumps(adjacency_rows), encoding="utf-8")

    summary = {
        "questions": handler.questions,
        "rows_parsed": handler.rows,
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
def run(force: bool = False, time_cap_s: float = 36000.0,
        max_rows: int | None = None, min_volume: int = 50,
        skip_download: bool = False, **kw) -> dict:
    """Land the dump (unless skipped) then parse it into the two staging files.

    Args:
        force: re-download even if the archive is already cached.
        time_cap_s: wall-clock budget for the (huge) download.
        max_rows: cap rows parsed — for a bounded probe; None = full history.
        min_volume: tag/pair volume floor for pruning noise.
        skip_download: parse an already-landed archive without touching the network.

    Returns a summary dict (rows = volume + adjacency rows). collect_all calls this.
    """
    if not skip_download:
        land_raw(force=force, time_cap_s=time_cap_s, which=("posts",))
    summary = build_staging(max_rows=max_rows, min_volume=min_volume)
    summary["rows"] = summary.get("volume_rows", 0) + summary.get("adjacency_rows", 0)
    return summary


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(run(), indent=2))
