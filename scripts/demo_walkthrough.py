"""Automated walkthrough of the whole project, for the screen-recorded demo video (Phase 8).

Opens Microsoft Edge (already on Windows; nothing else to download) and plays the demo by
itself at a watchable pace, with a caption on screen for each step:

  1. Website visitor: services question, a price question it won't answer, booking a call
  2. Existing client: email check with a one-time code, tickets, an urgent ticket + handover
  3. Emails the visitor and client received (Mailpit stands in for real inboxes)
  4. Consultant: overview, taking the handover, the private knowledge assistant
  5. Admin: conversations, leads, bookings, tickets, content, settings, access, privacy, audit

Needs: the production stack up (`.\\scripts\\prod.ps1 up`), Mailpit running for the codes
(`podman-compose -f infra\\compose.yaml up -d mailpit`), and Playwright in the venv
(`.venv\\Scripts\\pip install playwright`). Addresses and demo passwords come from .env.prod.

  .venv\\Scripts\\python scripts\\demo_walkthrough.py                 # everything
  .venv\\Scripts\\python scripts\\demo_walkthrough.py --speed 0.8     # slower
  .venv\\Scripts\\python scripts\\demo_walkthrough.py --only website,dashboard
  .venv\\Scripts\\python scripts\\demo_walkthrough.py --wait          # Enter to start, for recording

Model replies vary from run to run, so the visitor's answers are chosen from what the
assistant just asked. Real records are created (a lead, a booking, a ticket, a handover).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
MAILPIT = "http://localhost:8025"
VISITOR = {
    "name": "Sam Taylor",
    "company": "Brightpath Logistics",
    "email": "sam.taylor@brightpath.example.com",
}
CLIENT_EMAIL = (
    "priya.shah@northbridge.example.com"  # seeded demo client (Northbridge Legal)
)
SCENES = ["intro", "website", "client", "emails", "consultant", "admin", "outro"]

SPEED = 1.0


# ---------------------------------------------------------------- helpers


def env_prod() -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in (ROOT / ".env.prod").read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$", line):
            cfg[m[1]] = m[2].strip()
    return cfg


def beat(page: Page, seconds: float) -> None:
    page.wait_for_timeout(int(seconds * 1000 / SPEED))


CAPTION_JS = """([title, sub]) => {
  let el = document.getElementById('__demo_cap');
  if (!el) {
    el = document.createElement('div');
    el.id = '__demo_cap';
    Object.assign(el.style, {
      position: 'fixed', left: '50%', top: '18px', transform: 'translateX(-50%)',
      zIndex: 2147483647, background: 'rgba(12,12,12,.92)', color: '#fff',
      font: '500 17px/1.45 "Segoe UI", system-ui, sans-serif', padding: '12px 22px',
      borderRadius: '12px', maxWidth: '62vw', textAlign: 'center', pointerEvents: 'none',
      border: '1px solid rgba(255,255,255,.18)', boxShadow: '0 8px 30px rgba(0,0,0,.45)',
      transition: 'opacity .25s',
    });
    document.documentElement.appendChild(el);
  }
  el.innerHTML = '<div style="font-weight:600">' + title + '</div>' +
    (sub ? '<div style="opacity:.72;font-size:14px;margin-top:2px">' + sub + '</div>' : '');
  el.style.opacity = title ? '1' : '0';
}"""


def caption(page: Page, title: str, sub: str = "", hold: float = 2.5) -> None:
    print(f"  > {title}")
    try:
        page.evaluate(CAPTION_JS, [title, sub])
    except PlaywrightError:  # page mid-navigation: try once more
        page.wait_for_timeout(500)
        page.evaluate(CAPTION_JS, [title, sub])
    beat(page, hold)


def card(page: Page, title: str, lines: list[str], hold: float = 6) -> None:
    items = "".join(f"<li>{x}</li>" for x in lines)
    page.set_content(
        f"""<html><head><title>{title}</title></head>
        <body style="margin:0;height:100vh;display:grid;place-items:center;background:#0c0c0c;
          color:#f2f2f2;font-family:'Segoe UI',system-ui,sans-serif">
        <div style="max-width:880px;padding:40px">
          <div style="opacity:.55;letter-spacing:.08em;text-transform:uppercase;font-size:14px">
            Acme IT Consulting · AI assistant</div>
          <h1 style="font-size:48px;font-weight:600;margin:12px 0 24px">{title}</h1>
          <ul style="font-size:21px;line-height:1.7;opacity:.85;padding-left:22px">{items}</ul>
        </div></body></html>"""
    )
    beat(page, hold)


def type_into(locator, text: str) -> None:
    locator.click()
    locator.press_sequentially(text, delay=max(8, int(28 / SPEED)))


def smooth_scroll(page: Page, total: int, steps: int = 12) -> None:
    for _ in range(steps):
        page.mouse.wheel(0, total / steps)
        page.wait_for_timeout(int(140 / SPEED))


# ---------------------------------------------------------------- the chat widget


# Groq's free tier allows 8,000 tokens a minute per model, and one chat turn with tool calls
# uses 3,000 to 4,000. Messages are spaced at least this far apart (real seconds, whatever
# --speed says) so the demo doesn't hit the limit; the viewer is reading the reply meanwhile.
MIN_GAP_S = 22
TROUBLE = "having trouble right now"  # the assistant's reply when every model refused


class Chat:
    last_send = 0.0

    def __init__(self, page: Page):
        self.page = page
        self.replies = page.locator(".ac .msg.assistant:not(.typing)")

    def open(self) -> None:
        if not self.page.locator(".ac .panel").is_visible():
            self.page.locator(".ac .launcher").click()
        beat(self.page, 1)
        accept = self.page.locator(".ac .consent button.primary")
        if accept.is_visible():
            beat(self.page, 1.5)
            accept.click()
            self.wait_reply(0)

    def new_chat(self) -> None:
        self.page.locator(".ac header button", has_text="New chat").click()
        beat(self.page, 1)
        self.page.locator(".ac .consent button.primary").click()
        self.wait_reply(0)

    def wait_reply(self, before: int, timeout: float = 120) -> str:
        end = time.time() + timeout
        while time.time() < end:
            n = self.replies.count()
            if n > before:
                return self.replies.nth(n - 1).inner_text()
            if self.page.locator(".ac .error").is_visible():
                raise RuntimeError(self.page.locator(".ac .error").inner_text())
            self.page.wait_for_timeout(300)
        raise TimeoutError("no reply from the assistant")

    def _send(self, text: str) -> str:
        wait = Chat.last_send + MIN_GAP_S - time.time()
        if wait > 0:
            self.page.wait_for_timeout(int(wait * 1000))
        before = self.replies.count()
        box = self.page.locator(".ac textarea")
        type_into(box, text)
        beat(self.page, 0.4)
        Chat.last_send = time.time()
        box.press("Enter")
        try:
            return self.wait_reply(before)
        except (
            RuntimeError
        ):  # the widget's own error (visitor rate limit, network): resend
            self.page.wait_for_timeout(15000)
            self.page.locator(".ac form.composer button.primary").click()
            return self.wait_reply(before)

    def say(self, text: str, read: float = 4) -> str:
        reply = self._send(text)
        for _ in range(2):  # the free model tier was busy: give it time, then ask again
            if TROUBLE not in reply:
                break
            print("    (model rate limit: waiting 30 s and asking again)")
            self.page.wait_for_timeout(30000)
            Chat.last_send = 0
            reply = self._send(text)
        print(f"    visitor:   {text[:90]}")
        print(f"    assistant: {' '.join(reply.split())[:110]}")
        # Give the viewer time to read: longer replies stay longer.
        beat(self.page, read + min(len(reply) / 120, 6))
        return reply


def visitor_answer(reply: str) -> str | None:
    """What the prospect says next, based on what the assistant just asked."""
    r = reply.lower()
    if re.search(
        r"\b(booked|is confirmed|you're all set|confirmation (email|has been))", r
    ) and ("?" not in r[-80:]):
        return None  # done
    if re.search(r"\b(\d{1,2}(:\d{2})?\s?(am|pm)|\d{1,2}:\d{2})\b", r) and re.search(
        r"(monday|tuesday|wednesday|thursday|friday|slot|time|available)", r
    ):
        return "The first time you listed works for me, please book it."
    if "email" in r and "?" in r:
        return VISITOR["email"]
    if re.search(r"(how many|size|employees|staff|people)", r):
        return "About 40 people."
    if "budget" in r:
        return "We have budget set aside for this project."
    if re.search(r"(timeline|when .*(start|looking)|timeframe|how soon)", r):
        return "Within the next two months."
    if re.search(r"(name|who am i|who are you)", r) and "?" in r:
        return f"{VISITOR['name']} from {VISITOR['company']}."
    if re.search(r"(role|position|title)", r):
        return "I'm the operations director."
    if re.search(r"(where|location|region|based|timezone)", r):
        return "We're based in London."
    if re.search(r"(correct|confirm|go ahead|shall i|would you like me to|ok to)", r):
        return "Yes, that's all correct, go ahead."
    return "Yes please, let's find a time for a discovery call."


def client_answer(reply: str, code: str | None) -> str | None:
    r = reply.lower()
    if re.search(r"\bT-\d{3,}", reply) and re.search(
        r"(raised|logged|created|opened)", r
    ):
        return None  # urgent ticket logged
    if re.search(
        r"(summary|describe|description|details|what.s happening|impact|since when)", r
    ):
        return (
            "Office VPN down. Since 9am nobody in the London office can connect, so all "
            "40 staff are unable to reach client files. Please treat it as urgent."
        )
    if re.search(r"(priority|how urgent)", r):
        return "Urgent, please."
    if re.search(r"(shall i|would you like me to|should i|confirm|go ahead)", r):
        return "Yes please, raise it as urgent."
    if "code" in r and code:
        return f"The code is {code}"
    return None


# ---------------------------------------------------------------- Mailpit


def mailpit(path: str) -> dict:
    with urllib.request.urlopen(f"{MAILPIT}{path}", timeout=5) as resp:
        return json.load(resp)


def mailpit_ok() -> bool:
    try:
        mailpit("/api/v1/messages?limit=1")
        return True
    except (OSError, ValueError):
        return False


def wait_for_code(to: str, since: datetime, timeout: float = 60) -> str:
    end = time.time() + timeout
    while time.time() < end:
        for m in mailpit("/api/v1/messages?limit=20").get("messages", []):
            created = datetime.fromisoformat(m["Created"])
            if created < since or not any(a["Address"].lower() == to for a in m["To"]):
                continue
            if "verification code" in m["Subject"].lower():
                text = mailpit(f"/api/v1/message/{m['ID']}")["Text"]
                if found := re.search(r"\b(\d{6})\b", text):
                    return found[1]
        time.sleep(1)
    raise TimeoutError("no verification email in Mailpit (is the worker running?)")


# ---------------------------------------------------------------- scenes


def scene_intro(page: Page, cfg: dict) -> None:
    card(
        page,
        "An AI assistant for an IT consulting firm",
        [
            "Website chat that answers from approved content and books discovery calls",
            "Support for existing clients after an email check",
            "A hand-over to a real person when it matters",
            "A private knowledge assistant on a local model",
            "A staff dashboard, built on a free stack",
        ],
        hold=8,
    )


def scene_website(page: Page, cfg: dict) -> None:
    page.goto(cfg["PUBLIC_URL"])
    page.wait_for_load_state("networkidle")
    caption(
        page, "The firm's website", "The chat widget is one script tag on any site", 3
    )
    smooth_scroll(page, 1400)
    beat(page, 1.5)
    smooth_scroll(page, -1400, 8)
    chat = Chat(page)
    caption(
        page,
        "A visitor opens the chat",
        "Consent comes first: nothing is stored before it",
    )
    chat.open()
    caption(
        page,
        "It answers from the firm's approved content",
        "Never from the model's guesses",
    )
    chat.say("Hi, do you help companies move to Microsoft 365?")
    caption(
        page,
        "Prices and promises are off limits",
        "It offers a consultant instead of inventing a number",
    )
    chat.say("Roughly how much do you charge per day?")
    caption(
        page,
        "Qualifying the lead and booking a call",
        "Fixed rules decide who qualifies, then it checks real availability",
    )
    reply = chat.say(
        f"I'd like to book a discovery call about moving to Microsoft 365. I'm {VISITOR['name']} "
        f"from {VISITOR['company']}, about 40 people, based in London, {VISITOR['email']}."
    )
    for _ in range(7):
        answer = visitor_answer(reply)
        if answer is None:
            break
        reply = chat.say(answer)
    caption(
        page,
        "Booked",
        "One booking, one confirmation email to the visitor, one Slack message to the consultant",
        4,
    )


def scene_client(page: Page, cfg: dict) -> None:
    if not mailpit_ok():
        print("  ! Mailpit isn't running: skipping the client scene")
        return
    if not page.url.startswith(cfg["PUBLIC_URL"]):
        page.goto(cfg["PUBLIC_URL"])
    chat = Chat(page)
    chat.open()
    caption(page, "An existing client", "A new conversation from Northbridge Legal")
    chat.new_chat()
    chat.say(
        "Hi, I'm an existing client and I'd like to check our open support tickets."
    )
    caption(
        page,
        "Client data needs an email check first",
        "A one-time code goes to the address the client types",
    )
    since = datetime.now(UTC)
    reply = chat.say(CLIENT_EMAIL)
    code = wait_for_code(CLIENT_EMAIL, since)
    caption(
        page,
        "The code arrives by email",
        "Typed in by the client, never shown in the chat",
    )
    reply = chat.say(f"The code is {code}")
    if "ticket" not in reply.lower():
        reply = chat.say("Please show me our open tickets.")
    caption(
        page,
        "Only their own company's records",
        "Another client's ticket would simply be 'not found'",
    )
    caption(
        page,
        "An urgent problem",
        "It logs a ticket and hands over to a person straight away",
    )
    reply = chat.say(
        "Our VPN is down for the whole office and nobody can work. Please raise an urgent ticket."
    )
    for _ in range(3):
        answer = client_answer(reply, code)
        if answer is None:
            break
        reply = chat.say(answer)
    caption(page, "Ticket raised and the team alerted", "", 4)


def scene_emails(page: Page, cfg: dict) -> None:
    if not mailpit_ok():
        return
    page.goto(f"{MAILPIT}/")
    page.wait_for_load_state("networkidle")
    caption(
        page,
        "What arrived by email",
        "Booking confirmation, verification code, ticket receipt (a test inbox for the demo)",
        5,
    )
    first = page.locator("a.message, .message-list a, [href*='/view/']").first
    if first.count():
        first.click()
        beat(page, 4)


def sign_in(page: Page, cfg: dict, user: str, pw_key: str) -> None:
    page.goto(cfg["DASHBOARD_URL"])
    page.get_by_role("button", name=re.compile("Sign in")).wait_for()
    beat(page, 1)
    page.get_by_role("button", name=re.compile("Sign in")).click()
    page.locator("#username").wait_for()
    type_into(page.locator("#username"), user)
    page.locator("#password").fill(cfg[pw_key])
    beat(page, 0.8)
    page.locator("#kc-login").click()
    page.get_by_role("button", name="Sign out").wait_for(timeout=30000)
    beat(page, 1)


def sign_out(page: Page) -> None:
    page.get_by_role("button", name="Sign out").click()
    page.get_by_role("button", name=re.compile("Sign in")).wait_for(timeout=30000)


def nav(page: Page, label: str) -> None:
    link = page.get_by_role("link", name=re.compile(rf"^{re.escape(label)}"))
    if not link.first.is_visible():  # narrow window: open the menu first
        page.get_by_role("button", name="Menu").click()
    link.first.click()
    page.wait_for_load_state("networkidle")
    beat(page, 1)


def scene_consultant(page: Page, cfg: dict) -> None:
    page.goto(cfg["DASHBOARD_URL"])
    caption(
        page, "The staff dashboard", "Sign-in through the firm's own SSO (Keycloak)"
    )
    sign_in(page, cfg, "consultant.dev", "DEMO_CONSULTANT_PASSWORD")
    caption(page, "Signed in as a consultant", "Each role sees only what it needs", 3.5)
    nav(page, "Handovers")
    caption(
        page,
        "Conversations the assistant handed over",
        "Already posted to Slack and emailed; the client was told when to expect a reply",
        4,
    )
    take = page.get_by_role("button", name=re.compile("I'll take it"))
    if take.count():
        take.first.click()
        caption(page, "Taken", "Nobody else picks up the same person", 3)
    nav(page, "Bookings")
    caption(
        page,
        "Discovery calls the assistant booked",
        "Including the one from a minute ago",
        4,
    )
    nav(page, "Knowledge assistant")
    caption(
        page,
        "The private knowledge assistant",
        "Answers from internal documents this person may see, on a local model",
    )
    box = page.get_by_placeholder("Ask a question or ask for a draft…")
    type_into(box, "What's the cutover plan for Northbridge's mailbox migration?")
    page.get_by_role("button", name=re.compile(r"Ask$")).click()
    caption(
        page,
        "Running on this machine",
        "Confidential documents never go to a cloud model (about a minute on a CPU)",
        2,
    )
    page.get_by_role("button", name=re.compile(r"Ask$")).wait_for(timeout=300000)
    page.locator("ol.sources").last.scroll_into_view_if_needed()
    caption(
        page,
        "Every answer cites its sources",
        "Missing figures show as [TO CONFIRM]",
        8,
    )
    sign_out(page)


def scene_admin(page: Page, cfg: dict) -> None:
    sign_in(page, cfg, "admin.dev", "DEMO_ADMIN_PASSWORD")
    caption(
        page,
        "Signed in as an admin",
        "Live numbers: conversations, leads, calls, handovers",
        5,
    )
    nav(page, "Conversations")
    caption(
        page,
        "Every chat, newest first",
        "Opening a transcript is recorded in the audit log",
    )
    rows = page.locator("tr.clickable")
    if rows.count():
        rows.first.click()
        page.wait_for_load_state("networkidle")
        smooth_scroll(page, 900)
        beat(page, 3)
    nav(page, "Leads")
    caption(page, "Leads qualified by fixed rules", "Not by the model's opinion", 4)
    nav(page, "Tickets")
    caption(page, "Client tickets", "The urgent VPN ticket is at the top", 4)
    nav(page, "Website content")
    caption(
        page,
        "Website content, editable without a developer",
        "The only source the website assistant answers from",
    )
    q = page.locator("#try-q")
    if q.count():
        q.scroll_into_view_if_needed()
        type_into(q, "Do you work with schools?")
        page.get_by_role("button", name=re.compile("Search$")).click()
        beat(page, 3)
        caption(
            page, "Questions it couldn't answer become the content to-do list", "", 3
        )
    nav(page, "Settings")
    caption(
        page,
        "Rules as forms, not code",
        "Qualification, availability, handover routing, office hours: draft, check, publish",
        5,
    )
    nav(page, "Client access")
    caption(
        page,
        "Who can see which client's documents",
        "Applied before the search, not after",
        4,
    )
    nav(page, "Privacy requests")
    caption(page, "UK GDPR requests", "Find, export or erase a person's data by email")
    email = page.locator("#subject")
    type_into(email, VISITOR["email"])
    page.get_by_role("button", name=re.compile("Look up")).click()
    beat(page, 2)
    smooth_scroll(page, 500, 6)
    caption(
        page,
        "Everything held about one person, nobody else",
        "Export downloads it as JSON",
        4,
    )
    nav(page, "Audit log")
    caption(
        page,
        "Who looked at or changed what",
        "Including the lookup we just did; nobody can edit it",
        5,
    )
    sign_out(page)


def scene_outro(page: Page, cfg: dict) -> None:
    card(
        page,
        "Built on a free stack",
        [
            "FastAPI, Postgres + pgvector, Keycloak, React, Preact",
            "Groq and Gemini free tiers for the public chat; Ollama locally for confidential work",
            "Outbox pattern: no duplicate bookings or messages, retries when a service is down",
            "256 automated tests, red-team evals, UK GDPR tools, ISO 27001 control map",
            f"Live demo: {cfg['PUBLIC_URL'].removeprefix('https://')}",
        ],
        hold=9,
    )


# ---------------------------------------------------------------- main


def main() -> None:
    global SPEED
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--speed", type=float, default=1.0, help="1 = normal, 0.8 = slower")
    ap.add_argument("--only", help=f"comma-separated scenes: {','.join(SCENES)}")
    ap.add_argument(
        "--wait", action="store_true", help="press Enter to start (for recording)"
    )
    ap.add_argument(
        "--headless", action="store_true", help="no window (for testing the script)"
    )
    args = ap.parse_args()
    SPEED = max(0.2, args.speed)
    scenes = args.only.split(",") if args.only else SCENES
    cfg = env_prod()
    for k in (
        "PUBLIC_URL",
        "DASHBOARD_URL",
        "DEMO_ADMIN_PASSWORD",
        "DEMO_CONSULTANT_PASSWORD",
    ):
        if not cfg.get(k):
            sys.exit(f".env.prod: {k} is missing")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=args.headless,
            args=["--start-maximized", "--disable-infobars"],
        )
        ctx = (
            browser.new_context(viewport={"width": 1440, "height": 900})
            if args.headless
            else browser.new_context(no_viewport=True)
        )
        page = ctx.new_page()
        page.set_default_timeout(60000)
        page.set_content("<body style='background:#0c0c0c'></body>")
        if args.wait:
            input("Start recording, then press Enter here to begin the walkthrough...")
        for name in scenes:
            print(f"[{name}]")
            try:
                globals()[f"scene_{name}"](page, cfg)
            except Exception as e:  # noqa: BLE001 - one flaky step shouldn't spoil the take
                print(f"  ! {name} stopped early: {e}")
        print("Done. Stop the recording.")
        beat(page, 3)
        browser.close()


if __name__ == "__main__":
    main()
