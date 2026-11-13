# Phase 0 · Risk register

Scores are likelihood (L) and impact (I), from 1 to 5.

| # | Risk | L | I | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Free LLM tiers (Groq/Gemini) change limits or terms | 3 | 4 | Provider-agnostic layer; Ollama local fallback; evals run on every adapter | Dev |
| R2 | Local CPU model slow for the internal assistant | 4 | 2 | Streaming replies; small models (3B) for classifiers; Groq for non-confidential tasks | Dev |
| R3 | The brief asks for Claude, but Claude isn't free | 4 | 2 | Claude adapter built but disabled; documented as a config switch | User |
| R4 | Hallucinated pricing or promises | 3 | 5 | Layered guardrails, never-say post-check, adversarial eval set in CI that fails the build | Dev |
| R5 | Cross-client data leakage in RAG | 2 | 5 | ACL filter before search, separate public/internal tables, leakage tests in CI | Dev |
| R6 | Prompt injection through user input or documents | 3 | 4 | Tool allowlists per channel, no secrets in prompts, retrieved text treated as data, injection eval set | Dev |
| R7 | Duplicate bookings or messages on retries | 3 | 4 | Idempotency keys, outbox, DB exclusion constraint, chaos tests | Dev |
| R8 | HubSpot / Jira free-plan API limits or feature changes | 2 | 3 | Adapters with fakes; Twenty / Zammad self-hosted fallbacks | Dev |
| R9 | Free hosting (Oracle) capacity or account reclaim | 3 | 2 | Everything reproducible via compose; Cloudflare Tunnel from local as a fallback | Dev |
| R10 | Docker Desktop licence terms | 2 | 2 | Use Podman Desktop | Dev |
| R11 | GDPR: transcripts with personal data sent to a cloud LLM (Groq) | 3 | 4 | Consent notice, PII redaction before cloud LLM calls where feasible, local model for confidential data, DPAs listed | Dev |
| R12 | Scope creep across the deliverables | 3 | 3 | Each phase has an exit test; nothing starts until the earlier exit passes. Voice already removed. | User |
