"""Typed application settings (pydantic-settings).

All configuration — DB URLs, crawl scope, GPU/batch settings, Job-Score weights,
source API keys — is loaded here from environment / `.env`. Nothing is hard-coded
elsewhere (brief §2). Secrets default to empty so connectors skip+flag gracefully.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# repo root:  backend/core/config.py -> parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- core ----
    env: str = Field("local", validation_alias="STRATA_ENV")
    log_level: str = Field("INFO", validation_alias="STRATA_LOG_LEVEL")

    # ---- databases ----
    database_url: str = "sqlite:///backend/data/app.db"
    duckdb_path: str = "backend/data/warehouse.duckdb"
    data_dir: str = "backend/data"

    # ---- API ----
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:5191,http://127.0.0.1:5173"

    # ---- auth ----
    jwt_secret: str = "change-me-in-production-please"
    jwt_alg: str = "HS256"
    jwt_expire_minutes: int = 10080

    # ---- pipeline tunables ----
    role_volume_floor: int = 200
    jobscore_w_demand: float = 0.42
    jobscore_w_interest: float = 0.25
    jobscore_w_salary: float = 0.33
    forecast_horizon_months: int = 12
    forecast_backtest_periods: int = 6

    # ---- Common Crawl scope ----
    cc_recent_crawls: int = 3
    cc_historical_years: int = 4
    cc_index_server: str = "https://index.commoncrawl.org"
    cc_target_domains: str = ""

    # ---- source credentials (optional) ----
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    lightcast_client_id: str = ""
    lightcast_client_secret: str = ""
    # Cedefop Skills-OVATE open-data download URL — the portal moves periodically, so
    # the connector reads this override via getattr and stays gracefully disabled when unset.
    cedefop_ovate_url: str | None = Field(None, validation_alias="CEDEFOP_OVATE_URL")
    # USAJOBS API: free Authorization-Key + a contact email (required in the User-Agent).
    # Both optional → the connector skips+flags gracefully when absent.
    usajobs_api_key: str | None = Field(None, validation_alias="USAJOBS_API_KEY")
    usajobs_email: str | None = Field(None, validation_alias="USAJOBS_EMAIL")
    # BLS (OEWS/baselines) + Hugging Face tokens. Declared so they load from .env;
    # some connectors read these via ``os.environ.get`` rather than ``settings`` (a
    # historical inconsistency), so we bridge them into os.environ below. Optional.
    bls_api_key: str | None = Field(None, validation_alias="BLS_API_KEY")
    hf_token: str | None = Field(None, validation_alias="HF_TOKEN")

    # ---- GPU / ML ----
    ml_device: str = "cuda"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embed_batch_size: int = 256
    ml_vram_budget_gb: int = 16

    # ---- LLM corpus-extraction backend ----
    # "auto" prefers vLLM (Linux/WSL) and falls back to a local Ollama server (the
    # native-Windows path); "vllm" / "ollama" force one. Ollama needs its server up
    # (``ollama serve``) and the model pulled (``ollama pull <model>``).
    llm_backend: str = "auto"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_concurrency: int = 4          # parallel /api/chat requests per batch
    ollama_num_ctx: int = 8192           # context window passed to Ollama
    # LLM-as-judge (extract_validate): a STRONGER/different model than the extractor.
    judge_model: str = "Qwen/Qwen2.5-14B-Instruct"   # vLLM judge id
    ollama_judge_model: str = "gpt-oss:20b"          # native-Windows judge (strongest local)

    # cedefop_ovate.py reads the override as ``getattr(settings, "CEDEFOP_OVATE_URL")``
    # (uppercase). pydantic stores the field lowercased, so expose an uppercase alias
    # property to satisfy that getattr; stays None (gracefully disabled) when unset.
    @property
    def CEDEFOP_OVATE_URL(self) -> str | None:  # noqa: N802
        return self.cedefop_ovate_url

    # ---------- derived paths / helpers ----------
    def _resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def data_path(self) -> Path:
        return self._resolve(self.data_dir)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duckdb_file(self) -> Path:
        return self._resolve(self.duckdb_path)

    @property
    def raw_dir(self) -> Path:
        return self.data_path / "raw"

    @property
    def staging_dir(self) -> Path:
        return self.data_path / "staging"

    @property
    def warehouse_dir(self) -> Path:
        return self.data_path / "warehouse"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cc_target_domains_list(self) -> list[str]:
        return [d.strip() for d in self.cc_target_domains.split(",") if d.strip()]

    @property
    def jobscore_weights(self) -> dict[str, float]:
        return {
            "demand": self.jobscore_w_demand,
            "interest": self.jobscore_w_interest,
            "salary": self.jobscore_w_salary,
        }

    @property
    def resolved_database_url(self) -> str:
        """Make a relative sqlite path absolute so it works regardless of CWD."""
        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix):
            raw = url[len(prefix):]
            p = Path(raw)
            if not p.is_absolute():
                p = (PROJECT_ROOT / p).resolve()
            return f"{prefix}{p.as_posix()}"
        return url

    def ensure_dirs(self) -> None:
        for d in (self.data_path, self.raw_dir, self.staging_dir, self.warehouse_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# pydantic-settings loads `.env` into the Settings object, NOT into os.environ. A few
# connectors (baselines, huggingface) read their keys via ``os.environ.get(...)``, so
# without this bridge a key correctly placed in `.env` would be silently ignored at
# run time. Push the os.environ-consumed secrets across — but never clobber a real
# environment variable (keeps test/smoke isolation, which sets real env vars, intact).
import os as _os  # noqa: E402

for _name, _val in (("BLS_API_KEY", settings.bls_api_key), ("HF_TOKEN", settings.hf_token)):
    if _val and not _os.environ.get(_name):
        _os.environ[_name] = _val
