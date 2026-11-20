# Runbook · public demo

How to run, look after and fix the production stack. It runs on the dev PC beside the dev
stack and is published by Tailscale Funnel. Background: [01-deployment-plan.md](01-deployment-plan.md).

All commands run in PowerShell from the repository root.

| | |
|---|---|
| Website demo + chat | `https://desktop-q5clvqu.tail751d64.ts.net` |
| Staff dashboard | `https://desktop-q5clvqu.tail751d64.ts.net:8443` |
| Settings | `.env.prod` (git-ignored; the only file production reads) |
| Keycloak admin console | `http://127.0.0.1:8180/auth/admin` (this PC only), user `admin`, password `KC_ADMIN_PASSWORD` |
| Backups | `D:\Backups\assistant\<date-time>\` |

## What runs where

| Part | How it runs | Port (127.0.0.1 only) |
|---|---|---|
| Caddy (front door) | `D:\Caddy\caddy.exe`, started by `prod.ps1` | 8081 site/API/login, 8082 dashboard |
| API | Python (uvicorn), started by `prod.ps1` | 8001 |
| Outbox worker | Python, started by `prod.ps1` | metrics 9102 |
| Keycloak | container `assistant-prod_keycloak_1` | 8180 |
| Postgres | container `assistant-prod_postgres_1`, volume `pgdata-prod` | 5434 |
| Ollama | `D:\Ollama\ollama.exe serve` (shared with dev) | 11434 |
| Tailscale Funnel | Windows service | public 443 to 8081, 8443 to 8082 |

The dev stack uses different ports (5433, 8080, 8000) and its own data, so both can run at
once. The dev worker posts to the **real** Slack and Jira; production's integrations are off.

## Everyday commands

```powershell
.\scripts\prod.ps1 status        # containers, API, worker, Caddy, readiness
.\scripts\prod.ps1 up            # start everything (safe to run when already running)
.\scripts\prod.ps1 down          # stop everything; data is kept
.\scripts\prod.ps1 funnel-off    # take it off the internet (stack keeps running locally)
.\scripts\prod.ps1 funnel-on     # put it back on the internet
```

`status` should show both containers `Up`, `api`, `worker` and `caddy` running, and
`readyz: 200`. `tailscale funnel status` shows what is public.

## Start, stop, restart

**Start:** `.\scripts\prod.ps1 up`. It starts the Podman VM, Postgres and Keycloak, waits
for Keycloak (first start up to 2 minutes), sets the dashboard address and demo passwords in
Keycloak, applies database migrations, starts Ollama, the API, the worker and Caddy, and
waits for `/readyz`. It prints `Up: <address>` when ready.

**Stop:** `.\scripts\prod.ps1 down`. Visitors then get an error page from Tailscale. To show
nothing at all, run `funnel-off` first.

**Restart one part:** `up` only starts what isn't running. To restart the API after a
settings change, run `down` then `up` (about a minute).

**After a reboot or wake from sleep:** the Funnel setting is kept, the stack is not.
1. Sign in to Windows. Tailscale starts by itself (check the tray icon is connected).
2. `.\scripts\prod.ps1 up`
3. `.\scripts\prod.ps1 status`, then open the website address on a phone with mobile data.

While demoing, set Windows' **sleep** to *Never* (Settings, System, Power). A sleeping PC
takes the demo offline.

## Changing things

| Change | Steps |
|---|---|
| A setting in `.env.prod` (model, email, limits) | edit it, then `down`, `up` |
| The public address (new PC name, different tailnet) | edit `PUBLIC_URL` and `DASHBOARD_URL`, then `build`, `down`, `up` |
| A demo staff password | edit `DEMO_*_PASSWORD`, then `.\scripts\prod.ps1 sync-keycloak` |
| New code from git | `git pull`, `build`, `down`, `up` (migrations run in `up`) |
| Public website pages | in the dashboard (Website content), no restart |
| Assistant rules, greeting, office hours | in the dashboard (Settings), live within 10 seconds |

Don't change `POSTGRES_PASSWORD` or `KC_ADMIN_PASSWORD` in `.env.prod` on their own: the
database and Keycloak keep the old ones. Changing `APP_SECRET` signs out every website
visitor (their chats are kept).

Real email: set `SMTP_HOST`, `SMTP_PORT=587`, `SMTP_STARTTLS=true`, `SMTP_USERNAME`,
`SMTP_PASSWORD` and `SMTP_FROM` for Gmail (an app password) or Brevo free, then `down`, `up`.
Until then every email goes to Mailpit, which only runs with the dev stack
(`http://localhost:8025`).

## Backups and restore

**Back up:** `.\scripts\prod.ps1 backup`. It dumps both databases (`assistant` and
`keycloak`) to `D:\Backups\assistant\<date-time>\` and keeps the newest 14. Run it before
any upgrade and regularly while the demo is live. The backups are on the same PC: copy the
folder to a USB drive or cloud storage now and then.

**Restore drill (safe, any time):** `.\scripts\prod.ps1 restore-test`. It restores the
newest backup into a scratch database, prints row counts next to the live ones, and drops
the scratch copy. Live data isn't touched. First drill: 2026-11-20, all counts matched.

**Real restore (replaces live data):** only when the data is lost or damaged.

```powershell
$env:XDG_DATA_HOME = 'D:\PodmanData'
$b = 'D:\Backups\assistant\20261120-104607'          # the backup to restore
.\scripts\prod.ps1 down
.venv\Scripts\podman-compose.exe --env-file .env.prod -f infra\prod\compose.yaml up -d postgres
Start-Sleep 10                                       # let Postgres start
podman cp "$b\assistant.dump" assistant-prod_postgres_1:/tmp/a.dump
podman cp "$b\keycloak.dump" assistant-prod_postgres_1:/tmp/k.dump
podman exec assistant-prod_postgres_1 pg_restore -U assistant -d assistant --clean --if-exists --no-owner /tmp/a.dump
podman exec assistant-prod_postgres_1 pg_restore -U assistant -d keycloak --clean --if-exists --no-owner /tmp/k.dump
.\scripts\prod.ps1 up
```

Anything since that backup is lost: chats, bookings, leads, handovers, and staff password
changes made in Keycloak.

## Logs

| What | Where |
|---|---|
| API | `logs\prod-api.err.log` (JSON lines; errors and startup) |
| Worker (emails, retries, retention) | `logs\prod-worker.err.log` |
| Caddy (proxy errors, 502s) | `logs\prod-caddy.err.log` |
| Keycloak | `podman logs --tail 100 assistant-prod_keycloak_1` |
| Postgres | `podman logs --tail 100 assistant-prod_postgres_1` |
| Who did what in the dashboard | dashboard, **Audit log** (admins) |

`Get-Content logs\prod-api.err.log -Tail 50 -Wait` follows a log live. Podman commands need
`$env:XDG_DATA_HOME = 'D:\PodmanData'` first (the scripts set it themselves).

To see emails, Slack posts and tickets waiting or failed:

```powershell
podman exec assistant-prod_postgres_1 psql -U assistant -d assistant -c "SELECT kind, status, attempts, last_error FROM outbox WHERE status <> 'done' ORDER BY id DESC LIMIT 20"
```

## When something breaks

| Symptom | Likely cause | Fix |
|---|---|---|
| Public address doesn't load at all | PC asleep or off, or Tailscale disconnected | wake the PC; check the tray icon; `tailscale funnel status` |
| "502 Bad Gateway" | API not running | `status`; read `logs\prod-api.err.log`; `up` |
| Tailscale error page, local `status` fine | Funnel off | `.\scripts\prod.ps1 funnel-on` |
| `up` stops at "Waiting for Keycloak" | Keycloak slow or failing | `podman logs assistant-prod_keycloak_1`; wait and run `up` again |
| Dashboard sign-in: "Invalid parameter: redirect_uri" | Keycloak has an old dashboard address | `.\scripts\prod.ps1 sync-keycloak` |
| Dashboard says it can't reach the API | `build` made for another address | `build`, then reload the page |
| Signed in, but every page errors (401) | `PUBLIC_URL` changed without `build`, `down`, `up` | do all three |
| Chat replies "having trouble right now" | Groq limit reached or key wrong; Gemini fallback missing | `logs\prod-api.err.log`; Groq's free tier is 200k tokens a day per model; add `GEMINI_API_KEY` |
| Knowledge assistant says it's degraded | Ollama not running | `up` (starts it); or `D:\Ollama\ollama.exe serve` |
| Knowledge assistant very slow | normal on CPU: 30 to 60 seconds an answer | nothing; drafts take longer |
| Emails never arrive | SMTP not set (Mailpit), or failing | the outbox query above; `logs\prod-worker.err.log` |
| `podman` errors about the machine or connection | VM stopped, or `XDG_DATA_HOME` unset | the scripts set it; `podman machine start` with it set |
| Anything fails with "no space" | C: drive full (it's nearly full) | free space on C:; Podman, Ollama and backups are on D: |
| Visitors hit "too many requests" | rate limit (20 a minute per visitor) | expected; raise `RATE_LIMIT_PER_MINUTE` if needed |

**If something looks like abuse:** `funnel-off` takes it off the internet in a second.
Then check the audit log and `logs\prod-api.err.log`.

## Security notes

- Only Caddy is public. Postgres, Keycloak's admin console, the master realm, `/metrics`
  and the API docs aren't reachable from outside (checked 2026-11-20).
- Keep `.env.prod` and `D:\Backups` private: the backups contain every chat.
- Share demo staff passwords only with people you show the dashboard to.
- Slack, Jira, HubSpot and Google Calendar are off. If you ever turn one on, point it at a
  demo workspace, never the real one.
