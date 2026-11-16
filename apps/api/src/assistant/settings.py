from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (apps/api/src/assistant/settings.py -> 4 levels up)
REPO_ROOT = Path(__file__).resolve().parents[4]
_ROOT_ENV = REPO_ROOT / ".env"


DEV_SECRET = "dev-insecure-secret-change-me-0123456789"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(_ROOT_ENV, ".env"), extra="ignore")

    app_env: str = "dev"
    app_secret: str = DEV_SECRET

    database_url: str = "postgresql+asyncpg://assistant:assistant-dev@localhost:5433/assistant"

    # LLM
    llm_chain: str = "groq,groq_alt,gemini"
    llm_timeout_s: float = 30.0
    llm_max_tool_steps: int = 5
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    # Groq rate limits are per model, so a second free model doubles capacity.
    groq_alt_model: str = "openai/gpt-oss-20b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b"
    anthropic_enabled: bool = False
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

    # Auth
    oidc_issuer: str = "http://localhost:8080/realms/assistant"
    oidc_audience: str = "assistant-api"
    session_token_ttl_s: int = 24 * 3600

    # Email
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "assistant@example.test"

    # Alerts / notifications
    slack_alert_webhook_url: str = ""
    slack_notify_webhook_url: str = ""

    # Outbox worker
    outbox_batch_size: int = 10
    outbox_poll_interval_s: float = 1.0
    outbox_max_attempts: int = 6
    outbox_stuck_after_s: int = 300
    worker_metrics_port: int = 9101

    history_window: int = Field(default=30, description="Messages sent to the LLM per turn")

    # Knowledge base / embeddings (local CPU, free)
    embedder: str = "fastembed"  # fastembed | hash (tests/offline)
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_cache_dir: str = str(REPO_ROOT / ".cache" / "fastembed")
    kb_content_dir: str = str(REPO_ROOT / "content" / "public")
    kb_top_k: int = 4
    kb_min_score: float = 0.55  # cosine similarity below this = "not in approved content"

    # CRM
    crm_provider: str = "local"  # local (DB only) | hubspot
    hubspot_token: str = ""

    # Calendar
    calendar_provider: str = "local"  # local (DB bookings only) | google
    google_service_account_file: str = ""

    # Public web chat
    allowed_origins: str = "http://localhost:5173,http://localhost:3000"
    rate_limit_per_minute: int = 20  # per client IP on public chat endpoints

    @model_validator(mode="after")
    def _secure_outside_dev(self) -> "Settings":
        if self.app_env != "dev" and (self.app_secret == DEV_SECRET or len(self.app_secret) < 32):
            raise ValueError("APP_SECRET must be set to 32+ random characters outside dev")
        return self

    @property
    def llm_chain_list(self) -> list[str]:
        return [p.strip() for p in self.llm_chain.split(",") if p.strip()]

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
