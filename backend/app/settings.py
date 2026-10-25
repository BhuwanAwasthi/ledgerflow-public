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
    # dev only: seconds FakeLLM waits per call, so a fake run is slow enough to watch in the live view
    fake_llm_delay_s: float = 0.0

    # Auth: `supabase` verifies Supabase Auth JWTs (project JWKS, else the legacy HS256 secret). `dev` is a stub
    # login for local and e2e runs only (app.auth.check_dev_mode refuses it anywhere near production).
    auth_mode: str = "supabase"  # supabase | dev
    supabase_jwt_secret: str = ""  # legacy HS256 projects only
    supabase_jwks_url: str = ""  # default: <SUPABASE_URL>/auth/v1/.well-known/jwks.json
    dev_jwt_secret: str = ""  # AUTH_MODE=dev signing secret (random per process when empty)
    # HMAC secret of the 60 s file tokens (PDF, page PNG, downloads); random per process when empty
    file_token_secret: str = ""
    owner_emails: str = ""  # comma-separated: the owner console (P7)
    render: str = ""  # Render sets RENDER=true on its instances (dev auth refuses to start there)

    cors_origins: str = "http://localhost:5173"
    public_app_url: str = "http://localhost:5173"

    # Agents whose handler module does not exist yet run as StubAgent; set true to force stubs everywhere.
    force_stub_agents: bool = False

    @property
    def jwks_url(self) -> str:
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json" if self.supabase_url else ""

    @property
    def jwt_issuer(self) -> str | None:
        return f"{self.supabase_url.rstrip('/')}/auth/v1" if self.supabase_url else None

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        if "http://localhost:5173" not in origins:
            origins.append("http://localhost:5173")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
