# Project overview: AI assistant for an IT consulting firm

*Source material for a project document. Everything below describes what was actually
built, tested and deployed. The firm, its clients and people are fictional.*

---

## 1. Summary

An AI assistant for **Acme IT Consulting**, a fictional 50-person IT consulting firm in the
UK. One assistant "brain" serves four audiences:

1. **Website visitors (prospects):** answers questions from approved content, qualifies leads
   with fixed rules, and books free discovery calls with consultants.
2. **Existing clients:** after an email check with a one-time code, they can see their own
   tickets and projects, raise new tickets, and reschedule or cancel calls.
3. **Staff (consultants, managers):** a private knowledge assistant over confidential
   internal documents, running on a local model so nothing leaves the machine.
4. **Admins and managers:** a staff dashboard for handovers, conversations, leads, bookings,
   tickets, website content, settings, privacy requests and the audit log.

When the assistant can't or shouldn't handle something (pricing, complaints, legal, urgent
outages, security incidents, or simply "I want a person"), it **hands over to a human**:
the team is alerted in Slack and by email, and the visitor is told honestly when to expect a
reply.

Two constraints shaped every decision:
- **A completely free stack:** open source, self-hosted, or free-forever tiers, with no
  service that needs a payment card even to sign up.
- **Safety by design rather than by prompt:** prices, promises, client data separation and
  side effects are enforced in code, not left to the model's judgement.

It is built as a portfolio project, in 8 phases (one, voice calling, was removed from scope),
and is **deployed and publicly reachable** as a live demo.

---

## 2. The problem it solves

A small consulting firm loses leads out of hours, spends consultant time answering the same
questions, and keeps client knowledge in scattered documents. The assistant aims to:

- answer prospects instantly, 24/7, **without** inventing prices or promises;
- turn interested visitors into **qualified leads and booked calls**;
- let existing clients **self-serve** ticket and project status safely;
- get the **right person** involved quickly when it matters, with context;
- let consultants **find answers in internal documents** without exposing one client's data
  to another, or sending confidential material to a cloud AI;
- give managers **visibility** (conversion, escalations, unanswered questions) and admins
  **control** (content, rules, access, GDPR), without a developer.

---

## 3. Who uses it and what they can do

| Role | What they can do |
|---|---|
| Website visitor | Ask about services, get honest "I don't know" answers, book a discovery call |
| Existing client (verified) | See own company's tickets and projects, raise tickets, reschedule/cancel calls, ask for a person |
| Consultant | Handovers (team queue and their own), bookings, tickets, knowledge assistant (firm-wide docs + clients they're granted) |
| Manager | All of the above, plus every client's documents, conversations, leads, reassigning handovers, viewing content and settings |
| Admin | Everything, plus publishing content and settings, client access, privacy requests (export/erase), audit log |

Real-life example: on a Saturday evening, a law firm's office manager asks the website chat
whether Acme does Microsoft 365 migrations. The assistant explains from the firm's service
pages, declines to quote a day rate, asks a few qualifying questions, and books a Monday
9:30 call. The visitor gets a confirmation email and the consultant gets a Slack message.
On Monday the consultant opens the dashboard, sees the booking and the qualified lead, and
asks the knowledge assistant to summarise the firm's migration method before the call.

---

## 4. Features in detail

### 4.1 Website chat (public assistant)
- Embeddable **Preact widget**, a single script tag, with a consent step before anything is
  stored (consent recorded with the privacy-notice version).
- Answers **only from approved public content** (FAQ and service pages) via retrieval;
  below a similarity threshold it says it doesn't know and offers a consultant.
- **No prices, rates, guarantees or delivery promises:** a prompt rule plus a deterministic
  reply guardrail that replaces any reply containing them with a safe fallback.
- **Lead qualification by deterministic rules** (company size, need, budget, timeline,
  enterprise triggers), editable as forms in the dashboard, not left to the model.
- **Discovery-call booking:** checks real availability (consultant skills, working hours,
  UK bank holidays, existing bookings), offers slots, books exactly once. Database
  constraints prevent double booking even under concurrent requests.
- Confirmations and reminders: **email to the client** (at booking, 24 h before) and
  **Slack to the consultant** (at booking, 1 h before), respecting messaging hours.
- Records "unanswered topics" (questions no content covered), grouped by meaning, as a
  content to-do list.

### 4.2 Existing-client support
- **Email verification with a one-time 6-digit code:** hashed, expires, 3 attempts, 3 codes
  an hour. It never reveals whether an email belongs to a client. The code must be typed
  by the visitor (the tool checks the human's own message, so the model can't "guess" it).
- Client tools (tickets, projects, bookings) are **hidden from the model until verified**,
  and filter by the verified contact's company; other clients' records are "not found".
- Raise tickets (local database or **Jira**), add updates, see project updates, reschedule or
  cancel calls.
- Urgent and high-priority tickets also create a **handover**.

### 4.3 Human handover
- Triggers: visitor asks for a person, pricing negotiation, complaint or legal matter, urgent
  technical issue, security incident (keyword rules with exclusions, editable).
- Routing rules decide who gets it; notifications go out by **Slack and email**.
- Honest **office-hours promises** ("a consultant will reply by 10am Monday"), UK holidays
  included.
- In the dashboard: **I'll take it** (ownership), **Mark resolved**, and managers can
  **reassign**. Consultants can only take the team queue or their own.

### 4.4 Internal knowledge assistant
- Questions and drafts (proposals, meeting summaries, follow-up emails) over **17
  confidential internal documents** (SOPs, client notes, runbooks) with front matter for
  client, sensitivity and roles.
- **Access filtering happens inside the SQL search, before the vector search**, by role and
  per-consultant client grants, so another client's text can never reach the prompt.
- Runs on **Ollama locally** (qwen2.5:3b by default, 7b available): internal documents never
  go to a cloud model.
- **Cites sources** for every answer; a runtime "fact guard" replaces figures not supported
  by a source with **[TO CONFIRM]**; a citation-accuracy checker is used in evals.
- **Live records:** when a question names a person, company or reference (e.g. T-1003), it
  looks up the matching handovers, bookings, tickets (and leads for managers) in SQL under
  the dashboard's access rules. Transcripts are never given to the model.
- Can email or post to Slack **only when the person explicitly asks** in their own message,
  and email only to the firm's domain or clients the person can access.
- Private chat history per staff member; rate limit of 12 questions a minute.

### 4.5 Staff dashboard
Vite + React + TypeScript, Keycloak single sign-on (PKCE), monochrome design system.

| Page | Purpose |
|---|---|
| Overview | Conversations, qualified rate, booking conversion, escalation rate, calls this week, handover queue, unanswered topics, model token use |
| Handovers | Open / Taken / Resolved, take, resolve, reassign |
| Conversations | Every chat, searchable; opening a transcript is audited |
| Leads | Qualified by rules, searchable, synced to the CRM |
| Bookings | Upcoming and past discovery calls |
| Tickets | Client tickets, most urgent first (live Jira status when Jira is on) |
| Knowledge assistant | Chat with sources, private history |
| Website content | Edit/add public pages, check (blocks prices and promises), publish instantly, test a question, see what it couldn't answer |
| Settings | Greeting, fallbacks, qualification, availability, handover routing, office and messaging hours: forms with previews, versioned drafts, publish |
| Client access | Which consultant sees which client's documents |
| Privacy requests | UK GDPR lookup, export (JSON), erasure, retention periods |
| Audit log | Who looked at or changed what, filterable, read-only |

System prompts are deliberately **not** editable, so a stolen admin login can't rewrite the
safety rules.

---

## 5. Architecture

```
Website widget ─┐                           ┌─ Groq (gpt-oss-120b, gpt-oss-20b) → Gemini fallback
Client chat ────┼─► FastAPI API ─► Orchestrator (one brain) ─┤
Dashboard ──────┘        │              │                    └─ Ollama (local) for internal work
                         │              ├─ Tool registry (idempotent, savepoints, ToolRejected)
                         │              ├─ Retrieval: pgvector + fastembed (bge-small, 384-d)
                         │              └─ Guardrails, qualification rules, handover rules
                         ▼
                  Postgres 16 + pgvector  ◄──  Outbox worker (SKIP LOCKED, backoff, dead letters)
                                                   │
                                   Email (SMTP) · Slack · Jira · CRM · Calendar
```

Key ideas:
- **One orchestrator for all channels**, with channel-specific prompts and tool sets.
- **Tools never cause side effects directly.** A side-effecting tool returns a *plan*; the
  **outbox worker** performs it (email, Slack, Jira, CRM sync, calendar event) with retries,
  backoff, dead letters and alerts. This is what guarantees **no duplicate bookings or
  messages**, even when services are down or a visitor double-submits.
- **Actions come from the person, not the model:** side-effecting tools check the human's
  latest message (the code typed, an explicit "send", a recipient the user typed).
- **Every external integration sits behind an adapter with a fake for tests:** CRM
  (local/HubSpot), calendar (local/Google Calendar), ticketing (local/Jira).
- **Versioned no-code config** in the database (rules, greetings, content) with defaults in
  code; changes go live within seconds.
- Data model with CHECK-constrained string enums, Alembic migrations, async SQLAlchemy 2.

---

## 6. Technology (all free)

| Area | Choice |
|---|---|
| Backend | Python 3, FastAPI, async SQLAlchemy 2, Pydantic v2, Alembic |
| Database | PostgreSQL 16 + pgvector (also btree_gist for booking overlap constraints) |
| LLMs | Groq free tier (openai/gpt-oss-120b, gpt-oss-20b), Gemini free tier as fallback, Ollama locally (qwen2.5:3b / 7b); a Claude adapter exists but is disabled by design |
| Embeddings | fastembed, BAAI/bge-small-en-v1.5 (384 dimensions), on CPU |
| Auth | Keycloak (staff SSO, roles), signed visitor session tokens |
| Frontends | Preact chat widget (single-file build); React + TypeScript + Vite dashboard |
| Email | SMTP (Mailpit in development; Gmail app password or Brevo free in production) |
| Integrations | Slack webhooks, Jira Cloud (both connected for real in development), HubSpot and Google Calendar (built, opt-in) |
| Containers | Podman + podman-compose |
| Observability | Structured JSON logs, Prometheus metrics, optional Grafana |
| Hosting | The developer's PC, Caddy as front door, Tailscale Funnel for public HTTPS |
| Testing | pytest, a Python eval runner against the real models, Playwright for the demo walkthrough |

---

## 7. Safety, security and compliance

**AI safety measures**
- Public answers only from approved content; honest "I don't know".
- Deterministic guardrail on every reply (prices, rates, guarantees, promises).
- Client data separation enforced in SQL; verification required before client data.
- Prompt-injection resistance tested on every channel (red-team evals).
- Side effects only through the outbox, triggered by the human's own words.

**Security hardening (Phase 7):** review of all 37 API endpoints; 11 findings fixed,
including:
- tests that posted to the real Slack;
- system prompts rewritable through the API;
- the knowledge assistant able to email documents to any address;
- a rate-limit bypass with a fake X-Forwarded-For header;
- API docs and metrics open to anyone;
- missing security headers and request size limits;
- SQL wildcards in search;
- an open-redirect risk;
- dependency advisories.

Also:
- an access-control matrix test calls every staff route as every role;
- secrets were scanned across the whole git history (none found);
- `pip-audit` shows no known Python vulnerabilities.

**Red-team cases (all pass):** role-play jailbreaks, fake management policy, "system
override" code exfiltration, instructions hidden in pasted text, skipping verification,
asking for another client's data, false role claims, emailing a runbook to a personal
address.

**UK GDPR**
- Consent before chat.
- Right of access (JSON export).
- Right to erasure: anonymise, cancel future calls, stop queued emails, do-not-contact list.
- Automatic retention:

  | Data | Kept for |
  |---|---|
  | Visitor chats | 1 year |
  | Staff chats | 180 days |
  | Code records | 7 days |
  | Sent-message records | 90 days |
  | Audit log | 2 years |

- Privacy actions are audited by an email hash, never the address.

**ISO 27001:** a map of 25 Annex A controls to the code and tests that evidence them, plus
the controls that remain the firm's responsibility.

**Audit log:** transcript views, lead list views, knowledge lookups, handovers, settings,
access grants and privacy actions, with the staff member's name.

---

## 8. Testing and quality

- **256 automated tests** (unit and database), including:
  - security tests (16) and privacy tests (7);
  - failure drills (7): both LLMs down, Ollama down, Jira down then back, Slack unreachable,
    database down, a double "book it", a worker crash mid-message;
  - reliability tests: retries, dead letters, two workers, concurrent bookings.
- Tests **never touch real services**: all integrations are forced offline in test config.
- **Evals against the real models:** 24 public-assistant cases (answers, refusals,
  booking, verification, client isolation, injection) and 10 internal cases (citations,
  access, drafting), on both model chains.
- **Load test:** a single API process handled ~96 requests a second with zero errors at
  500 visitors x 3 messages + 50 staff (p50 815 ms, p95 2.5 s, stand-in model). The real
  limit is the free model tier (Groq: 8,000 tokens a minute per model), not the code.
- Ruff lint and formatting; CI workflow.

---

## 9. Deployment

Goal: public, free, and **no card at signup**. Cloud options were evaluated and rejected:
- **Oracle, Google Cloud, AWS, Azure:** need a card.
- **Render, Vercel, Koyeb, Fly.io, Railway, Hugging Face Spaces, SnapDeploy, Back4App:**
  these sleep, have 256–512 MB of memory, only offer trials, or can't run the always-on
  worker, Keycloak and a local model.

**Chosen:** the full stack runs on the developer's PC as a **separate production stack**
beside the development one, published with **Tailscale Funnel** (free, automatic HTTPS).
- Caddy is the only public entry point. It routes the website and widget, the API and
  sign-in on one address, and the dashboard on port 8443. It blocks Keycloak's admin
  console, its master realm and `/metrics`.
- Every other service listens on 127.0.0.1 only. Keycloak runs in production mode, with
  brute-force protection and browser sign-in only.
- Production reads only its own settings file, so development's real Slack and Jira
  credentials can't leak into the public demo. Integrations are off in production.
- One script manages it:
  - `init` (generates secrets), `build`, `up`, `load-data`, `status` and `down`;
  - `backup` (both databases, keeps 14) and `restore-test` (a restore drill comparing row
    counts);
  - `funnel-on` and `funnel-off`.
- Verified from outside: website chat with real model replies, staff login, knowledge
  assistant, blocked paths, backup and restore drill.
- Trade-off: the demo is online only while the PC is on and awake.

At real scale the plan would change:
- more than one server, and a CDN for the frontends;
- managed off-site backups;
- a paid or self-hosted model with more capacity;
- rate limits shared across servers (Redis or Postgres).

---

## 10. Delivery by phase

| Phase | Delivered |
|---|---|
| 0 Discovery | Requirements, user stories, stack decisions (free-stack rule), scope changes |
| 1 Foundation | API, database, Keycloak, dev stack scripts, one orchestrator, LLM chain, outbox worker |
| 2 Website chat MVP | Widget, public knowledge base, guardrails, qualification rules, booking, CRM/calendar adapters, evals |
| 3 Client support + handover | Email verification, tickets (local/Jira), projects, reschedule/cancel, reminders, handover engine |
| 4 Voice | Removed from scope (no calling features; Telegram also removed) |
| 5 Internal knowledge | Access-filtered internal search, local model, citations, fact guard, drafting, client access grants |
| 6 Staff dashboard | All dashboard pages, SSO, analytics, content and rule editors, handover ownership, live records |
| 7 Security + compliance | Security review and fixes, red-team evals, GDPR tools, audit log viewer, ISO 27001 map, drills, load test |
| 8 Launch | Public deployment (PC + Tailscale Funnel), runbook, staff and admin guides, automated demo-video walkthrough |

---

## 11. Documentation delivered

- Phase decision docs for each phase (`docs/phase-N/`).
- **Runbook:** start, stop, restart after a reboot, changing settings, backups and restore,
  logs, a symptom/fix table, security notes.
- **Staff guide:** roles, handovers, bookings, tickets, the knowledge assistant.
- **Admin guide:** staff accounts and roles in Keycloak, client access, website content,
  settings, GDPR requests, audit log, an admin checklist.
- **Demo video guide:** an automated Playwright walkthrough (captions on screen) plus
  narration for each step.

---

## 12. Known limitations and next steps

- The public demo depends on one PC being on; no redundancy.
- Groq's free tier limits throughput (a few chats a minute) and daily tokens.
- The local model is slow on CPU (about 30–60 seconds an answer).
- Email in the demo goes to a test inbox until a free SMTP sender is configured.
- HubSpot and Google Calendar adapters are built and unit-tested but not connected to real
  accounts.
- Not yet done:
  - two failure drills (CRM down, calendar timeout);
  - major-version upgrades of the dev tooling (Vite 6+, React Router 7);
  - an external penetration test.
- Bookable consultants are set in seed data rather than through the dashboard.

---

## 13. Key numbers

| | |
|---|---|
| Automated tests | 256 passing |
| Real-model evals | 24 public + 10 internal cases, all passing; red-team cases on every channel |
| API endpoints reviewed | 37; 11 security findings fixed |
| Internal documents | 17 (42 indexed chunks) |
| ISO 27001 controls mapped | 25 |
| Load test | ~96 requests/second per process, 0 errors |
| Running cost | £0 (free tiers, open source, own hardware) |
