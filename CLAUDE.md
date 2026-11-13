# CLAUDE.md

AI assistant for a (fictional) ~50-person IT consulting firm: website chat, existing-client
support, an internal knowledge assistant, integrations, and an admin dashboard.

## Hard rules
- **Free stack only.** Every dependency must be open source, self-hosted, or free-forever. If
  something would cost money, stop and flag it. Decisions: `docs/phase-0/01-stack-decisions.md`.
- **No voice/calling features** (removed from scope). Reminders go by email and Telegram.
- **The Claude adapter stays disabled.** Default LLM chain: Groq → Gemini; Ollama for internal or
  confidential data and offline work.
- **Never call external APIs from a tool.** Side-effecting tools return `OutboxSpec`s; the outbox
  worker performs them. That's what guarantees no duplicate bookings or messages.
- **Client data separation.** Internal retrieval must filter by role and client *before* the
  vector search. Never put another client's data in a prompt.
- Public assistant rules (no prices, no promises, honest "I don't know") live in
  `config_store.DEFAULTS` and the `config_versions` table. Change prompts there, not in code.

## Layout
```
apps/api/src/assistant/
  api/            FastAPI app + routes (health, chat, admin)
  llm/            LLMProvider interface; openai_compat (groq/gemini/ollama), anthropic (off), fake
  tools/          ToolRegistry (+ idempotency), builtin tools
  outbox/         worker (SKIP LOCKED, backoff, dead-letter, alerts) + handlers
  orchestrator.py one brain for all channels
  models.py       data model (CHECK-constrained string enums)
  config_store.py versioned no-code config with defaults
  auth.py         Keycloak JWT (staff roles) + visitor session tokens
infra/            compose (postgres+pgvector, keycloak, mailpit; obs profile)
docs/phase-N/     phase decisions and docs
```

## Commands (run from apps/api, using the repo venv)
- Dev stack: `powershell -File ../../scripts/stack.ps1 up|down|status|stop-vm`. Podman data is on
  D:\PodmanData (C: is full), so never call `podman machine` without `XDG_DATA_HOME` set.
  Postgres is on port **5433**.
- Tests: `../../.venv/Scripts/python -m pytest -q`. DB tests skip unless `TEST_DATABASE_URL` is set
  (`postgresql+asyncpg://assistant:assistant-dev@localhost:5433/assistant_test`).
- Lint and format: `../../.venv/Scripts/ruff check src tests && ../../.venv/Scripts/ruff format src tests`
- Run the API: `../../.venv/Scripts/uvicorn assistant.api.main:app --reload --port 8000`
- Run the worker: `../../.venv/Scripts/assistant-worker`
- Migrations: `../../.venv/Scripts/alembic revision --autogenerate -m "..."`, then `alembic upgrade head`

## Conventions
- Python 3.11+, async everywhere, SQLAlchemy 2 typed models, Pydantic v2, ruff (line length 100).
- New enum values: add them to the `StrEnum` in models.py plus a migration (CHECK constraint).
- New side effect: a tool `plan()` returns `OutboxSpec(kind, payload)`, and you register a handler
  in `outbox/handlers.py`. A handler raises `PermanentError` for non-retryable failures.
- Every new external integration goes behind an adapter with a fake for tests.
- Tests: every feature gets unit tests; anything touching SQL gets a `requires_db` test.
- Staff data access gets an `audit.record(...)` call.
