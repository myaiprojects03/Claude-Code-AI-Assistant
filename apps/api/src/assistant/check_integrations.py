"""Check the Slack and Jira configuration in .env. Read-only unless you pass a flag.

    assistant-check-integrations                       # read-only checks
    assistant-check-integrations --send-slack-test     # also posts ONE test message
    assistant-check-integrations --create-jira-test    # also creates ONE test issue

Secrets are never printed.
"""

import argparse
import asyncio
import base64
import sys

import httpx

from assistant.settings import Settings, get_settings

OK, WARN, FAIL = "ok  ", "warn", "FAIL"


def line(status: str, what: str, detail: str = "") -> None:
    print(f"[{status}] {what}{(' - ' + detail) if detail else ''}")


async def check_slack(s: Settings, send: bool) -> bool:
    good = True
    for name, url in (
        ("SLACK_NOTIFY_WEBHOOK_URL", s.slack_notify_webhook_url),
        ("SLACK_ALERT_WEBHOOK_URL", s.slack_alert_webhook_url),
    ):
        if not url:
            line(WARN, name, "not set (messages are logged instead)")
            continue
        if not url.startswith("https://hooks.slack.com/services/"):
            line(FAIL, name, "doesn't look like a Slack incoming-webhook URL")
            good = False
            continue
        line(OK, name, "looks like an incoming-webhook URL")
    if send and s.slack_notify_webhook_url:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                s.slack_notify_webhook_url,
                json={
                    "text": ":white_check_mark: Test from the Acme assistant: Slack notifications "
                    "are connected."
                },
            )
        if r.status_code == 200:
            line(OK, "Slack test message", "posted to the notify channel")
        else:
            line(FAIL, "Slack test message", f"HTTP {r.status_code}: {r.text[:120]}")
            good = False
    return good


async def check_jira(s: Settings, create: bool) -> bool:
    if s.ticketing_provider != "jira":
        line(WARN, "TICKETING_PROVIDER", f"is '{s.ticketing_provider}', so Jira is not used")
    missing = [
        n
        for n, v in (
            ("JIRA_BASE_URL", s.jira_base_url),
            ("JIRA_EMAIL", s.jira_email),
            ("JIRA_API_TOKEN", s.jira_api_token),
        )
        if not v
    ]
    if missing:
        line(
            FAIL if s.ticketing_provider == "jira" else WARN,
            "Jira",
            f"missing {', '.join(missing)}",
        )
        return s.ticketing_provider != "jira"
    auth = base64.b64encode(f"{s.jira_email}:{s.jira_api_token}".encode()).decode()
    async with httpx.AsyncClient(
        base_url=s.jira_base_url.rstrip("/"),
        timeout=15,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    ) as c:
        r = await c.get("/rest/api/3/myself")
        if r.status_code != 200:
            line(
                FAIL,
                "Jira sign-in",
                f"HTTP {r.status_code}. Check JIRA_BASE_URL, JIRA_EMAIL and the API token",
            )
            return False
        line(OK, "Jira sign-in", f"as {r.json().get('displayName', 'unknown')}")

        r = await c.get(f"/rest/api/3/project/{s.jira_project_key}")
        if r.status_code != 200:
            projects = await c.get("/rest/api/3/project/search", params={"maxResults": 20})
            keys = (
                [p["key"] for p in projects.json().get("values", [])] if projects.is_success else []
            )
            line(
                FAIL,
                f"Jira project '{s.jira_project_key}'",
                f"not found. Projects you can see: {', '.join(keys) or 'none'}",
            )
            return False
        project = r.json()
        types = [t["name"] for t in project.get("issueTypes", []) if not t.get("subtask")]
        line(
            OK,
            f"Jira project '{s.jira_project_key}'",
            f"{project.get('name')} ({project.get('projectTypeKey')})",
        )
        if s.jira_issue_type not in types:
            line(
                FAIL,
                f"Jira issue type '{s.jira_issue_type}'",
                f"not in this project. Set JIRA_ISSUE_TYPE to one of: {', '.join(types)}",
            )
            return False
        line(OK, f"Jira issue type '{s.jira_issue_type}'", "exists")

    if create:
        from assistant.integrations.ticketing import JiraTickets

        jira = JiraTickets(
            s.jira_base_url,
            s.jira_email,
            s.jira_api_token,
            s.jira_project_key,
            issue_type=s.jira_issue_type,
        )
        key = await jira.create(
            ref="T-TEST",
            summary="Integration test from the assistant",
            description="Safe to delete.",
            priority="low",
            company="Acme (test)",
        )
        line(OK, "Jira test issue", f"created {key} (label assistant-t-test; safe to delete)")
    return True


async def run(args: argparse.Namespace) -> int:
    s = get_settings()
    print("Slack")
    slack_ok = await check_slack(s, args.send_slack_test)
    print("\nJira")
    jira_ok = await check_jira(s, args.create_jira_test)
    print("\nAll good." if slack_ok and jira_ok else "\nSome checks need attention (see above).")
    return 0 if slack_ok and jira_ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--send-slack-test", action="store_true")
    ap.add_argument("--create-jira-test", action="store_true")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
