# AI Assistant (free stack)

A Claude Code portfolio project: an AI front door for an IT consulting firm, built entirely on
free and open-source tools. See [docs/phase-0](docs/phase-0/README.md) for the decisions.

## Status
- [x] Phase 0: discovery and decisions
- [x] Phase 1: foundation
- [x] Phase 2: client chat assistant MVP ([docs](docs/phase-2/README.md)): widget, knowledge base, qualification, booking, guardrails.
- [x] Phase 3: existing-client support and handover ([docs](docs/phase-3/README.md)): verification, tickets, projects, rescheduling, reminders, handover routing, public holidays, real Slack + Jira. 148 tests and 24/24 evals passing.
- ~~Phase 4: voice agent~~ (removed from scope)
- [x] Phase 5: internal knowledge assistant ([docs](docs/phase-5/README.md)): local Ollama model, permission-filtered search over confidential documents, cited sources, drafting with a fact guard, leak tests. 193 tests passing; internal evals 10/10 on qwen2.5 3B and 7B.
- [x] Phase 6: staff dashboard ([docs](docs/phase-6/README.md)): Keycloak SSO, overview with conversion, escalation and unanswered-topic analytics, handovers, conversations, leads, bookings, tickets, settings and website-content editors, knowledge assistant, client access, handover ownership (take, reassign, resolve), form-based rule editors with previews, search, live records in the knowledge assistant, live Jira statuses. 222 tests passing.
- [x] Phase 7: security, compliance and hardening ([docs](docs/phase-7/README.md)): access-control and prompt-injection testing on every channel, 11 security fixes, dependency and secret scans, UK GDPR export/erasure/retention, audit log viewer, ISO 27001 control map, failure drills and load tests. 252 tests passing.

## Quick start (Windows)

Podman's data lives on **D:\PodmanData** (C: is nearly full). `scripts/stack.ps1` sets that up
for every call, so use it rather than calling `podman` directly.

1. One-time: Podman CLI (`winget install RedHat.Podman`), then
   `$env:XDG_DATA_HOME='D:\PodmanData'; podman machine init --cpus 4 --memory 6144`
2. Python environment and config:
   ```bash
   python -m venv .venv
   .venv/Scripts/python -m pip install -e "apps/api[dev]"
   copy .env.example .env      # then add a free GROQ_API_KEY
   ```
3. Start the stack:
   ```bash
   powershell -File scripts/stack.ps1 up
   ```
   - Postgres + pgvector on `localhost:5433` (5432 belongs to the PostgreSQL 18 service on this PC)
   - Keycloak on http://localhost:8080 (admin / admin-dev); realm `assistant` with dev users
     `admin.dev`, `manager.dev`, `consultant.dev` (passwords in `infra/keycloak/realm-assistant.json`)
   - Mailpit on http://localhost:8025, which catches every outgoing email
4. Schema and tests (from `apps/api`):
   ```bash
   ../../.venv/Scripts/alembic upgrade head
   set TEST_DATABASE_URL=postgresql+asyncpg://assistant:assistant-dev@localhost:5433/assistant_test
   ../../.venv/Scripts/python -m pytest -q
   ```
5. Run it (from `apps/api`):
   ```bash
   ../../.venv/Scripts/uvicorn assistant.api.main:app --reload --port 8000
   ../../.venv/Scripts/assistant-worker
   ../../.venv/Scripts/python ../../scripts/chat_cli.py
   ```
6. Shut down: `powershell -File scripts/stack.ps1 stop-vm` (stops containers and frees the VM's RAM).

No API key? Set `LLM_CHAIN=ollama` (local) or `LLM_CHAIN=fake` (scripted echo, offline).

## API (Phase 1)
| Method | Path | Who |
|---|---|---|
| GET | `/healthz`, `/readyz`, `/metrics` | anyone |
| POST | `/v1/chat/sessions` | visitor (requires consent) |
| POST | `/v1/chat/messages` | visitor session token |
| POST | `/v1/internal/conversations[/{id}/messages]` | any staff role (owner only) |
| GET | `/v1/conversations[/{id}]` | manager, admin (views are audited) |
| GET/POST | `/v1/config/{key}`, `/drafts`, `/versions/{v}/publish` | read: manager+; write: admin |

OpenAPI docs: http://localhost:8000/docs
