# Phase 3 · Existing-client support and human handover

The assistant recognises existing clients, handles routine requests (ticket status, new
tickets, project updates, rescheduling), and hands over to the right person with a full
summary when it should step aside. Everything runs free; Jira and Slack are opt-in.

## What's built

| Piece | Where | Notes |
|---|---|---|
| Identity check | `identity.py`, tools `send_verification_code`, `verify_code` | Email + 6-digit code. Only a hash is stored; 10-minute expiry, 3 attempts, 3 codes per hour. The email and code must come from the visitor's own messages, not the model. |
| No client enumeration | `identity.py` | Same reply whether or not an email is on record; a code is only sent to known addresses |
| Verification gate | `tools/registry.py` | Client tools are **hidden from the model and refused** until `verify_code` succeeds |
| Tickets | `tools/clients.py`, `integrations/ticketing.py` | List, view, create, add updates. `local` (default) or Jira Service Management Free |
| Client isolation | `tools/clients.py` | Every lookup filtered by the verified contact's company; another client's ticket number looks exactly like one that doesn't exist |
| Project updates | `Project` model, tool `get_project_updates` | Client-safe status, latest update and next milestone |
| Bookings | tools `list_my_bookings`, `reschedule_booking`, `cancel_booking` | Only the verified person's own upcoming calls |
| Confirmations and reminders | `reminders.py` | Client: email at booking and 24h before (within messaging hours, their timezone). Consultant: Slack at booking and 1h before |
| Handover engine | `handover.py`, tool `request_handover` | Rules route by reason; enterprise accounts go to their account manager; reference numbers like H-501 |
| Security fast path | `orchestrator.py` | Keywords (ransomware, breach, compromised…) page security on-call **before the model runs**, with exclusions like "breach of contract" |
| Staff API | `GET /v1/handovers`, `POST /v1/handovers/{id}/acknowledge` | Any staff role; acknowledgements are audited |

## Who gets what

| Event | Client / visitor | Team |
|---|---|---|
| Discovery call booked | Email confirmation | Slack confirmation |
| 24h / 1h before | Email reminder (24h) | Slack reminder (1h) |
| Call moved or cancelled | Email | Slack |
| Ticket raised | Email confirmation | Slack; urgent and high priority also open a handover |
| Handover | Told when someone will reply, with the reference | Slack post plus email to the routed person, with summary and transcript link |

Reminders are ordinary outbox jobs scheduled for later. If the booking is cancelled or moved,
the old reminders notice and skip themselves. Client reminders respect the do-not-contact list.

## Handover routing (config `handover.rules`)

| Reason | Slack | Urgency |
|---|---|---|
| security_incident | #security-urgent (+ on-call email) | P1 |
| complaint_or_legal | #client-escalations | high |
| technical_issue | #support | high |
| pricing_negotiation | #sales-handover | normal |
| sensitive | #client-escalations | normal |
| human_requested / cannot_answer | #handover | normal / low |
| any reason, enterprise client | account manager's email, urgency raised to at least high | |

Outside office hours (Mon–Fri 09:00–17:30 London), visitors are told which day someone will
reply. After a handover the assistant stops answering that conversation, and the visitor is
told a consultant is looking after it.

## Demo data (`assistant-seed`)

| Company | Contact (verify with this email) | Notes |
|---|---|---|
| Northbridge Legal LLP | priya.shah@northbridge.example.com | 2 tickets, 1 project |
| Halcyon Manufacturing Group (enterprise) | tom.okafor@halcyon.example.com | Account manager Cora; 1 ticket, 2 projects |

Verification codes appear in Mailpit at http://localhost:8025.

## Public holidays

Client emails, discovery-call slots and "we'll get back to you on…" promises skip public
holidays, using the free `holidays` library (`workdays.py`), including "observed" days such as
Monday 28 December 2026. Each rule set (`messaging.hours`, `booking.rules`, `office.hours`)
names its calendar (`holidays_country`, `holidays_subdiv`; England by default), so staff can
change it in the dashboard's Settings. Visitors writing before opening time on a working day
are told the office opens later that day, not "tomorrow".

## Connecting real Slack and Jira (free, optional)

Put the values in `.env` yourself (never commit it), then check them read-only:

```bash
cd apps/api
../../.venv/Scripts/assistant-check-integrations
```

`--send-slack-test` posts one test message and `--create-jira-test` creates one test issue.

**Slack:** at api.slack.com/apps create an app ("From scratch", in your workspace), turn on
**Incoming Webhooks**, and add a webhook for the channel that should receive notifications.
Set `SLACK_NOTIFY_WEBHOOK_URL`, and optionally a second webhook for system alerts in
`SLACK_ALERT_WEBHOOK_URL`. Each webhook posts to one channel; the intended route (e.g.
#security-urgent) is shown at the top of each handover message.

**Jira:** create a project (the key, e.g. `SUP`, goes in `JIRA_PROJECT_KEY`) and an API token at
id.atlassian.com → Security → API tokens. Set `TICKETING_PROVIDER=jira`, `JIRA_BASE_URL`
(`https://<site>.atlassian.net`), `JIRA_EMAIL` (your Atlassian login) and `JIRA_API_TOKEN`. If
the check says "Task" isn't an issue type in your project, set `JIRA_ISSUE_TYPE` to one it
lists. Tickets are mirrored with a label (`assistant-t-1001`), so a retried create finds the
existing issue instead of making a duplicate. Statuses are read live from Jira.

Restart the API and the worker after changing `.env`. The worker is what actually posts to
Slack and creates the Jira issues.

## Database

Migration `c95526c1a3c2` adds `tickets` (replacing `tickets_ref`), `projects` and
`verification_codes`, handover reference numbers, and companies' account managers. Migration
`3312b15a9988` removed Telegram.
