# Phase 0 · System architecture

```
                ┌──────────── Channels ─────────────┐
 Website ─► Chat widget (Preact) ─┐                 │
 WhatsApp test (optional) ────────┤                 │
 Staff ─► Internal assistant + Admin dashboard (Next.js, Keycloak SSO)
                                  │
                                  ▼
                 ┌──────── Core API (FastAPI) ────────┐
                 │ Conversation orchestrator           │
                 │  ├─ Rules/prompt config (DB, versioned)
                 │  ├─ Guardrails (pre/post checks)    │
                 │  ├─ Tool layer (typed tools)        │
                 │  ├─ Handover engine                 │
                 │  └─ LLMProvider: groq|ollama|gemini|(anthropic off)
                 │ Retrieval service (pgvector, ACL-filtered)
                 │ Identity/OTP · Auth (OIDC) · Audit log
                 └───────────────┬────────────────────┘
                                 │ writes intent + idempotency key
                                 ▼
                  PostgreSQL ── outbox table ──► Worker(s)
                   (app data, vectors, logs)       │ at-least-once + dedupe
                                                   ▼
                   HubSpot · Jira SM · Google Calendar · Brevo/Mailpit
                   Slack

 Observability: Langfuse (LLM traces) · Grafana/Loki/Prometheus → Slack alerts
```

## Key design decisions

1. **One brain, many channels.** Web chat and messaging both call the same orchestrator. Channels only translate input and output.
2. **Tools never call external APIs directly.** A tool such as `book_meeting` writes an *intent* row plus an outbox row in one transaction, keyed by an idempotency key (`sha256(conversation_id + tool + normalised args)`). Workers carry it out, retry with backoff, and record the external ID. If a retry hits an existing key, it returns the stored result, so no double bookings or double messages.
3. **Calendar double-booking guard.** Free/busy is checked again inside the worker right before the event is created, and the Postgres table has an exclusion constraint on `(consultant_id, tstzrange)`.
4. **Config lives in the DB, not the code.** Prompts, FAQs, qualification questions, handover rules and messaging hours are versioned rows, edited and previewed from the dashboard, and hot-reloaded.
5. **Access-scoped retrieval.** Every chunk carries `client_id`, `sensitivity` and `allowed_roles`. The SQL `WHERE` filter is built from the user's Keycloak token **before** the vector search, so the LLM never sees data it shouldn't. Public and internal content live in separate tables.
6. **Provider-agnostic LLM.** OpenAI-style tool schemas are converted per adapter, and evals run against every adapter.
7. **Guardrails in layers.** The system prompt rules, then a pre-check (injection and topic classifier), then a post-check (a regex and classifier for prices, dates and promises, which rewrites the reply or hands over).
8. **Integrations sit behind adapters** (`CRMAdapter`, `TicketAdapter`, `CalendarAdapter`, `Notifier`), with fakes for tests. Swapping HubSpot for Twenty, or Jira for Zammad, means writing one new adapter.

## Core data model (first cut)
`contacts`, `companies`, `leads`, `conversations`, `messages`, `bookings`, `tickets_ref`, `handovers`, `tool_intents`, `outbox`, `config_versions`, `documents`, `chunks`, `audit_log`, `do_not_contact`, `consents`.

## Repo layout (Phase 1)
```
/apps/api        FastAPI core + worker
/apps/widget     Preact chat widget
/apps/web        Next.js dashboard + internal assistant
/packages/evals  promptfoo suites
/infra           compose files, Keycloak realm, Grafana dashboards
/docs            phase docs, runbook, guides
CLAUDE.md
```
