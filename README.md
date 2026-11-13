# AI Assistant (free stack)

A Claude Code portfolio project: an AI front door for an IT consulting firm, built entirely on
free and open-source tools. See [docs/phase-0](docs/phase-0/README.md) for the decisions.

## Status
- [x] Phase 0: discovery and decisions
- [x] Phase 1: foundation (46 tests passing against Postgres)

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
