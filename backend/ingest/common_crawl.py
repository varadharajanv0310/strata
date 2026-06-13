"""Common Crawl connector — the primary postings source + historical depth (§6).

Strategy (legitimate, no LinkedIn/Indeed scraping):
  1. Resolve the most-recent N monthly crawls (+ historical sampling) from collinfo.
  2. Query the **CC columnar URL index (cc-index)** to target job-board / company
     -careers domains efficiently — we do NOT download petabytes.
  3. **Byte-range-fetch** only the matching WARC records and extract embedded
     `schema.org/JobPosting` JSON-LD (title, description, skills, jobLocation→
     addressCountry, baseSalary+currency, datePosted, employmentType,
     hiringOrganization, jobLocationType).

Idempotent + resumable: progress is checkpointed per (crawl, domain) so a long
scan never restarts from zero. Scope (crawls, years, domains) is config-driven.
Web Data Commons' prior JobPosting extractions may bootstrap/validate the parser.
"""
from __future__ import annotations

import gzip
import io
import json

import requests

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.ingest import checkpoint
from backend.ingest.base import BaseConnector

log = get_logger("ingest.common_crawl")

# curated default target domains (careers/ATS hosts that publish JobPosting JSON-LD)
DEFAULT_DOMAINS = [
    "boards.greenhouse.io", "jobs.lever.co", "*.myworkdayjobs.com",
    "jobs.ashbyhq.com", "*.bamboohr.com", "careers.google.com",
]
DATA_HOST = "https://data.commoncrawl.org"


class CommonCrawlConnector(BaseConnector):
    name = "common_crawl"
    description = "schema.org/JobPosting JSON-LD from targeted careers domains across monthly crawls"
    joins_on = ("country", "role", "skill", "employer", "time")
    adds_signal = "job-level postings at scale + multi-year history"

    def _recent_crawls(self) -> list[str]:
        try:
            r = requests.get(f"{settings.cc_index_server}/collinfo.json", timeout=30)
            r.raise_for_status()
            ids = [c["id"] for c in r.json()]
            return ids[: settings.cc_recent_crawls]
        except Exception as e:
            log.warning("collinfo unavailable (%s) — using none", e)
            return []

    def _query_index(self, crawl: str, domain: str, limit: int) -> list[dict]:
        url = f"{settings.cc_index_server}/{crawl}-index"
        params = {"url": domain, "output": "json", "filter": "mime:text/html", "limit": str(limit)}
        out: list[dict] = []
        try:
            with requests.get(url, params=params, stream=True, timeout=60) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if line:
                        out.append(json.loads(line))
        except Exception as e:
            log.warning("index query failed for %s @ %s: %s", domain, crawl, e)
        return out

    def _fetch_record(self, rec: dict) -> bytes | None:
        offset, length = int(rec["offset"]), int(rec["length"])
        headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
        try:
            r = requests.get(f"{DATA_HOST}/{rec['filename']}", headers=headers, timeout=60)
            r.raise_for_status()
            return gzip.GzipFile(fileobj=io.BytesIO(r.content)).read()
        except Exception as e:
            log.warning("range fetch failed: %s", e)
            return None

    @staticmethod
    def _extract_jobposting(html_bytes: bytes) -> list[dict]:
        """Pull JobPosting JSON-LD blocks out of a WARC HTTP response."""
        from bs4 import BeautifulSoup

        try:
            body = html_bytes.split(b"\r\n\r\n", 2)[-1]
            soup = BeautifulSoup(body, "html.parser")
        except Exception:
            return []
        postings = []
        for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(tag.string or "")
            except Exception:
                continue
            for node in data if isinstance(data, list) else [data]:
                if isinstance(node, dict) and node.get("@type") == "JobPosting":
                    postings.append(node)
        return postings

    def land_raw(self, limit: int | None = None) -> int:
        limit = limit or 200
        domains = settings.cc_target_domains_list or DEFAULT_DOMAINS
        crawls = self._recent_crawls()
        if not crawls:
            raise NotImplementedError(
                "Common Crawl index unreachable in this environment. Code is ready; "
                "run with network access to scan (scope in .env: CC_RECENT_CRAWLS / CC_TARGET_DOMAINS)."
            )
        total = 0
        for crawl in crawls:
            for domain in domains:
                unit = f"{crawl}:{domain}"
                if checkpoint.is_done(self.name, unit):
                    continue
                recs = self._query_index(crawl, domain, limit)
                landed = []
                for rec in recs:
                    raw = self._fetch_record(rec)
                    if raw:
                        landed.extend(self._extract_jobposting(raw))
                    if len(landed) >= limit:
                        break
                if landed:
                    self.write_raw_json(f"{crawl}__{domain.replace('*.', '').replace('/', '_')}.json", landed)
                    total += len(landed)
                checkpoint.mark_done(self.name, unit)
        return total

    def build_staging(self) -> int:
        """Normalize landed JobPosting JSON-LD into typed STAGING rows."""
        import pandas as pd

        rows = []
        for f in self.raw_dir().glob("*.json"):
            for jp in json.loads(f.read_text(encoding="utf-8")):
                loc = jp.get("jobLocation") or {}
                addr = (loc[0] if isinstance(loc, list) and loc else loc).get("address", {}) if loc else {}
                sal = jp.get("baseSalary", {}) or {}
                val = sal.get("value", {}) if isinstance(sal, dict) else {}
                rows.append({
                    "title": jp.get("title"),
                    "description": (jp.get("description") or "")[:4000],
                    "country": addr.get("addressCountry"),
                    "currency": sal.get("currency"),
                    "salary": val.get("value") if isinstance(val, dict) else None,
                    "date_posted": jp.get("datePosted"),
                    "employment_type": jp.get("employmentType"),
                    "employer": (jp.get("hiringOrganization") or {}).get("name"),
                    "remote": jp.get("jobLocationType") == "TELECOMMUTE",
                })
        if not rows:
            return 0
        df = pd.DataFrame(rows).drop_duplicates(subset=["title", "employer", "country", "date_posted"])
        df.to_parquet(self.staging_dir() / "postings.parquet", index=False)
        return len(df)
