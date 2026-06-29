"""Remaining §6 sources registered as **scaffold connectors**.

Each documents its real extraction plan and reports `scaffold` (or `skipped` when
its credentials are absent) so the catalog is complete and coverage is honest.
They share the BaseConnector contract (idempotent/resumable/credential-graceful)
and are promoted to full extractors as the build continues / the developer runs
them at scale. Common Crawl and Adzuna are already full (see their modules).
"""
from __future__ import annotations

from backend.ingest.base import ScaffoldConnector


class DolOflc(ScaffoldConnector):
    name = "dol_oflc"
    description = "US DOL OFLC H-1B/PERM disclosure — ~8M+ individual cases with SOC code + base salary"
    joins_on = ("country", "role", "employer", "time")
    adds_signal = "US individual-level wage ground truth (migration-relevant)"
    plan = ("Download quarterly OFLC disclosure files, parse employer/title/SOC/worksite/base-wage, "
            "crosswalk SOC→role, land to fact_salary_person (person-level).")


class PyPiNpm(ScaffoldConnector):
    name = "pypi_npm"
    description = "PyPI / npm download stats (BigQuery public) — technology adoption proxy"
    joins_on = ("skill", "time")
    adds_signal = "package-adoption demand proxy"
    plan = "Query the public BigQuery download-stats datasets; aggregate to skill adoption over time."


class Lightcast(ScaffoldConnector):
    name = "lightcast"
    description = "Lightcast Open Skills (~34k skills) — canonical skill taxonomy + title mappings"
    requires = ("lightcast_client_id", "lightcast_client_secret")
    joins_on = ("skill",)
    adds_signal = "canonical skill ids (normalization backbone)"
    plan = "OAuth client-credentials → pull skills + relations; load dim_skill canonical ids + embeddings."


class Esco(ScaffoldConnector):
    name = "esco"
    description = "ESCO multilingual skills/occupations — needed for German (and other) terms"
    joins_on = ("skill", "role")
    adds_signal = "multilingual taxonomy crosswalk"
    plan = "Download ESCO classification (CSV/RDF); map ESCO↔Lightcast↔O*NET; enrich dim_skill."


class Onet(ScaffoldConnector):
    name = "onet"
    description = "O*NET occupation↔skill↔wage crosswalk (US)"
    joins_on = ("role", "skill")
    adds_signal = "occupation↔skill↔wage crosswalk + resume mapping"
    plan = "Download O*NET DB; build SOC↔skill↔role crosswalk used by skill_norm and the résumé feature."


class OecdPpp(ScaffoldConnector):
    name = "oecd_ppp"
    description = "OECD PPP factors — powers the nominal/PPP toggle"
    joins_on = ("country", "time")
    adds_signal = "purchasing-power-parity factors (no FX)"
    plan = "Pull OECD PPP series via SDMX; load dim_ppp by country/year (replaces flat seed PPP)."


class WorldBankIcp(ScaffoldConnector):
    name = "worldbank_icp"
    description = "World Bank ICP — authoritative country-level PPP"
    joins_on = ("country", "time")
    adds_signal = "authoritative PPP corroboration"
    plan = "Pull ICP indicators via the World Bank API; reconcile with OECD into dim_ppp."


class Numbeo(ScaffoldConnector):
    name = "numbeo"
    description = "Numbeo cost-of-living (city-level) — affordability comparisons"
    joins_on = ("country",)
    adds_signal = "cost-of-living index (affordability)"
    plan = "Collect COL indices (licence-aware); load dim_ppp.col_index. No tax/net pay (out of scope)."


class BlsOews(ScaffoldConnector):
    name = "bls_oews"
    description = "BLS OEWS (US, SOC×metro) + ONS ASHE / Eurostat / MOM / Job Bank / PLFS anchors"
    joins_on = ("country", "role", "time")
    adds_signal = "official aggregate salary anchors (keep per-country numbers honest)"
    plan = "Download national statistical wage tables; crosswalk to roles; store as calibration anchors."


# NOTE: a CompanyEnrich scaffold (Wikidata/GitHub-org → company size/industry) was
# removed here — strata is ROLES-only and never enriches or surfaces companies.

# NOTE: so_survey, gh_archive, google_trends and stack_exchange scaffolds were
# removed — those sources now have **real** extractor modules
# (backend/ingest/{so_survey,gh_archive,google_trends,stack_exchange}.py) wired
# into the registry's real-connector dispatch. Only genuinely-pending sources
# remain as scaffolds below.
SCAFFOLD_CONNECTORS = [
    DolOflc, PyPiNpm,
    Lightcast, Esco, Onet, OecdPpp, WorldBankIcp, Numbeo, BlsOews,
]
