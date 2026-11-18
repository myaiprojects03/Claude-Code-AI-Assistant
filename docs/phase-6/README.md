# Phase 6 · Staff dashboard

An operations dashboard for the firm's staff. The visual theme is inspired by
harrygeorge.design (theme only); see [design-system.md](design-system.md). Layout, components
and content are this project's own.

## Run it

```bash
powershell -File scripts/stack.ps1 up
cd apps/api
../../.venv/Scripts/assistant-seed --demo     # optional: fictional activity to look at
../../.venv/Scripts/uvicorn assistant.api.main:app --reload --port 8000
cd ../web && npm install && npm run dev       # http://localhost:3000
```

Sign in with a dev account from `infra/keycloak/realm-assistant.json`: `admin.dev`,
`manager.dev` or `consultant.dev`.

## Pages

| Page | What it shows | Who |
|---|---|---|
| Overview | Headline sentence, 5 stat tiles (conversations vs the previous week, qualified rate, calls, people waiting, model tokens against the free daily allowance), 3 rate tiles (booking conversion, escalation rate, questions it couldn't answer), 14-day chart, handover queue, unanswered topics (managers), upcoming calls | all staff |
| Handovers | Open, taken and resolved, most urgent first. Each handover is either in the **team queue** or **for a named person** (an enterprise client's account manager, or whoever a manager reassigned it to). **Consultants** take team-queue handovers and their own; others they can see but not take. **Managers and admins** take any and **reassign** it to a person or back to the team queue (the new owner is told in Slack). The first person to click "I'll take it" keeps it. **Mark resolved**: whoever took it, or a manager. Names are shown, not ids. Taking and resolving are posted to the handover's Slack channel. When the last open handover of a conversation is resolved, the assistant answers that visitor again. Every action is audited | all staff |
| Conversations | Search, status filter, last message, lead status. Internal-assistant chats are never listed: they're private to their author | manager, admin |
| Transcript | Visitor and assistant messages; tool calls and results collapsed, with refused calls marked | manager, admin (each view audited) |
| Leads | Search by name, email, company or need; status filter with counts; expand for the visitor's own words and the summary | manager, admin (list audited) |
| Bookings | Upcoming and past discovery calls; search by visitor, company, topic or consultant | all staff |
| Tickets | Open or all, most urgent first. Each load checks Jira-linked tickets' **live status** with Jira (read-only, in parallel, 4 s timeout) and saves it, so a ticket an engineer closed in Jira drops off the open list. If Jira can't be reached, the page says so and shows the last known status | all staff |
| Settings | Greeting and fallback replies as text. Qualification, handover, booking, office-hours and messaging-hours rules as **forms** (choices, day pickers, time fields, a routes table) with a **live preview**: which status a sample visitor gets, whether a message raises a security handover and where it goes, which call times would be offered, what a visitor is told about office hours, when a reminder would be sent. Rules are checked against a schema on save and publish, so a broken rule can't go live. Draft, publish, history; "edit as JSON" stays as an advanced option | view: manager; publish: admin |
| Website content | The FAQ and service pages the website assistant answers from. Edit in markdown with a live check (no prices, rates, guarantees or delivery promises) and a section preview; save a draft, publish, or restore an older version. Publishing re-indexes at once. "Test a question" shows what the assistant would find; "Questions it couldn't answer" groups similar unanswered questions | view and test: manager; save and publish: admin |
| Knowledge assistant | Chat with the internal assistant (local model). Answers come from internal documents **and live records** (handovers, calls, tickets, leads for managers) when the question names a person, client or reference. Sources are numbered; live records are marked and link to the dashboard. A "can't find" answer shows what was searched instead of presenting it as sources. Past chats are private to their author | all staff |
| Client access | Which clients' documents each consultant can see in the knowledge assistant; give or remove access (audited) | admin |

## How it fits together

- **Auth:** OIDC authorization code with PKCE (`oidc-client-ts`) against Keycloak. Tokens are
  kept in sessionStorage and refreshed silently. The API checks the token audience and roles
  on every request, so the UI's role-based hiding is only a convenience, not the security.
- **Data:** `/v1/dashboard/*` endpoints, plus the existing admin routes for conversation
  detail, handovers and config, and `/v1/internal/*` for the knowledge assistant.
- **Content editing:** each public page is a versioned config key `content.public.<slug>`
  (the same draft, publish and history flow as settings). A published version overrides the
  file in `content/public/`, and `assistant-ingest` respects it, so the CLI never reverts a
  dashboard edit. Publishing runs the reply rules on the text (422 if it fails) and re-indexes
  in the same transaction, so the new text is searchable the moment it's live.
- **Unanswered topics:** every time `search_knowledge` returns nothing, the visitor's question
  is a gap. Questions are grouped by embedding similarity (cosine ≥ 0.8, local model).
- **Rates:** booking conversion = qualified or enterprise leads (30 days) with a call booked;
  escalation rate = visitor conversations (30 days) with a handover.
- **Charts:** the single-series column chart follows the dataviz rules: columns ≤ 24px with 4px
  rounded tops, a hairline grid, selective labels (peak and today), a hover and keyboard
  tooltip, and a screen-reader table.
- **Theme:** CSS tokens in `src/styles/app.css`, dark first, with light as an inverted scale. It
  follows the OS setting and can be cycled by hand (system → dark → light).

## Verified (2026-11-16)

- Real Keycloak sign-in and sign-out. After sign-out the password is required again.
- Every page renders with the demo data.
- Settings: edited the greeting, saved a draft, published it, and confirmed the live chat
  served the new text within 10 seconds. Then restored the original as v2 (v1 archived).
- Phone width (375px): two-column tiles, slide-out menu with a backdrop and Escape to close.
- Light mode.
- `npm run build` passes, and the 5 dashboard API tests pass (146 tests in total).

## Verified (2026-11-18)

- Website content: added a FAQ answer, saved it as a draft (not live yet), published it, and
  "Test a question" found it at once as the top match (79%). Adding a price and a guarantee was
  flagged by the live check and saving was blocked.
- Knowledge assistant: asked about the Northbridge cutover; the local model answered from the
  Northbridge runbook and proposal, with 5 numbered sources. The chat reloads from the list.
- Client access: the seeded assignment is listed; give and remove are covered by tests.
- Overview: booking conversion, escalation rate and the unanswered-topics panel render with the
  demo data (`assistant-seed --demo` now adds unanswered questions too).
- No horizontal scrolling at phone width (375px) on the new pages.
- Settings: the five rule forms render with previews. Turning off "51-250 staff" changed the
  sample visitor from Qualified to Unqualified ("below minimum company size"); an end time before
  the start time showed the error in the preview and the save was refused by the API.
- Search: "brightwater" narrowed 22 leads to 3; "cloud" found the one cloud-migration call.
- Handovers: as admin, took and resolved one ("Taken by Ada Admin", then "Resolved by Ada Admin") and
  reassigned one to Cora and then to the manager. Signed in as the consultant: the manager's
  handover shows "Needs a manager to reassign" with no button and no reassign controls, and their
  own is takeable.
- `npm run build` passes; 222 API tests pass (29 in `tests/test_db_phase6.py`).

## Known gaps

- No consultant reply-to-visitor screen yet: after a handover, staff follow up by email or phone.
- The assistant's system prompts aren't editable from the dashboard, on purpose: they hold the
  safety rules (no prices or promises, sources only, client separation). They change through
  code review.
- Data-subject export and deletion (GDPR) belong to Phase 7.
