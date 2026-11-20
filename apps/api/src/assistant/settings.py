import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (apps/api/src/assistant/settings.py -> 4 levels up)
REPO_ROOT = Path(__file__).resolve().parents[4]
_ROOT_ENV = REPO_ROOT / ".env"
# Production runs on the same PC as dev: ASSISTANT_ENV_FILE=.env.prod makes it read ONLY that
# file, so dev's real Slack/Jira settings in .env can never leak into the public demo.
_ENV_FILES = (
    (os.environ["ASSISTANT_ENV_FILE"],)
    if os.environ.get("ASSISTANT_ENV_FILE")
    else (_ROOT_ENV, ".env")
)


DEV_SECRET = "dev-insecure-secret-change-me-0123456789"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

    app_env: str = "dev"
    app_secret: str = DEV_SECRET

    database_url: str = "postgresql+asyncpg://assistant:assistant-dev@localhost:5433/assistant"

    # LLM
    llm_chain: str = "groq,groq_alt,gemini"
    # Internal knowledge assistant: confidential documents stay on this machine.
    internal_llm_chain: str = "ollama"
    ollama_timeout_s: float = 600.0  # CPU inference is slow; a 7B proposal can take 5+ min
    ollama_max_tokens: int = 2000  # a full proposal is ~1,000 tokens; stops runaway loops
    llm_timeout_s: float = 30.0
    llm_max_tool_steps: int = 5
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    # Groq rate limits are per model, so a second free model doubles capacity.
    groq_alt_model: str = "openai/gpt-oss-20b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:3b"  # ~28s/answer on an i5-8500 CPU; 7b is ~2x slower
    anthropic_enabled: bool = False
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

    # Auth
    oidc_issuer: str = "http://localhost:8080/realms/assistant"
    oidc_audience: str = "assistant-api"
    # Where to fetch Keycloak's signing keys; blank = {oidc_issuer}/protocol/.../certs. In
    # production the issuer is the public URL, but the keys are fetched on this machine.
    oidc_jwks_url: str = ""
    session_token_ttl_s: int = 24 * 3600

    # Email
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "assistant@example.test"
    # Real mail servers (Gmail app password, Brevo): sign in over STARTTLS. Blank = Mailpit.
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = False

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

    # Ticketing
    ticketing_provider: str = "local"  # local (DB only) | jira
    jira_base_url: str = ""  # e.g. https://yourteam.atlassian.net
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "SUP"
    jira_issue_type: str = "Task"  # must exist in the project (see assistant-check-integrations)

    # Links in handover messages (Phase 6 dashboard)
    dashboard_url: str = "http://localhost:3000"

    # Public web chat
    allowed_origins: str = "http://localhost:5173,http://localhost:3000"
    rate_limit_per_minute: int = 20  # per client IP on public chat endpoints
    # Phase 7 hardening
    # Only behind a reverse proxy you control: otherwise anyone can fake X-Forwarded-For
    # and dodge the per-IP rate limit.
    trust_proxy_headers: bool = False
    internal_rate_limit_per_minute: int = 12  # per staff member, knowledge assistant
    max_body_bytes: int = 256 * 1024  # larger request bodies are refused (413)
    # /metrics: open in dev; outside dev only from this machine or with this bearer token.
    metrics_token: str = ""
    # The knowledge assistant may only email these domains, plus the domains of clients the
    # person has access to (stops confidential documents being sent to personal addresses).
    firm_email_domains: str = "acme.example"  # the (fictional) firm's own domain
    # GDPR retention (days). Applied by the outbox worker every few hours.
    retention_conversation_days: int = 365  # visitor chats with nothing still open
    retention_internal_chat_days: int = 180  # staff knowledge-assistant chats
    retention_code_days: int = 7  # verification-code records after they expire
    retention_outbox_days: int = 90  # sent or failed message records
    retention_audit_days: int = 730  # audit log (ISO 27001 evidence)
    retention_interval_s: int = 6 * 3600

    @model_validator(mode="after")
    def _secure_outside_dev(self) -> "Settings":
        if self.app_env != "dev" and (self.app_secret == DEV_SECRET or len(self.app_secret) < 32):
            raise ValueError("APP_SECRET must be set to 32+ random characters outside dev")
        return self

    @property
    def llm_chain_list(self) -> list[str]:
        return [p.strip() for p in self.llm_chain.split(",") if p.strip()]

    @property
    def internal_llm_chain_list(self) -> list[str]:
        return [p.strip() for p in self.internal_llm_chain.split(",") if p.strip()]

    @property
    def firm_email_domains_list(self) -> list[str]:
        return [d.strip().lower() for d in self.firm_email_domains.split(",") if d.strip()]

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
