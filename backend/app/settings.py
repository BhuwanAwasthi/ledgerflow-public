"""Environment settings. Secrets come from env vars only (a local .env is read by pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"), extra="ignore")

    database_url: str = f"sqlite:///{(BACKEND_DIR / 'ledgerflow.db').as_posix()}"
    config_dir: Path = REPO_ROOT / "config"
    data_dir: Path = REPO_ROOT / "data"

    storage_backend: str = "local"  # local | supabase
    storage_dir: Path = REPO_ROOT / "storage"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = "documents"

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    # azure (needs AZURE_OPENAI_*) | fake (recorded outputs from backend/tests/llm_fixtures, for UI work offline)
    llm_backend: str = "azure"
    llm_fixtures_dir: Path = BACKEND_DIR / "tests" / "llm_fixtures"

    cors_origins: str = "http://localhost:5173"
    public_app_url: str = "http://localhost:5173"

    # Agents whose handler module does not exist yet run as StubAgent; set true to force stubs everywhere.
    force_stub_agents: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if "http://localhost:5173" not in origins:
            origins.append("http://localhost:5173")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
