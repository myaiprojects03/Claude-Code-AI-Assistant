# Staff guide · the dashboard

For consultants and managers at Acme IT Consulting. Admins: see also the
[admin guide](admin-guide.md).

The website assistant talks to visitors and existing clients around the clock. When it
can't or shouldn't handle something itself, it hands over to a person. The dashboard is
where you pick those up, see what's booked, follow client tickets, and ask the knowledge
assistant about clients and methods.

## Signing in

Open the dashboard (demo: `https://desktop-q5clvqu.tail751d64.ts.net:8443`) and choose
**Sign in with company SSO**. Use the account your admin gave you. Ten wrong passwords in a
row lock the account for a while.

What you see depends on your role:

| Page | Consultant | Manager | Admin |
|---|---|---|---|
| Overview | ✓ | ✓ | ✓ |
| Handovers | team queue + yours | all, can reassign | all, can reassign |
| Bookings (discovery calls) | ✓ | ✓ | ✓ |
| Tickets | ✓ | ✓ | ✓ |
| Knowledge assistant | firm-wide + your clients' documents | every client | every client |
| Conversations, Leads | - | ✓ | ✓ |
| Website content, Settings | - | view and test | edit and publish |
| Client access, Privacy requests, Audit log | - | - | ✓ |

## Overview

The first page: conversations this week, qualified leads, discovery calls coming up, how
many people are **waiting for a person**, and the topics the assistant couldn't answer. The
red number on **Handovers** in the menu means something urgent is waiting.

## Handovers: when the assistant steps aside

The assistant hands a conversation to a person when a visitor asks for one, or the
conversation turns to pricing, a complaint or legal matter, an urgent technical problem, or
a security incident. Every handover has already been posted to Slack and emailed to the
right person, and the visitor has been told honestly when to expect a reply (during office
hours, or the next working day).

Each handover shows who it is, why it was handed over, how urgent it is, a short summary,
and where it was routed. It moves through three tabs:

1. **Open**: nobody has it yet. Click **I'll take it**. It moves to **Taken** under your
   name, so nobody else picks up the same person.
2. **Taken**: being worked on. Contact the person (their details are on the handover),
   sort it out, then click **Mark resolved**.
3. **Resolved**: done, with who resolved it and when.

Consultants can take handovers from the **team queue** and ones **assigned to them**. One
assigned to someone else (say, another account manager's client) shows who has it, and a
manager can reassign it. Managers and admins can take any handover and **reassign** it to a
person or back to the team queue.

A security incident is urgent: follow the security incident SOP (ask the knowledge
assistant, "Summarise our security incident SOP").

## Bookings: discovery calls

Calls the assistant booked with prospects, upcoming and past, searchable by visitor,
company, topic or consultant. Times are UK time. The client gets an email confirmation and
a reminder 24 hours before; the consultant gets a Slack message at booking and 1 hour
before. Clients reschedule or cancel through the assistant (after an email check), and the
change shows here.

## Tickets: client support requests

Raised by **verified** existing clients through the assistant ("our VPN is down"). Urgent
and high-priority tickets also open a handover. Each ticket has a reference (like
`T-1003`), the client company, priority and status. In a real setup the engineers work
tickets in Jira and the status here follows; in the public demo tickets stay in the
assistant's own database.

**Ticket or handover?** A ticket is a piece of technical work for a client, done by
engineers. A handover is a conversation that needs a person to reply. An urgent ticket
creates both.

## Knowledge assistant

Ask questions about clients, projects and how the firm works, or have it draft something:

- "What's the cutover plan for Northbridge's mailbox migration?"
- "What's happening with Alex Bob's query?" or "Status of T-1003?"
- "Draft a follow-up email after the 21 October Northbridge meeting."
- "Draft a proposal outline for a 40-seat Microsoft 365 migration."

How it works, and what to expect:

- It answers **only from internal documents you're allowed to see**: firm-wide ones, plus
  the clients your admin has given you access to. Managers and admins see every client.
- Every answer lists its **sources** (`[1]`, `[2]`, ...). Check them before you rely on it.
- A figure it can't find in a source shows as **[TO CONFIRM]**. Fill those in yourself.
- If your question names a person, a company or a reference, it also looks up the **live
  records** for them: handovers, bookings and tickets (and leads, for managers). It never
  shows chat transcripts; it links to the dashboard instead.
- It runs on a **local model on this machine**, so confidential documents never leave it.
  That makes it slow: about 30 to 60 seconds an answer, longer for full drafts.
- It **sends** nothing unless you ask it to in your own words ("send this to
  priya.shah@northbridge.example.com"). Email goes only to the firm's own addresses or to
  clients you have access to.
- Your chats are **private to you** and kept for 180 days. Up to 12 questions a minute.

If it says it's degraded, the local model isn't running: tell whoever runs the system.

## Managers: conversations and leads

**Conversations** lists every website chat, newest first, searchable by name or email.
Opening a transcript is **recorded in the audit log**, so open one only when you need it.

**Leads** are prospects the assistant qualified. The decision (qualified, qualifying, not a
fit) comes from the **rules in Settings**, not the model's opinion. Search by name, email,
company or need.

**Website content** and **Settings**: managers can read everything and test questions
against the website content; publishing changes needs an admin.

## Good to know

- The website assistant never quotes prices, rates or delivery promises, and says "I don't
  know" rather than guess. If a visitor needs a price, that's a handover.
- Clients see their own company's tickets, projects and bookings only after proving their
  email with a code. It never confirms whether an email belongs to a client.
- Everything you look at that involves personal data is logged. That's for UK GDPR and
  ISO 27001, not to watch you.
