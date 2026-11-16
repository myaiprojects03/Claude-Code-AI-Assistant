# Phase 2 · Client chat assistant (MVP)

A website chat that explains services from approved content, qualifies leads, and books
discovery calls. Everything runs on the free stack; HubSpot and Google Calendar are optional
switches.

## What's built

| Piece | Where | Notes |
|---|---|---|
| Chat widget | `apps/widget` | Preact, one ~21 KB file, Shadow DOM, consent first, light/dark, mobile |
| Approved content | `content/public/*.md` | Services, FAQs, company info. **No prices, dates or guarantees.** |
| Knowledge search | `kb/public.py`, tool `search_knowledge` | fastembed `bge-small-en-v1.5` on CPU, pgvector HNSW, 0.55 similarity cut-off |
| Qualification | `qualification.py`, tool `update_lead_profile` | Deterministic rules from config `qualification.rules` (editable without code) |
| Leads and contacts | `leads.py` | Our DB first; CRM sync queued once per distinct lead state |
| Booking | `booking.py`, tools `find_available_slots`, `book_discovery_call` | Rules from config `booking.rules`; DB constraint prevents double-booking |
| Guardrails | `guardrails.py` | Replaces any public reply containing prices, rates, discounts, guarantees or delivery commitments |
| CRM | `integrations/crm.py` | `local` (default) or `hubspot` |
| Calendar | `integrations/calendar.py` | `local` (default) or `google` (service account, idempotent event ids) |
| Rate limit | `ratelimit.py` | 20 requests/min per client on public chat endpoints |
| Evals | `apps/api/evals` | 16 scripted conversations against the real model |

## Flow

```
visitor ─► widget ─► POST /v1/chat/messages ─► orchestrator ─► LLM (Groq)
                                                   │  tools
       search_knowledge ◄── approved content (pgvector)
       update_lead_profile ─► leads/contacts ─► outbox: crm.sync
       find_available_slots ◄── consultants + bookings (+ Google free/busy)
       book_discovery_call ─► booking row (no-overlap constraint)
                              └► outbox: calendar.create_event, email.send (confirmation)
       final reply ─► guardrail check ─► widget
```

## Run it locally

```bash
powershell -File scripts/stack.ps1 up
cd apps/api
../../.venv/Scripts/alembic upgrade head
../../.venv/Scripts/assistant-seed        # two fictional consultants
../../.venv/Scripts/assistant-ingest      # index content/public
../../.venv/Scripts/uvicorn assistant.api.main:app --reload --port 8000
../../.venv/Scripts/assistant-worker      # sends emails (Mailpit), creates events, syncs CRM
cd ../widget && npm install && npm run dev   # demo site at http://localhost:5173/demo/
```

Confirmation emails appear in Mailpit at http://localhost:8025.

## Evals (need a free Groq key)

```bash
cd apps/api
../../.venv/Scripts/python -m evals.run_evals
```

Categories: knowledge answers, honesty ("I don't know"), pricing, promises, prompt injection,
confidentiality, off-topic, qualification, urgent incidents, and a full booking flow. A case
also fails if the guardrail had to step in, because the model should behave on its own.

### Latest results (2026-11-16)
**16/16 passing on two consecutive runs** with `openai/gpt-oss-120b` (Groq), falling back to
`openai/gpt-oss-20b`. The first real run found these problems, and all are fixed:

| Finding | Fix |
|---|---|
| `llama-3.3-70b-versatile` was retired by Groq | Default model is now `openai/gpt-oss-120b` |
| Free tier allows 8,000 tokens/min per model, and each turn uses about 1.3–4k tokens, so the assistant gave up after a rate limit | The provider waits out short 429s (≤ 8s, using Groq's "try again in" hint). A second free model (`groq_alt`, own limit) is used next. |
| Eval runner counted "I'm having trouble" fallback replies as passes | Degraded turns now fail. A rate-limited case is retried once after 30s. |
| Guardrail blocked a *good* answer ("No one can guarantee 100% security") | Promise rules ignore negated sentences; affirmative promises are still blocked |
| Model gave a pizza recipe | Prompt now has an explicit on-topic rule |
| Model didn't record lead details | Prompt: call `update_lead_profile` in the same turn as the details are shared |
| Model asked qualification questions before booking, and said "pencilled in" before anything was booked | Prompt: booking comes first; never claim a booking until the tool returns `queued` |
| `qwen/qwen3.8-27b` produced malformed tool calls on Groq | Not used as the fallback |

**Capacity on the free tier:** roughly 4–6 visitor messages a minute across both models
before waits kick in. Plenty for a demo or a quiet site; a busy production site would need
Groq's paid tier or a self-hosted model. (Gemini's free tier, as a third model in the chain,
adds more headroom at no cost.)

## Turning on the real integrations (all free)

**HubSpot Free CRM**
1. Create a free HubSpot account. Go to Settings → Integrations → Private apps → Create.
2. Scopes: `crm.objects.contacts.read`, `crm.objects.contacts.write`.
3. In `.env`: `CRM_PROVIDER=hubspot` and `HUBSPOT_TOKEN=<token>`.

**Google Calendar**
1. In a free Google Cloud project, enable the Calendar API and create a service account with
   a JSON key. Save it outside the repo.
2. Share each consultant's calendar with the service account's email ("Make changes to events").
3. Set each consultant's `calendar_id` (usually their Google email) in the `consultants` table.
4. In `.env`: `CALENDAR_PROVIDER=google` and `GOOGLE_SERVICE_ACCOUNT_FILE=<path>`.

Without domain-wide delegation, a service account can't send Google invites to visitors, so
our own confirmation email does that job.

## Known limits (tracked for later phases)
- Rescheduling or cancelling bookings needs a consultant (Phase 3).
- The rate limiter is in-memory, one API instance only (Phase 7).
- Guardrail regexes are English-only.
