# ISO 27001:2022 Annex A: how the assistant maps to the controls

This maps the **technical** controls that apply to this system. It isn't a certification: an
ISMS also needs organisational controls (risk owner, policies, staff training, supplier
reviews), which belong to the firm. "Evidence" points at the code or test that shows each one.

| Control | What it asks | How the assistant meets it | Evidence |
|---|---|---|---|
| **5.15 Access control** | Access by business need | Three staff roles (admin, manager, consultant) checked by the API on every request; visitors never see staff data | `auth.py` `require_roles`; `test_db_security.py::test_access_matrix_every_route_every_role` walks every route x every role |
| **5.16 Identity management** | Unique identities | Staff sign in through Keycloak (one account each); visitors get a signed, expiring session token per conversation | `auth.py`; `test_visitor_tokens_cannot_be_forged_or_reused_elsewhere` |
| **5.17 Authentication information** | Protect secrets | Keys live only in `.env` (git-ignored); `APP_SECRET` must be 32+ random characters outside dev or the app refuses to start; git history scanned for keys: none | `settings.py` validator; Phase 7 secret scan |
| **5.18 Access rights** | Grant, review, remove | Client access per consultant on the Client access page, granted and removed by admins, audited | `/v1/dashboard/client-access`; audit actions `client_access.*` |
| **5.34 Privacy and PII** | Protect personal data | UK GDPR: consent recorded at chat start, export, erasure, retention; verification before any client data | `privacy.py`, Privacy requests page; `test_db_privacy.py` |
| **8.2 Privileged access** | Restrict admin rights | Only admins publish settings, edit content, manage access, handle privacy requests and read the audit log; the system prompts can't be changed through the API at all | `test_system_prompts_cannot_be_rewritten_through_the_api` |
| **8.3 Information access restriction** | Restrict by need | Client data separation: verified clients see only their company; consultants see only assigned clients' documents and records, enforced inside the database query | `kb/internal.py`, `kb/records.py`; Phase 5 leak tests, `test_consultants_never_get_records_of_clients_they_are_not_assigned` |
| **8.5 Secure authentication** | Strong auth, limits | Keycloak PKCE login, token audience and issuer checked; visitor 6-digit codes: hashed, 10-minute expiry, 3 attempts, 3 codes an hour | `identity.py`; Phase 3 tests |
| **8.7 Protection against malware / abuse** | Guard inputs | Prompt-injection defences: rules in the system prompt, a deterministic reply check, tools hidden until verified, actions only from the person's own words; red-team evals on every channel | `guardrails.py`, `tools/builtin.py`; `evals/cases.py` `rt-*` |
| **8.8 Technical vulnerabilities** | Find and fix | `pip-audit` and `npm audit` on every dependency set; fixes applied; remaining dev-tool advisories recorded with reasons | Phase 7 README "Dependencies" |
| **8.9 Configuration management** | Controlled config | Every setting is versioned (draft, publish, history), validated by schema before it can go live, and audited | `config_store.py`, `rules.py` |
| **8.10 Information deletion** | Delete when no longer needed | Retention periods applied every 6 hours by the worker; erasure on request | `privacy.apply_retention`; `test_retention_expires_old_data_but_keeps_what_is_still_open` |
| **8.11 Data masking** | Limit exposure | Audit entries store a hash of a subject's email, not the address; verification codes are stored as HMACs; transcripts never reach the knowledge assistant | `privacy.email_hash`, `identity.py`, `kb/records.py` |
| **8.12 Data leakage prevention** | Stop data leaving | Internal documents stay on the local model; the knowledge assistant may only email the firm's domain or a client the person can access | `INTERNAL_LLM_CHAIN=ollama`; `test_internal_email_only_to_the_firm_or_accessible_clients` |
| **8.15 Logging** | Record activity | Audit log of staff data access and changes (transcripts opened, leads viewed, settings published, access granted, privacy requests), viewable by admins, kept 2 years | `audit.py`, Audit log page |
| **8.16 Monitoring** | Detect problems | Prometheus metrics (requests, latency, outbox backlog, dead letters, guardrail blocks, injection flags); alerts to Slack on critical failures | `metrics.py`, `alerts.py`; `/metrics` closed outside dev |
| **8.20 Network security** | Protect services | CORS limited to the site and dashboard origins; API docs off outside dev; HTTPS-only headers (HSTS) outside dev | `api/main.py`, `api/security.py`; `test_cors_only_for_configured_origins` |
| **8.22 Segregation of networks** | Separate environments | Dev stack is local; test suite forced offline (no real Slack, Jira, CRM or email) | `tests/conftest.py` `_no_real_integrations` |
| **8.23 Web filtering / abuse limits** | Resist abuse | Per-IP rate limit on the public chat (spoof-proof by default), per-person limit on the knowledge assistant, 256 KB request cap | `ratelimit.py`, `BodyLimit`; security tests |
| **8.24 Cryptography** | Use it properly | Signed JWTs (RS256 for staff via Keycloak, HS256 visitor tokens with a 32+ character secret), HMAC for codes, TLS expected in production | `auth.py`, `identity.py` |
| **8.25 Secure development** | Build securely | CI runs lint, tests and builds on every push; every feature has tests; security tests cover each new route automatically | `.github/workflows/ci.yml`; `test_every_staff_route_declares_roles` |
| **8.26 Application security requirements** | Know what to protect | Hard rules in `CLAUDE.md` (no side effects from tools, verification first, client separation, actions from the person) | `CLAUDE.md` |
| **8.28 Secure coding** | Avoid common flaws | Parameterised SQL everywhere (search input with `%`, `_`, quotes and `DROP TABLE` tested); output escaping in the dashboard (React); no `eval` | `test_search_inputs_are_parameters_not_sql` |
| **8.29 Security testing** | Test before release | Phase 7 security suite, red-team evals, failure drills | this phase |
| **5.30 / 8.14 Continuity and redundancy** | Keep working | Every outside service can fail without losing work: outbox retries with backoff and dead-letters with alerts; model fallback chain; readiness probe for load balancers | `test_db_drills.py`, `test_db_reliability.py` |

## Not covered by the software (the firm's part)

- **5.1–5.4 Policies, roles, responsibilities:** information security policy, a named owner.
- **5.19–5.22 Suppliers:** data processing agreements with Groq, Google (Gemini), Slack,
  Atlassian and HubSpot (if enabled). Groq and Gemini see public-chat text only; internal
  documents never leave the machine.
- **6.3 Awareness and training:** staff guidance on the assistant (Phase 8 guides and video).
- **7.x Physical:** the machine running Ollama and the database.
- **8.13 Backup:** Postgres backups (`pg_dump` on a schedule) are an operations task for
  launch (Phase 8 runbook).
