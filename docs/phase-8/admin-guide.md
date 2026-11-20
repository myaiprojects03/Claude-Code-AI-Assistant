# Admin guide

For admins of the assistant: staff accounts, client access, website content, settings,
privacy requests and the audit log. The everyday pages (handovers, bookings, tickets, the
knowledge assistant) are in the [staff guide](staff-guide.md). Running and fixing the
system: [runbook](runbook.md).

## Staff accounts (Keycloak)

Sign-in is handled by Keycloak. Its admin console is **only reachable on the PC that runs
the system**, never from the internet:

1. On that PC, open `http://127.0.0.1:8180/auth/admin` and sign in as `admin` with
   `KC_ADMIN_PASSWORD` from `.env.prod`.
2. Top left, switch the realm from *master* to **assistant**.

**Add a person**
1. **Users**, **Add user**: username, email (their work address), first and last name.
   Tick *Email verified*. **Create**.
2. **Credentials** tab, **Set password**. Leave *Temporary* on so they choose their own at
   first sign-in.
3. **Role mapping** tab, **Assign role**, filter by realm roles, pick **one** of:
   - `consultant`: handovers (team queue and their own), bookings, tickets, the knowledge
     assistant for firm-wide documents plus the clients you grant them (below);
   - `manager`: everything a consultant has, plus conversations, leads, every client's
     documents, reassigning handovers, and viewing content and settings;
   - `admin`: everything, plus publishing content and settings, client access, privacy
     requests and the audit log.
4. For a consultant, give them client access (next section) using the **same email**.

**Remove a person:** open the user and switch **Enabled** off (keeps their name on past
handovers and audit entries), or **Delete**. Then remove their client access. They are
signed out within 15 minutes, when their current sign-in expires.

**Change a role:** Role mapping, unassign the old role, assign the new one. It applies at
their next sign-in.

The three demo accounts (`admin.dev`, `manager.dev`, `consultant.dev`) get their passwords
from `.env.prod` every time the system starts. Change those in `.env.prod`, not in Keycloak.

Consultants who take **discovery calls** (name, email, skills, calendar) are set in the
seed data (`apps/api/src/assistant/seed.py`), not in the dashboard.

## Client access

**Client access** decides which clients' confidential documents each consultant sees in the
knowledge assistant. Managers and admins always see every client; consultants see
firm-wide documents plus the clients listed here.

- **Give access:** enter the consultant's sign-in email, pick the client, **Give access**.
- **Remove:** use the action on their row in **Current access**.

It applies to their next question and is recorded in the audit log. Give access only to
clients the person actually works on.

## Website content

The FAQ and service pages are **the only thing the website assistant answers from**. If it
isn't on a page, the assistant says it doesn't know and offers a consultant.

1. Open a page, edit the text.
2. **Check**: it looks for prices, rates, guarantees and delivery promises, which can't be
   published (the same rule as the assistant's replies), and shows a preview as a visitor
   would see it.
3. **Save as draft**, then **Publish**. The assistant uses it immediately.

**Add page** creates a new one. Pages marked *From the repository* come from
`content/public`; *Edited here* means a dashboard version overrides the file.

**Test a question** shows what the assistant would find for a visitor's question in the
live content. **Questions it couldn't answer** groups what visitors asked that no page
covered. It's the best to-do list for new content.

Managers can read pages and test questions; saving and publishing need an admin.

## Settings

Change how the assistant behaves **without a developer**:

| Setting | What it controls |
|---|---|
| Greeting | first message a visitor sees |
| Reply when the model is unavailable | what visitors see if every model is down |
| Reply that replaces a blocked answer | shown instead of an answer with a price or promise |
| Lead qualification rules | company sizes, needs, budgets and timelines that qualify; what counts as enterprise |
| Discovery-call availability | which consultant skill takes calls, days, hours, how many slots are offered |
| Handover keywords and routing | security words (and phrases that aren't incidents), where each kind of handover goes |
| Office hours | used for "we'll reply by..." promises; UK bank holidays included |
| When client emails may be sent | quiet hours for reminders and confirmations |

Each one has a form (or **Advanced: edit as JSON**). **Check** previews the effect, for
example how a sample message would be routed. Then **Save as draft** and **Publish**: it's
live within 10 seconds and recorded in the audit log. Every version is kept; to undo, change
it back and publish again.

The assistants' own instructions (their system prompts) are deliberately **not** editable
here. That stops a stolen admin sign-in from rewriting the rules about prices, promises and
client data.

## Privacy requests (UK GDPR)

For requests from a person about their data. Search by their **email address**.

**Find a person** shows what is held: conversations and messages, leads, discovery calls,
tickets raised, handovers, consent records and code requests.

**Export** (right of access) downloads one JSON file with everything about that person and
nobody else. Send it to them through a secure channel after confirming who they are.

**Erase** (right to erasure). **This can't be undone.** Type the email twice and confirm. It
removes their name, email and phone, every chat message, lead answers and handover notes;
cancels their future calls and calendar events; stops queued emails to them; and puts the
address on the do-not-contact list. Tickets stay with the client company for the contract,
without the person's name. Records are anonymised rather than deleted, so reports and the
audit trail still add up. If HubSpot is ever switched on, erase them there too.

**Retention** shows how long data is kept. It's cleared automatically every 6 hours:

| Data | Kept for |
|---|---|
| Visitor chats (with nothing still open) | 1 year after the last activity |
| Knowledge-assistant chats | 180 days |
| Verification-code records | 7 days after they expire |
| Sent-message records | 90 days |
| Audit log | 2 years |

Every lookup, export and erasure is recorded in the audit log by a fingerprint of the email,
never the address itself.

## Audit log

Who looked at or changed what, newest first: transcripts opened, lead lists viewed,
knowledge-assistant lookups, handovers taken and resolved, settings and content published,
access granted, privacy requests. Filter by kind; click a row for the details; **Load
older** to page back. Nobody can edit or delete entries; they're kept for 2 years.

Worth a look now and then: transcripts opened without a reason, unexpected access grants,
settings published by someone unexpected.

## Admin checklist

| When | What |
|---|---|
| Someone joins | Keycloak user with one role; client access for consultants |
| Someone leaves | disable in Keycloak; remove client access |
| Weekly | Questions it couldn't answer, then add content; glance at the audit log |
| Before any change to the system | `.\scripts\prod.ps1 backup` |
| A privacy request | verify the person, then export or erase within a month |
| A security incident | follow the security incident SOP; `.\scripts\prod.ps1 funnel-off` takes the demo offline at once |
