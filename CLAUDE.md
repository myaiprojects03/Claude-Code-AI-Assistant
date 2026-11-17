# CLAUDE.md

AI assistant for a (fictional) ~50-person IT consulting firm: website chat, existing-client
support, an internal knowledge assistant, integrations, and an admin dashboard.

## Hard rules
- **Free stack only.** Every dependency must be open source, self-hosted, or free-forever. If
  something would cost money, stop and flag it. Decisions: `docs/phase-0/01-stack-decisions.md`.
- **No voice/calling features** (removed from scope). Telegram is out of scope too. Clients get
  confirmations and reminders by email; the team gets them in Slack.
- **The Claude adapter stays disabled.** Default LLM chain: Groq → Gemini; Ollama for internal or
  confidential data and offline work.
- **Never cause external side effects from a tool.** Side-effecting tools return `Planned(effects)`;
  the outbox worker performs them. Read-only lookups (e.g. calendar free/busy) are fine.
  This is what guarantees no duplicate bookings or messages.
- **Client data separation.** Internal retrieval must filter by role and client *before* the
  vector search. Never put another client's data in a prompt. Client tools filter by the
  *verified* contact's company and answer "not found" for other clients' records.
- **Actions come from the person, not the model.** Side-effecting tools check the human's own
  latest message: verification codes and emails must be typed by the visitor, `send_email` needs an
  explicit "send" and a recipient the user typed. Never rely on the prompt alone for this.
- **Internal documents stay local.** The internal assistant uses `INTERNAL_LLM_CHAIN` (Ollama);
  don't route it to a cloud model without the user's decision.
- **Verification before client data.** Tools that touch a client's tickets, projects or bookings
  set `requires_verification = True`: the model doesn't see them, and they're refused, until
  `verify_code` succeeds. Never reveal whether an email belongs to a client.
- Public assistant rules (no prices, no promises, honest "I don't know") live in
  `config_store.DEFAULTS` and the `config_versions` table. Change prompts there, not in code.

## Layout
```
apps/api/src/assistant/
  api/            FastAPI app + routes (health, chat, admin)
  llm/            LLMProvider interface; openai_compat (groq/gemini/ollama), anthropic (off), fake
  tools/          ToolRegistry (+ idempotency, savepoints, ToolRejected), builtin + public tools
  kb/             embeddings (fastembed / hash), public KB, internal KB (access-filtered search)
  tools/clients.py  verification, tickets, projects, bookings, request_handover
  integrations/   CRM (local/hubspot), calendar (local/google), ticketing (local/jira) adapters
  booking.py      slots and reservation   leads.py  contacts, leads, CRM sync
  identity.py     email + one-time code   reminders.py  confirmations and scheduled reminders
  handover.py     triggers, routing, Slack/email notification, office-hours promises
  qualification.py  deterministic lead rules   guardrails.py  reply safety net
  outbox/         worker (SKIP LOCKED, backoff, dead-letter, alerts) + handlers
  orchestrator.py one brain for all channels
  models.py       data model (CHECK-constrained string enums)
  config_store.py versioned no-code config with defaults
  auth.py         Keycloak JWT (staff roles) + visitor session tokens
apps/widget/      Preact chat widget (single-file build)
apps/api/evals/   eval cases + runner against the real model
content/public/   approved public content (the only source for public answers)
content/internal/ confidential internal documents (front matter: client, sensitivity, roles)
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
- Content and seed: `../../.venv/Scripts/assistant-ingest` (re-index content/public),
  `assistant-ingest-internal` (content/internal), `assistant-seed`
- Local model: Ollama at `D:\Ollama` (models in `D:\Ollama\models`); start with
  `D:\Ollama\ollama.exe serve`. Internal evals: `python -m evals.run_evals --internal`
- Evals (real model): `../../.venv/Scripts/python -m evals.run_evals [--only id,id] [--dry-run]`
- Widget: `cd ../widget && npm run dev` (demo at /demo/) or `npm run build`. npm's cache is set
  to D: in `apps/widget/.npmrc`.

## Conventions
- Python 3.11+, async everywhere, SQLAlchemy 2 typed models, Pydantic v2, ruff (line length 100).
- New enum values: add them to the `StrEnum` in models.py plus a migration (CHECK constraint).
- New side effect: a tool's `plan(ctx, args, key)` returns `Planned([OutboxSpec(kind, payload)])`
  (raise `ToolRejected` to refuse cleanly), and you register a handler in `build_handlers()`
  (`outbox/handlers.py`). A handler raises `PermanentError` for non-retryable failures.
- Public content changes: edit `content/public/*.md`, then run `assistant-ingest`. Never put
  prices, dates or guarantees in it.
- Every new external integration goes behind an adapter with a fake for tests.
- Tests: every feature gets unit tests; anything touching SQL gets a `requires_db` test.
- Staff data access gets an `audit.record(...)` call.
