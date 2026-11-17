# Phase 0 · Stack decisions (free stack)

Rule: every component is open source, self-hosted, or on a free-forever tier. Anything that would cost money is out of scope.

> **Scope change (2026-11-13):** the **voice/call system is removed** from this project: no phone agent, no Twilio, no browser calling, no speech-to-text or text-to-speech. Booking confirmations and reminders go out by email instead.

> **Scope change (2026-11-16):** **Telegram is removed.** Clients get confirmations and reminders by email; consultants get booking confirmations and reminders in Slack.

Dev machine: i5-8500 (6 cores), 32 GB RAM, no NVIDIA GPU, Docker not yet installed.

## Summary

| Area | Choice | Why | Fallback |
|---|---|---|---|
| Containers | **Podman** (CLI; data on D:) | Podman is fully free with no licence conditions. Docker Desktop is free only for personal use or companies under 250 staff and $10M revenue. | Docker Engine in WSL2 |
| Backend | **Python 3.12 + FastAPI** | Strong AI/RAG ecosystem, async, typed | — |
| Database | **PostgreSQL 16 + pgvector** | One database for app data, the outbox and vector search | — |
| Queue / jobs | **Postgres outbox + worker** (`SELECT … FOR UPDATE SKIP LOCKED`) | Gives idempotency and retries without adding Redis or RabbitMQ | Redis + RQ |
| LLM | **Provider-agnostic layer** (see below) | Keeps everything free with no lock-in | — |
| Embeddings | **fastembed `bge-small-en-v1.5`** (local ONNX, CPU) | Free, no GPU or Ollama install, and documents never leave the machine. *(Changed in Phase 2 from Ollama `nomic-embed-text`, which would install onto the full C: drive.)* | Ollama `nomic-embed-text` |
| RAG framework | Plain code + pgvector (no LangChain) | Access filtering stays explicit and auditable | LlamaIndex |
| CRM | **HubSpot Free CRM** | Free forever, with a full REST API through a private app (contacts, companies, deals, notes) | Twenty CRM (self-hosted, open source) |
| Ticketing | **Jira Service Management Free** (3 agents) | Free cloud plan, REST API, customer-request model | Zammad (self-hosted, open source) |
| Calendar | **Google Calendar API** (personal Google account) | Free API with free/busy lookup and event creation | Cal.com self-hosted |
| Email (send) | **Brevo free** (300 emails a day) for real sends, **Mailpit** locally | Both free; Mailpit catches every dev email so nothing reaches real inboxes | Gmail SMTP with an app password |
| Team chat | **Slack Free** (bot + Block Kit) | Bot tokens and webhooks are free; the 90-day history limit doesn't matter here | Discord webhooks |
| Messaging | **Email** to clients, **Slack** to the team; optional **WhatsApp Cloud API test number** | Free. The WhatsApp test number is free for up to 5 verified recipients. *(Telegram removed 2026-11-16.)* | — |
| Auth / SSO / roles | **Keycloak** (self-hosted OIDC) | Real SSO and role-based access, free | Authentik |
| Web chat widget | **Preact + Vite** (one small embeddable JS file) | Small bundle that drops into any site | — |
| Admin dashboard + internal assistant UI | **Next.js + shadcn/ui** | Free, quick to build | React + Vite |
| LLM tracing / evals | **Langfuse** (self-hosted) + **Python eval runner** (`apps/api/evals`) | Langfuse is open source. *(Changed in Phase 2: a small Python runner replaced promptfoo; it needs no extra dependencies and runs the real orchestrator.)* | promptfoo |
| Logs, metrics, alerts | **Grafana + Loki + Prometheus**, alerts sent to Slack | All open source | GlitchTip for errors |
| Secrets | **SOPS + age** (encrypted in the repo), `.env` locally | Free and needs no server | Infisical self-hosted |
| CI/CD | **GitHub Actions** (free for public repos, 2,000 minutes a month for private ones) | Free | — |
| Tests | pytest, Playwright, promptfoo | Free | — |
| Public demo hosting | **Oracle Cloud Always Free** (4 ARM cores, 24 GB RAM) + **Cloudflare Tunnel** | Free forever | Run locally and expose it with Cloudflare Tunnel |

## LLM

The original brief says "powered by Claude", but the Claude API is paid, with no permanent free tier. (Claude Code, the development tool, is already covered by your subscription.)

> **✅ Decided (2026-11-13):** default to the free models. **Groq** handles web chat and messaging. **Ollama** (local) handles the internal knowledge assistant and offline dev. **Gemini** is the free fallback. The Claude adapter is built but stays **off** (paid).

One `LLMProvider` interface, with the adapter selected by config:

| Adapter | Cost | Use |
|---|---|---|
| `groq` (`openai/gpt-oss-120b`) + `groq_alt` (`openai/gpt-oss-20b`) | Free tier, 8k tokens/min per model | **Default.** *(Llama 3.3 was retired by Groq; changed 2026-11-16.)* Web chat and messaging; fast, with tool calling |
| `ollama` (Qwen2.5 7B / Llama 3.1 8B, CPU) | Free, fully local | Internal knowledge assistant (confidential data stays on the machine) and offline dev. About 5–8 tokens a second on this CPU. |
| `gemini` (Gemini Flash) | Free tier | Fallback |
| `anthropic` (Claude) | Paid | Built but **disabled** |

Prompts, tools and evals are written once and run against every adapter. promptfoo compares them side by side.
