# Phase 0 · Requirements (draft for sign-off)

The firm is a fictional IT consulting company of about 50 people serving SMEs and enterprise clients, called **"the Firm"** below. Everything marked *(default)* is a proposed value you can change.

## 1. Scope (from the brief)

| # | Capability | In scope |
|---|---|---|
| C1 | Client-facing web chat: explain services, FAQs, qualify leads, book discovery calls | Yes |
| C2 | Voice agent (phone or browser calls) | **No: removed 2026-11-13 to keep the stack free** |
| C3 | Existing-client support: identity check, ticket status, project updates, scheduling, create and update tickets | Yes |
| C4 | Internal knowledge assistant: sourced answers, drafting, strict client separation | Yes |
| C5 | Integrations: CRM, ticketing, calendar, email, Slack | Yes (HubSpot, Jira SM, Google Calendar, Brevo/Mailpit, Slack) |
| C6 | Human handover with full summary | Yes |
| C7 | Admin dashboard: logs, analytics, no-code editing | Yes |
| C8 | Security and reliability: idempotency, messaging hours, alerts, logs, role-based access, privacy | Yes |
| — | WhatsApp / LinkedIn channels | WhatsApp test number optional; LinkedIn **out of scope** (no free messaging API) |

## 2. Compliance and data residency *(default)*

- **GDPR applies** in full. The Firm is the controller; each vendor is a processor and needs a DPA (HubSpot, Atlassian, Google, Slack, Brevo and Groq all publish DPAs).
- **ISO 27001:** controls are mapped and evidenced (audit logs, access control, backups, incident process). Certification itself is out of scope because it costs money.
- **Data residency:** the database, vector store and transcripts are self-hosted, in the EU when deployed. Confidential internal documents are embedded **locally** (Ollama) and answered by the **local** model by default.
- **Retention** *(default)*: chat transcripts 12 months, unqualified leads 24 months, audit logs 3 years.
- **Data-subject rights:** export and delete by email or phone, from the dashboard.
- **Consent:** cookie-free widget; a consent notice before chat starts; marketing opt-in kept separate.

## 3. Brand voice *(default)*

- Professional, warm, concise. British English. No emoji. No more than 3 short sentences per chat turn.
- Talks as the Firm's assistant ("we"). Says it's an AI if asked, and says so in the first message.
- Approved service catalogue (placeholder, to be replaced with real content): Cloud migration · Managed IT and support · Cybersecurity assessments · Microsoft 365 / Google Workspace · Data and analytics · Custom software and integrations · IT strategy / vCIO.

### "Never say" list
1. Any price, rate, estimate, discount or budget fit ("that should fit your budget").
2. Delivery dates, deadlines or SLAs not in the approved content.
3. Guarantees or promises ("we'll fix it", "100% secure").
4. Named clients or case-study details not marked public.
5. Legal, compliance or security advice beyond general information.
6. Opinions on competitors.
7. Anything about another client, including whether someone is a client.

When asked about any of these, it says it can't confirm that and offers a call with a consultant.

## 4. Lead qualification *(default)*

Captured as structured fields and written to HubSpot:

| Field | Question (paraphrased naturally) | Values |
|---|---|---|
| need | What are you looking to solve? | free text + category from catalogue |
| company_size | Roughly how many employees? | 1–10, 11–50, 51–250, 251–1000, 1000+ |
| current_systems | What are you running today? | free text + tags (M365, GWS, AWS, Azure, on-prem…) |
| budget_range | Is there a budget range in mind? | <10k, 10–50k, 50–150k, 150k+, unknown |
| timeline | When would you like to start? | <1 month, 1–3, 3–6, 6+, exploring |
| role | Your role? | decision-maker, influencer, other |
| contact | Name, work email, company, phone (optional) | — |

**Qualified** = need matches the catalogue **and** company_size is 11 or more **and** (budget ≥ 10k **or** budget unknown and role is decision-maker) **and** timeline is 6 months or less.
**Enterprise** = company_size 1000+, which triggers enterprise routing.
Qualified and enterprise leads get offered a discovery call. Others get a resources link and an optional call.

## 5. Human handover rules *(default)*

| Trigger | Detected by | Route to | Channel | Urgency |
|---|---|---|---|---|
| Pricing negotiation or discount request | rule + LLM classifier | Sales lead | Slack `#sales-handover` | normal |
| Security incident ("breach", "ransomware", "hacked", "phishing", etc.) | rule (keywords) + LLM | Security on-call | Slack `#security-urgent` + email | **P1, immediate** |
| Enterprise account (any request) | CRM flag | Account manager on record | Slack DM | high |
| Complaint, legal, contract dispute | LLM classifier | Delivery manager | Slack `#client-escalations` | high |
| Sensitive or personal topics | LLM classifier | Delivery manager | Slack | normal |
| Customer asks for a human | rule | On-duty consultant | Slack | normal |
| Assistant can't answer twice in a row | rule | On-duty consultant | Slack | low |
| Technical issue from an existing client | LLM + ticket | Ticket assignee or team queue | Jira + Slack | by severity |

Every handover sends: who the customer is, CRM link, verified status, reason, a 5-line summary, the full transcript link, and the next action they asked for. Outside office hours *(default Mon–Fri 09:00–17:30 Europe/London)*, the customer is told when a consultant will follow up, and the ticket is queued.

## 6. Identity verification (existing clients)
Email or phone is matched against HubSpot contacts, then a 6-digit one-time code goes to the email on file (valid 10 minutes, 3 attempts). Until verified, the assistant shares no ticket, project or account details.

## 7. Messaging limits *(default)*
**Who gets what** *(decided 2026-11-16)*:

| Message | Client / visitor | Consultant (team) |
|---|---|---|
| Booking confirmation | Email, at booking | Slack, at booking |
| Reminder | Email, 24 hours before | Slack, 1 hour before |

Client emails go out only 09:00–19:00 in the **recipient's** timezone, Mon–Fri, never on public holidays; the do-not-contact list is checked first. A 24-hour reminder that would fall outside those hours is sent at the start of the previous allowed window instead. Slack messages to the team are not time-restricted. Every confirmation and reminder is sent at most once, enforced by idempotency keys.

## 8. Internal content audit
Since this is a portfolio project, we'll **create a synthetic corpus** in Phase 5: about 30 documents across 3 fictional clients (proposals, technical docs, SOPs, case studies, templates), each tagged with `client_id` and `sensitivity` (public / internal / client-confidential). The leakage tests depend on having more than one client.

## 9. Non-functional targets
- Chat first-token latency under 2 s (Groq).
- Zero duplicate bookings or messages under retries (tested).
- Every conversation, tool call and handover logged with an actor and a timestamp.
- Uptime is best effort (free hosting); the system degrades gracefully when an integration is down.

## Sign-off
- [x] LLM decision: free (Groq default, Ollama for internal/offline, Claude adapter off), decided 2026-11-13
- [x] Voice/call system removed from scope (2026-11-13)
- [ ] Rest of the stack accepted
- [ ] Defaults above accepted or edited
- [ ] Architecture accepted
- [ ] Risk register reviewed
