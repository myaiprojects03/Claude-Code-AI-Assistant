# Phase 8 · Deployment plan

Free stack, no card at signup. The whole project (frontends and backend) runs on the dev PC
as a separate production stack, made public by **Tailscale Funnel** (free personal plan,
sign in with GitHub, Google or Microsoft). No cloud host was free, card-free *and* big enough
(about 5 GB, always on, plus the local model); the options checked are listed at the end.

The catch: the demo is online only while the PC is on and awake.

## Where each part runs

| Part | Runs as | Listens on | Public address |
|---|---|---|---|
| Website demo + chat widget | static files, Caddy | 127.0.0.1:8081 | `https://<pc>.<tailnet>.ts.net/` |
| API (`/v1`, `/healthz`, `/readyz`) | uvicorn, native | 127.0.0.1:8001 | same host, via Caddy |
| Staff login (Keycloak, production mode) | container | 127.0.0.1:8180 | same host, `/auth` (admin console and master realm blocked) |
| Staff dashboard | static files, Caddy | 127.0.0.1:8082 | `https://<pc>.<tailnet>.ts.net:8443/` |
| Postgres + pgvector | container, own volume | 127.0.0.1:5434 | never |
| Outbox worker, Ollama | native | local only | never |

Funnel ends TLS and forwards 443 to Caddy's 8081 and 8443 to 8082. Everything else binds to
127.0.0.1, so nothing on the PC is reachable from the internet or the home network except
what Caddy serves. Caddy runs natively (`D:\Caddy\caddy.exe`, v2.11.9, checksum verified)
because containers here can't reach programs on the Windows host.

Production is separate from dev: its own database (port 5434, volume `pgdata-prod`), its own
Keycloak, its own settings file `.env.prod`. The API and worker read **only** `.env.prod`
(`ASSISTANT_ENV_FILE`), so dev's real Slack and Jira settings can't leak into the public demo.

## Files

| File | What |
|---|---|
| `infra/prod/compose.yaml` | Postgres + Keycloak (production mode, `/auth`, proxy headers) |
| `infra/prod/Caddyfile` | front door: site, API, login, dashboard; security headers; blocked paths |
| `infra/prod/keycloak/realm-assistant.json` | realm without passwords; brute-force protection; browser sign-in only |
| `infra/prod/postgres-init.sql` | Keycloak database, extensions |
| `.env.prod.example` | settings template (copy to `.env.prod`, git-ignored) |
| `scripts/prod.ps1` | init, build, up, load-data, status, down, backup, restore-test, funnel-on/off |

Code changes: `ASSISTANT_ENV_FILE`, `OIDC_JWKS_URL` (signing keys fetched on this PC, not over
the public URL), SMTP sign-in over STARTTLS (`SMTP_USERNAME`, `SMTP_PASSWORD`,
`SMTP_STARTTLS`) for a free real mail sender. Tests: `tests/test_phase8_unit.py`.

## Steps

1. `.\scripts\prod.ps1 init` creates `.env.prod` with new random secrets and demo passwords.
   Paste `GROQ_API_KEY` (and `GEMINI_API_KEY`) yourself.
2. Install Tailscale, sign in, turn on HTTPS certificates and Funnel in the admin console.
   Set `PUBLIC_URL=https://<pc>.<tailnet>.ts.net` and `DASHBOARD_URL=...:8443` in `.env.prod`.
3. `.\scripts\prod.ps1 build` builds the dashboard and the website demo for those addresses.
4. `.\scripts\prod.ps1 up`, then (first time) `.\scripts\prod.ps1 load-data`.
5. `.\scripts\prod.ps1 funnel-on` makes it public; `funnel-off` takes it down.
6. Backups: `.\scripts\prod.ps1 backup` (both databases to `D:\Backups\assistant`, keeps
   14), and `restore-test` restores the newest into a scratch database and compares counts.

Changing an address later only needs `build` and `up` again: `up` re-applies the dashboard
address and demo passwords to Keycloak every time.

## Local dry run (done 2026-11-20)

With `PUBLIC_URL=http://localhost:8081` and `DASHBOARD_URL=http://localhost:8082`:

| Check | Result |
|---|---|
| Website demo loads, widget starts a chat, real Groq reply | Pass |
| Dashboard sign-in through production Keycloak, API accepts the token | Pass |
| Dashboard routes (`/leads`) served by the single-page fallback | Pass |
| `/auth/admin/...`, `/auth/realms/master/...`, `/metrics`, `/docs` from outside | 404 |
| Every port bound to 127.0.0.1 | Pass |
| Backup, then restore drill: restored counts equal live counts | Pass |

Found and fixed during the run: the demo page was read with the wrong text encoding (icons
garbled), and Caddy ran the `/auth` proxy before the admin-console block (a `handle` runs
before a bare `respond`), so the block is now a `handle` of its own.

## Live (2026-11-20)

Public through Funnel at `https://desktop-q5clvqu.tail751d64.ts.net` (website demo) and
`:8443` (dashboard). Checked from outside the tailnet: homepage and `/readyz` return 200;
`/auth/admin`, `/auth/realms/master` and `/metrics` return 404.

The Funnel setting survives a restart of the PC, but the stack doesn't start by itself: after
a reboot run `.\scripts\prod.ps1 up`. Until then visitors get an error page.

## Public demo safety

- Slack, Jira, HubSpot and Google Calendar are off in `.env.prod`. Point them only at demo
  workspaces if ever turned on.
- Email stays in Mailpit until a free sender is set (Gmail app password or Brevo free).
- Demo staff accounts have new random passwords; share them only with people you show it to.
- Rate limits apply per visitor (Caddy passes the visitor IP from Funnel).

## Hosting options checked (none fits without a card)

Oracle Cloud Always Free, Google Cloud, AWS and Azure need a card. Render (sleeps, 512 MB,
free Postgres deleted after 30 days), Vercel (no long-running programs), Koyeb (now a card),
Fly.io (trial only), Railway (trial credit), Hugging Face Spaces (Docker needs a paid plan),
SnapDeploy and Back4App (256–512 MB, sleep or hour caps) can't run Keycloak, the worker and
Ollama. Cloudflare Pages could host the static frontends, but the backend would still be here.

## At real scale (not this demo)

More than one server, a CDN for the frontends, managed database backups off-site, a paid or
self-hosted model with more capacity than Groq's free tier, and rate limits in Redis.
