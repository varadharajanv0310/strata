"""Common Crawl connector — the volume/role/skill spine for the GPU pipeline (§6).

Legitimate, no LinkedIn/Indeed scraping. We mine **schema.org/JobPosting JSON-LD**
that ATS hosts already publish, via Common Crawl's public index + WARC store:

  1. Resolve recent monthly crawls from ``collinfo.json``.
  2. For each (crawl, ATS host) locate matching captures through the **CDX API**
     (``index.commoncrawl.org``); when that 504s — which it does intermittently —
     fall back to the **columnar cc-index** on ``data.commoncrawl.org`` by binary
     -searching ``cluster.idx`` for the host's SURT prefix, byte-range-fetching the
     one ``cdx-NNNNN.gz`` block that covers it, and reading the CDX lines from there.
  3. **Byte-range-fetch** only the matching WARC records and extract the embedded
     JobPosting JSON-LD (title, description, addressCountry→country_code,
     hiringOrganization→employer, datePosted, baseSalary present?).

Reliable hosts (probed 2026-06): ``jobs.ashbyhq.com`` emits clean JobPosting
JSON-LD on every posting; ``*.myworkdayjobs.com`` does on ``/job/`` URLs. Greenhouse
was DROPPED — boards.greenhouse.io now 301s to a client-rendered SPA with no JSON-LD.
Lever/SmartRecruiters CDX captures are mostly shallow listing pages with no posting
JSON-LD, so they're not in the default set (kept available via config override).

Idempotent + resumable: each (crawl, host) unit is checkpointed on disk and skipped
on re-run; raw JSON-LD blocks are cached under staging/common_crawl/raw/. The fusion
step reads staging/common_crawl/postings.parquet; the measured salary-disclosure rate
lands in staging/common_crawl/probe.json.

Council decisions (logged in council_decisions):
  * 504/transient strategy — CDX API gets a bounded retry (3 tries, exp backoff)
    then we fall through to the columnar index; the columnar block fetch itself gets
    a bounded retry, then we GIVE UP on that unit and move on. Never hang.
  * Low salary-disclosure rate is EXPECTED and FINE. CC postings feed the
    volume / role / skill / employer / geography spine regardless of salary —
    Adzuna + SO survey carry the pay signal. We just *measure* and report disclosure.
"""
from __future__ import annotations

import bisect
import gzip
import io
import json
import time

import requests

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.ingest import checkpoint
from backend.ingest.base import BaseConnector
from backend.warehouse.seed import COUNTRIES

log = get_logger("ingest.common_crawl")

# Default targets: ATS hosts that reliably emit JobPosting JSON-LD (probed live).
# greenhouse dropped (SPA, no JSON-LD); lever/smartrecruiters captures are listing
# pages w/o posting JSON-LD. Override via CC_TARGET_DOMAINS in .env if desired.
DEFAULT_DOMAINS = [
    "jobs.ashbyhq.com",          # primary — every posting carries JobPosting JSON-LD
    "*.myworkdayjobs.com",       # secondary — /job/ posting pages carry it
]
# domains whose *individual posting* URLs are worth fetching (filter noise on big hosts)
_URL_HINTS = {"*.myworkdayjobs.com": "/job/"}

CDX_HOST = "https://index.commoncrawl.org"
DATA_HOST = "https://data.commoncrawl.org"
_HEADERS = {"User-Agent": "strata-jobmarket/1.0 (research; contact via repo)"}

# --- country normalization: JSON-LD addressCountry is free text or ISO -----------
_NAME_TO_ISO = {c["name"].lower(): c["code"] for c in COUNTRIES}
_NAME_TO_ISO.update({
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US",
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "britain": "GB",
    "deutschland": "DE",
})
_ISO = {c["code"] for c in COUNTRIES}


def _country_code(val) -> str | None:
    """addressCountry -> one of our 7 ISO codes, else None (still a valid posting)."""
    if isinstance(val, dict):
        val = val.get("name") or val.get("@id")
    if not isinstance(val, str):
        return None
    s = val.strip()
    if s.upper() in _ISO:
        return s.upper()
    if len(s) == 2 and s.upper() in _ISO:
        return s.upper()
    return _NAME_TO_ISO.get(s.lower())


def _surt_prefix(domain: str) -> str:
    """ats host -> SURT key prefix used by the CDX/columnar index.

    'jobs.ashbyhq.com'  -> 'com,ashbyhq,jobs)/'
    '*.myworkdayjobs.com' -> 'com,myworkdayjobs,'  (wildcard subdomain -> open prefix)
    """
    wild = domain.startswith("*.")
    host = domain[2:] if wild else domain
    surt = ",".join(reversed(host.split(".")))
    return f"{surt}," if wild else f"{surt})/"


class CommonCrawlConnector(BaseConnector):
    name = "common_crawl"
    description = "schema.org/JobPosting JSON-LD from ATS hosts across monthly crawls (CDX + columnar fallback)"
    joins_on = ("country", "role", "skill", "employer", "time")
    adds_signal = "job-level postings at scale (volume/role/skill spine) + multi-year history"

    # -------- crawl resolution --------
    def _recent_crawls(self, crawls: list[str] | None, n: int) -> list[str]:
        if crawls:
            return crawls
        try:
            r = requests.get(f"{CDX_HOST}/collinfo.json", headers=_HEADERS, timeout=30)
            r.raise_for_status()
            return [c["id"] for c in r.json()][:n]
        except Exception as e:  # noqa: BLE001
            log.warning("collinfo unavailable (%s) — no crawls resolved", e)
            return []

    # -------- index: CDX API (primary) --------
    def _cdx_api(self, crawl: str, domain: str, limit: int) -> list[dict] | None:
        """Query the hosted CDX API. Returns rows, or None on persistent 504/5xx
        (signal to the caller to try the columnar fallback)."""
        url = f"{CDX_HOST}/{crawl}-index"
        params = {
            "url": domain if domain.startswith("*.") else f"{domain}/*",
            "output": "json", "filter": "status:200", "limit": str(limit * 4),
        }
        for attempt in range(3):
            try:
                with requests.get(url, params=params, headers=_HEADERS,
                                  stream=True, timeout=90) as r:
                    if r.status_code in (503, 504, 502, 500, 429):
                        wait = 4 * (2 ** attempt)
                        log.warning("CDX %s @ %s -> %s, backoff %ss (try %d)",
                                    domain, crawl, r.status_code, wait, attempt + 1)
                        time.sleep(wait)
                        continue
                    r.raise_for_status()
                    out = []
                    for line in r.iter_lines():
                        if line:
                            try:
                                out.append(json.loads(line))
                            except Exception:  # noqa: BLE001
                                continue
                    return out
            except Exception as e:  # noqa: BLE001
                wait = 4 * (2 ** attempt)
                log.warning("CDX request error %s @ %s: %s — backoff %ss",
                            domain, crawl, e, wait)
                time.sleep(wait)
        return None  # persistent failure -> caller falls back to columnar index

    # -------- index: columnar cc-index (fallback) --------
    def _cluster_idx(self, crawl: str) -> list[tuple[str, int, int, str]] | None:
        """Cache + parse cluster.idx -> sorted [(surtkey, offset, length, cdxfile)].

        cluster.idx is the second-level index of the columnar cc-index; each row
        points at one cdx-NNNNN.gz block. We download it once per crawl and cache.
        """
        cache = self._raw_dir() / f"cluster_{crawl}.idx"
        if not (cache.exists() and cache.stat().st_size > 1000):
            url = f"{DATA_HOST}/cc-index/collections/{crawl}/indexes/cluster.idx"
            for attempt in range(3):
                try:
                    r = requests.get(url, headers=_HEADERS, timeout=180)
                    r.raise_for_status()
                    cache.write_bytes(r.content)
                    break
                except Exception as e:  # noqa: BLE001
                    wait = 5 * (2 ** attempt)
                    log.warning("cluster.idx %s fetch err: %s — backoff %ss", crawl, e, wait)
                    time.sleep(wait)
            else:
                return None
        rows: list[tuple[str, int, int, str]] = []
        for ln in cache.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = ln.split("\t")
            if len(parts) < 4:
                continue
            surt = parts[0].split(" ", 1)[0]
            try:
                rows.append((surt, int(parts[2]), int(parts[3]), parts[1]))
            except (ValueError, IndexError):
                continue
        rows.sort(key=lambda x: x[0])
        return rows

    def _columnar(self, crawl: str, domain: str, limit: int) -> list[dict]:
        """Resolve captures for a host through the columnar index (CDX-API fallback)."""
        idx = self._cluster_idx(crawl)
        if not idx:
            return []
        prefix = _surt_prefix(domain)
        keys = [r[0] for r in idx]
        # blocks whose key range may contain our prefix: the block starting at-or-before
        # the prefix, through all blocks whose start key shares the prefix.
        start = max(0, bisect.bisect_right(keys, prefix) - 1)
        wanted = []
        for i in range(start, len(idx)):
            surt, off, length, cdxfile = idx[i]
            if i > start and not surt.startswith(prefix[:8]) and surt > prefix:
                # we've walked past any block that could hold the prefix
                if not surt.startswith(prefix.rstrip(")/,")[:6]):
                    break
            wanted.append(idx[i])
            if len(wanted) >= 6:  # cap blocks scanned per unit (bounded work)
                break
        out: list[dict] = []
        for surt, off, length, cdxfile in wanted:
            url = f"{DATA_HOST}/cc-index/collections/{crawl}/indexes/{cdxfile}"
            block = None
            for attempt in range(2):
                try:
                    h = {**_HEADERS, "Range": f"bytes={off}-{off + length - 1}"}
                    r = requests.get(url, headers=h, timeout=120)
                    r.raise_for_status()
                    block = gzip.GzipFile(fileobj=io.BytesIO(r.content)).read()
                    break
                except Exception as e:  # noqa: BLE001
                    time.sleep(3 * (attempt + 1))
                    log.warning("columnar block fetch err (%s): %s", cdxfile, e)
            if not block:
                continue
            for line in block.decode("utf-8", "replace").splitlines():
                if not line.startswith(prefix.split(")")[0]):
                    continue
                sp = line.split(" ", 2)
                if len(sp) < 3:
                    continue
                try:
                    rec = json.loads(sp[2])
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("status") != "200" or rec.get("mime-detected", rec.get("mime")) not in (
                    "text/html", None,
                ):
                    if rec.get("status") != "200":
                        continue
                out.append(rec)
                if len(out) >= limit * 4:
                    return out
        return out

    def _resolve_index(self, crawl: str, domain: str, limit: int) -> list[dict]:
        rows = self._cdx_api(crawl, domain, limit)
        if rows is None:
            log.info("CDX API exhausted for %s @ %s — using columnar fallback", domain, crawl)
            rows = self._columnar(crawl, domain, limit)
        # keep only individual-posting URLs on hosts that need it
        hint = _URL_HINTS.get(domain)
        if hint:
            rows = [r for r in rows if hint in (r.get("url") or "")]
        return rows

    # -------- WARC fetch + extraction --------
    def _fetch_record(self, rec: dict) -> bytes | None:
        try:
            off, length = int(rec["offset"]), int(rec["length"])
        except (KeyError, ValueError, TypeError):
            return None
        h = {**_HEADERS, "Range": f"bytes={off}-{off + length - 1}"}
        for attempt in range(2):
            try:
                r = requests.get(f"{DATA_HOST}/{rec['filename']}", headers=h, timeout=90)
                r.raise_for_status()
                return gzip.GzipFile(fileobj=io.BytesIO(r.content)).read()
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    log.warning("range fetch failed: %s", e)
                else:
                    time.sleep(2)
        return None

    @staticmethod
    def _extract_jobpostings(html_bytes: bytes) -> list[dict]:
        from bs4 import BeautifulSoup

        try:
            body = html_bytes.split(b"\r\n\r\n", 2)[-1]
            soup = BeautifulSoup(body, "html.parser")
        except Exception:  # noqa: BLE001
            return []
        out = []
        for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(tag.string or "")
            except Exception:  # noqa: BLE001
                continue
            stack = data if isinstance(data, list) else [data]
            # unwrap @graph containers
            graphed = []
            for node in stack:
                if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                    graphed.extend(node["@graph"])
                else:
                    graphed.append(node)
            for node in graphed:
                if not isinstance(node, dict):
                    continue
                t = node.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    out.append(node)
        return out

    # -------- filesystem --------
    def _raw_dir(self):
        d = settings.staging_dir / self.name / "raw"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -------- main scan --------
    def land_raw(self, target_postings: int | None = None,
                 crawls: list[str] | None = None) -> int:
        target = target_postings or 300
        domains = settings.cc_target_domains_list or DEFAULT_DOMAINS
        crawl_ids = self._recent_crawls(crawls, settings.cc_recent_crawls)
        if not crawl_ids:
            raise NotImplementedError(
                "Common Crawl index unreachable — code ready; run with network access."
            )
        per_unit = max(40, target // max(1, len(crawl_ids)))
        total = 0
        for crawl in crawl_ids:
            for domain in domains:
                if total >= target:
                    return total
                unit = f"{crawl}:{domain}"
                if checkpoint.is_done(self.name, unit):
                    continue
                recs = self._resolve_index(crawl, domain, per_unit)
                landed: list[dict] = []
                for rec in recs:
                    raw = self._fetch_record(rec)
                    if raw:
                        for jp in self._extract_jobpostings(raw):
                            jp["_source_url"] = rec.get("url")
                            jp["_crawl"] = crawl
                            landed.append(jp)
                    if len(landed) >= per_unit:
                        break
                if landed:
                    fname = f"{crawl}__{domain.replace('*.', '').replace('/', '_')}.json"
                    self._raw_dir().joinpath(fname).write_text(
                        json.dumps(landed, ensure_ascii=False), encoding="utf-8")
                    total += len(landed)
                    log.info("CC %s @ %-22s -> %d postings (running %d)",
                             crawl, domain, len(landed), total)
                checkpoint.mark_done(self.name, unit)
        return total

    # -------- normalize + probe --------
    def build_staging(self) -> int:
        import pandas as pd

        rows = []
        for f in self._raw_dir().glob("*__*.json"):
            try:
                blocks = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for jp in blocks:
                loc = jp.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                addr = loc.get("address", {}) if isinstance(loc, dict) else {}
                sal = jp.get("baseSalary")
                has_salary = bool(sal) and not (isinstance(sal, dict) and not sal)
                org = jp.get("hiringOrganization") or {}
                rows.append({
                    "title": jp.get("title"),
                    "description": (jp.get("description") or "")[:4000],
                    "country_code": _country_code(addr.get("addressCountry"))
                                    if isinstance(addr, dict) else None,
                    "raw_country": addr.get("addressCountry") if isinstance(addr, dict) else None,
                    "employer": org.get("name") if isinstance(org, dict) else None,
                    "date_posted": jp.get("datePosted"),
                    "employment_type": jp.get("employmentType"),
                    "has_salary": has_salary,
                    "remote": jp.get("jobLocationType") == "TELECOMMUTE",
                    "source_url": jp.get("_source_url"),
                    "crawl": jp.get("_crawl"),
                })
        sd = settings.staging_dir / self.name
        sd.mkdir(parents=True, exist_ok=True)
        if not rows:
            (sd / "probe.json").write_text(json.dumps(
                {"total_postings": 0, "salary_disclosure_rate": None,
                 "country_breakdown": {}}), encoding="utf-8")
            return 0
        df = pd.DataFrame(rows).drop_duplicates(
            subset=["title", "employer", "raw_country", "date_posted"])
        out = sd / "postings.parquet"
        try:
            df.to_parquet(out, index=False)
        except Exception as e:  # noqa: BLE001 — parquet engine optional
            log.warning("parquet write failed (%s) — falling back to JSONL", e)
            out = sd / "postings.jsonl"
            out.write_text("\n".join(df.to_json(orient="records", lines=True).splitlines()),
                           encoding="utf-8")
        n = len(df)
        disclosed = int(df["has_salary"].sum())
        cb = (df["country_code"].dropna().value_counts().to_dict())
        probe = {
            "total_postings": n,
            "with_salary": disclosed,
            "salary_disclosure_rate": round(disclosed / n, 4) if n else None,
            "country_breakdown": {k: int(v) for k, v in cb.items()},
            "mapped_country_share": round(df["country_code"].notna().mean(), 4),
            "hosts": sorted({(u or "").split("/")[2] for u in df["source_url"].dropna()
                             if (u or "").count("/") > 2}),
        }
        (sd / "probe.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")
        log.info("CC staging: %d postings, salary-disclosure %.1f%%, countries=%s",
                 n, 100 * (disclosed / n if n else 0), cb)
        return n


# -------- top-level orchestrator entrypoint --------
def run(target_postings: int = 300, crawls: list[str] | None = None) -> dict:
    """Scan Common Crawl for JobPosting JSON-LD; land postings.parquet + probe.json.

    Args:
        target_postings: how many postings to aim for (collect_all widens this).
        crawls: explicit crawl ids (e.g. ['CC-MAIN-2026-25', ...]); default = most
                recent settings.cc_recent_crawls crawls.
    Returns a summary dict (also the SMOKE self-check payload).
    """
    c = CommonCrawlConnector()
    raw = c.land_raw(target_postings=target_postings, crawls=crawls)
    staging = c.build_staging()
    probe = {}
    p = settings.staging_dir / c.name / "probe.json"
    if p.exists():
        probe = json.loads(p.read_text(encoding="utf-8"))
    summary = {"raw_postings": raw, "staging_rows": staging, **probe}
    log.info("common_crawl run summary: %s", summary)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(target_postings=300), indent=2))
