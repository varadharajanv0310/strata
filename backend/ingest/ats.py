"""ATS board connectors (Greenhouse / Lever / Ashby) — STUB (interface only).

The brainstorm's crown jewel: Greenhouse, Lever, Ashby, SmartRecruiters et al. expose
**public JSON board APIs** — structured postings, no HTML scraping — and the slug
universe falls out of the Common Crawl columnar index (see ``cc_index.py``). Polling
~65k boards weekly yields ~1M live postings.

This file defines the **interface + endpoint map + skeleton** so the future
ingestion run is "flip it on". The data-dependent guts (the exact JSON→Posting field
mapping per vendor) are deliberately NOT finished here — they must be written against
the live payloads, not guessed. Each ``_parse_*`` raises NotImplementedError with the
documented expected shape. Fetching, when implemented, rides the PoliteFleet harness
(``polite_fleet.py``) for pacing / circuit-breaking / checkpoint-resume.

NO ingestion is performed by this module in the build pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backend.core.logging import get_logger

log = get_logger("ingest.ats")

# public board-API endpoint templates ({slug} = company board id)
ATS_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever":      "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby":      "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
    "workable":   "https://apply.workable.com/api/v1/widget/accounts/{slug}",
}


@dataclass
class Posting:
    """Normalized posting — the common shape every ATS parser must emit."""
    source: str                      # 'greenhouse' | 'lever' | ...
    board_slug: str
    external_id: str
    title: str
    company: str = ""
    location: str = ""
    remote: bool | None = None
    department: str = ""             # ATS dept metadata aggregators strip (role-disambig gold)
    description: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    posted_at: str = ""
    url: str = ""
    raw: dict = field(default_factory=dict)


class AtsConnector:
    """Base ATS connector. Subclasses implement ``parse`` for one vendor's JSON."""

    vendor: str = ""

    def endpoint(self, slug: str) -> str:
        return ATS_ENDPOINTS[self.vendor].format(slug=slug)

    def parse(self, slug: str, payload: dict) -> list[Posting]:
        raise NotImplementedError

    def fetch_board(self, slug: str, http_get: Callable[[str], dict] | None = None) -> list[Posting]:
        """Fetch + parse one board. ``http_get`` is injected (so this stays testable
        and so the real run can route it through PoliteFleet). Raises if not wired."""
        if http_get is None:
            raise NotImplementedError(
                f"ATS fetch not wired for build pass. To enable: pass http_get "
                f"(route via polite_fleet.PoliteFleet) hitting {self.endpoint(slug)}")
        return self.parse(slug, http_get(self.endpoint(slug)))


class GreenhouseConnector(AtsConnector):
    vendor = "greenhouse"

    def parse(self, slug: str, payload: dict) -> list[Posting]:
        # TODO(ingestion): map Greenhouse JSON → Posting against a LIVE payload.
        # Expected: payload["jobs"] = [{id, title, location:{name},
        #   departments:[{name}], content (HTML), absolute_url, updated_at, ...}].
        # Salary is usually absent (Greenhouse) → salary_* stay None.
        raise NotImplementedError("GreenhouseConnector.parse — wire against live JSON")


class LeverConnector(AtsConnector):
    vendor = "lever"

    def parse(self, slug: str, payload: list | dict) -> list[Posting]:
        # TODO(ingestion): Lever returns a LIST of postings: [{id, text (title),
        #   categories:{team, location, commitment}, descriptionPlain, hostedUrl,
        #   createdAt, ...}]. Map team→department, categories.location→location.
        raise NotImplementedError("LeverConnector.parse — wire against live JSON")


class AshbyConnector(AtsConnector):
    vendor = "ashby"

    def parse(self, slug: str, payload: dict) -> list[Posting]:
        # TODO(ingestion): Ashby JobPosting JSON-LD often carries baseSalary →
        # populate salary_min/max/currency (Ashby has the highest disclosure rate
        # we measured, ~54%). Expected: payload["jobs"] with compensation block.
        raise NotImplementedError("AshbyConnector.parse — wire against live JSON")


CONNECTORS: dict[str, type[AtsConnector]] = {
    "greenhouse": GreenhouseConnector,
    "lever": LeverConnector,
    "ashby": AshbyConnector,
}


def fetch_fleet(slugs_by_vendor: dict[str, list[str]], **fleet_kw):
    """STUB orchestrator: poll many boards across vendors via PoliteFleet.

    TODO(ingestion): build units = [(vendor, slug), ...]; host_of = lambda u:
    ATS host for u[0]; fetch_fn = CONNECTORS[u[0]]().fetch_board(u[1], http_get).
    Run with polite_fleet.PoliteFleet(checkpoint=ParquetCheckpoint(...)). Deferred:
    needs the slug universe from cc_index.enumerate_ats_slugs() (a real run).
    """
    raise NotImplementedError(
        "ATS fleet ingestion deferred — needs slug enumeration (cc_index) + a run. "
        "Interface ready: wire CONNECTORS + PoliteFleet when enabling.")
