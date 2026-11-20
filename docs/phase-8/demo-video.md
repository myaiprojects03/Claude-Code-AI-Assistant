# Demo video · recording guide and narration

`scripts/demo_walkthrough.py` plays the whole demo by itself in Microsoft Edge, with a caption
on screen for each step. You record the screen and read the narration below over it (live,
or recorded afterwards and laid over the video). Length at normal speed: about 9 minutes.

## Before you record

1. Production stack running: `.\scripts\prod.ps1 status` shows everything running and
   `readyz: 200` (if not, `.\scripts\prod.ps1 up`).
2. Mailpit running (the stand-in inbox where the verification code is read from):
   `$env:XDG_DATA_HOME='D:\PodmanData'; .\.venv\Scripts\podman-compose.exe -f infra\compose.yaml up -d mailpit`
3. Model allowance: a full run uses roughly 40,000 to 50,000 Groq tokens. The free tier is
   200,000 a day per model, so plan on 3 or 4 takes a day. A free Gemini key
   (`GEMINI_API_KEY` in `.env.prod`, then `down`/`up`) adds a fallback.
4. Close other windows, turn on Do Not Disturb (Windows: Focus assist), and set sleep to
   *Never*.
5. A recorder (both free):
   - **Xbox Game Bar**, built into Windows: **Win + Alt + R** starts and stops recording the
     active window. Videos go to `Videos\Captures`.
   - **OBS Studio** (obsproject.com), if you want a microphone track, a webcam, or to record
     the whole screen.

## Recording

```powershell
.\.venv\Scripts\python scripts\demo_walkthrough.py --wait
```

Edge opens full screen on a black page. Start the recorder, click back in PowerShell, press
**Enter**, and keep your hands off the mouse and keyboard until it prints
`Done. Stop the recording.`

| Option | What |
|---|---|
| `--speed 0.8` | slower (more reading time); `1.2` is quicker. Message spacing stays the same |
| `--only website,client` | just some scenes: `intro, website, client, emails, consultant, admin, outro` |
| `--headless` | no window, for checking the script works |

Things to know:
- The assistant's words differ a little each take: it's a real model. The script answers
  based on what was just asked.
- Messages are at least 22 seconds apart because of Groq's free-tier limit. If the model is
  still busy, the reply is "Sorry, I'm having trouble right now" and the script waits 30
  seconds and asks again. Cut that part out in editing, or do another take.
- The knowledge-assistant answer takes about a minute (local model on a CPU). Speed that
  part up in editing.
- Each take creates real demo records: a lead, a booking, a ticket and a handover. That's fine;
  the booking moves to the next free slot.
- PowerShell prints each exchange as it happens, which is handy for spotting a bad take.

## Narration

The captions match these steps. Times are approximate at normal speed.

### 1. Intro (0:00, title card)
> This is an AI assistant I built for a fictional 50-person IT consulting firm. It talks to
> website visitors, supports existing clients, hands over to a real person when it matters,
> and gives staff a private knowledge assistant, all on a completely free stack.

### 2. Website visitor (0:10)
**The firm's website.**
> Here's the firm's website. The chat widget in the corner is one script tag, so it works
> on any site.

**A visitor opens the chat.**
> A visitor opens the chat. Before anything is stored, they accept the privacy notice.

**It answers from the firm's approved content.**
> It answers from the firm's own approved pages, not from whatever the model happens to
> know. If something isn't covered, it says so and offers a consultant.

**Prices and promises are off limits.**
> Pricing is a conversation for a person, so it won't invent a day rate. And if the model
> ever let a price slip through, a separate check on every reply would catch it.

**Qualifying the lead and booking a call.**
> Now the visitor wants a call. Fixed rules, not the model's opinion, decide whether they're
> a qualified lead. Then it checks real availability and offers times.

**Booked.**
> Booked. Behind the scenes it's one booking, one confirmation email and one Slack message
> to the consultant, even if the visitor double-clicks or a service is briefly down.

### 3. Existing client (2:30)
**An existing client.**
> Now an existing client, from a law firm, wants to check their support tickets.

**Client data needs an email check first.**
> Nothing about a client is shown until they prove their email. It sends a one-time code,
> and never reveals whether an address belongs to a client.

**The code arrives by email.**
> The client types the code they received. The assistant itself never sees or shows it.

**Only their own company's records.**
> Once verified, they see their own company's tickets, and nothing of any other client's.

**An urgent problem.**
> Their VPN is down for the whole office. It logs an urgent ticket and, because it's urgent,
> hands the conversation to a person straight away.

### 4. The emails (4:30)
**What arrived by email.**
> These are the emails that went out: the booking confirmation, the verification code and
> the ticket receipt. For the demo they land in a test inbox. Staff get Slack instead.

### 5. Consultant (5:00)
**The staff dashboard.**
> Staff sign in to the dashboard through the firm's own single sign-on.

**Signed in as a consultant.**
> This is a consultant's view. Each role only sees what it needs.

**Conversations the assistant handed over.**
> Here's the urgent VPN handover. It's already been posted to Slack and emailed, and the
> client was told when to expect a reply.

**Taken.**
> The consultant takes it, so nobody else picks up the same person.

**Discovery calls the assistant booked.**
> And here's the call our visitor booked a few minutes ago.

**The private knowledge assistant.**
> The knowledge assistant answers questions about clients and projects from internal
> documents, only the ones this person is allowed to see.

**Running on this machine.**
> It runs on a local model, so confidential documents never go to a cloud AI. That's why it
> takes about a minute.

**Every answer cites its sources.**
> Every answer lists its sources, and any figure it can't back up is marked "to confirm"
> rather than made up.

### 6. Admin (7:00)
**Signed in as an admin.**
> An admin sees the whole picture: conversations, qualified leads, calls and handovers.

**Every chat, newest first.**
> Every conversation is here. Opening a transcript is recorded in the audit log.

**Leads qualified by fixed rules.** and **Client tickets.**
> Leads, qualified by the rules, and client tickets with the urgent one on top.

**Website content, editable without a developer.**
> Staff edit the website pages here, and publishing is blocked if a page mentions prices or
> promises. The questions it couldn't answer become the content to-do list.

**Rules as forms, not code.**
> Qualification rules, availability, handover routing and office hours are forms. Draft,
> check and publish, live in seconds.

**Who can see which client's documents.**
> Admins decide which consultant sees which client. That filter is applied before the search,
> so another client's data never reaches the model.

**UK GDPR requests.** and **Everything held about one person, nobody else.**
> For data-protection requests, an admin looks up a person by email and can export or erase
> everything about them.

**Who looked at or changed what.**
> And the audit log shows who looked at or changed what, including the lookup we just did.

### 7. Outro (8:40, title card)
> It's built on a free stack: FastAPI, Postgres, Keycloak, React, free model tiers for the
> public chat and a local model for confidential work. It has 256 automated tests, red-team
> testing, and UK GDPR tools, and it's live. Thanks for watching.
