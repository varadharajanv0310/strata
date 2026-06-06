"""Adzuna connector — salary aggregates / histograms per title·category·region
across the 7 targets (§6). Free tier via app_id + app_key (env). Skips+flags when
credentials are absent. Many postings lack salary — Adzuna's aggregate estimates
are used for market-representative salary calibration.
"""
from __future__ import annotations

import requests

from backend.core.config import settings
from backend.core.logging import get_logger
from backend.ingest.base import BaseConnector

log = get_logger("ingest.adzuna")

# Adzuna country endpoints for our 7 markets
ADZUNA_CC = {"IN": "in", "US": "us", "GB": "gb", "CA": "ca", "AU": "au", "SG": "sg", "DE": "de"}
BASE = "https://api.adzuna.com/v1/api/jobs"


class AdzunaConnector(BaseConnector):
    name = "adzuna"
    description = "salary histograms + historical salary trends per category/region"
    requires = ("adzuna_app_id", "adzuna_app_key")
    joins_on = ("country", "role", "time")
    adds_signal = "market-representative salary calibration (aggregate estimates)"

    def _auth(self) -> dict:
        return {"app_id": settings.adzuna_app_id, "app_key": settings.adzuna_app_key}

    def land_raw(self, limit: int | None = None) -> int:
        total = 0
        for code, cc in ADZUNA_CC.items():
            try:
                # category-level salary histogram (aggregate) for the country
                hist = requests.get(f"{BASE}/{cc}/histogram", params={**self._auth(), "what": "developer"}, timeout=45)
                hist.raise_for_status()
                cats = requests.get(f"{BASE}/{cc}/categories", params=self._auth(), timeout=45)
                cats.raise_for_status()
                self.write_raw_json(f"{code}_histogram.json", hist.json())
                self.write_raw_json(f"{code}_categories.json", cats.json())
                total += 1
            except Exception as e:
                log.warning("adzuna %s failed: %s", code, e)
        return total

    def build_staging(self) -> int:
        import json

        import pandas as pd

        rows = []
        for f in self.raw_dir().glob("*_histogram.json"):
            code = f.name.split("_")[0]
            hist = json.loads(f.read_text(encoding="utf-8")).get("histogram", {})
            for band, count in hist.items():
                rows.append({"country": code, "salary_band": int(band), "postings": int(count)})
        if not rows:
            return 0
        df = pd.DataFrame(rows)
        df.to_parquet(self.staging_dir() / "salary_histograms.parquet", index=False)
        return len(df)
