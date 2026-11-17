"""Phase 3 unit tests: handover triggers and promises, reminder timing, Jira adapter."""

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from assistant.config_store import HANDOVER_RULES, OFFICE_HOURS
from assistant.handover import detect_trigger, follow_up_promise
from assistant.integrations.ticketing import JiraTickets, adf
from assistant.outbox.errors import PermanentError
from assistant.reminders import latest_allowed

MSG_HOURS = {
    "start": "09:00",
    "end": "19:00",
    "days": [0, 1, 2, 3, 4],
    "holidays_country": "GB",
    "holidays_subdiv": "ENG",
}


# ---------- Triggers ----------


@pytest.mark.parametrize(
    "text",
    [
        "Help, we've been hit by ransomware",
        "I think our Microsoft 365 account was compromised",
        "Someone clicked a phishing link and now files are encrypted",
        "We had a data leak this morning",
    ],
)
def test_security_trigger(text):
    assert detect_trigger(text, HANDOVER_RULES) == "security_incident"


@pytest.mark.parametrize(
    "text",
    [
        "I want to complain about a breach of contract",
        "Do you run phishing awareness training?",
        "We'd like to migrate to Azure",
        "Our firewall is unbreachable, right?",  # word boundary: no partial match
    ],
)
def test_no_false_security_trigger(text):
    assert detect_trigger(text, HANDOVER_RULES) is None


def test_follow_up_promise_depends_on_office_hours():
    tue_10 = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
    fri_20 = datetime(2026, 11, 20, 20, 0, tzinfo=UTC)
    assert "shortly" in follow_up_promise(OFFICE_HOURS, "normal", tue_10)
    late = follow_up_promise(OFFICE_HOURS, "normal", fri_20)
    assert "closed" in late and "Monday" in late
    assert "security team" in follow_up_promise(OFFICE_HOURS, "p1", fri_20)


# ---------- Reminder timing ----------


def test_reminder_inside_hours_is_unchanged():
    at = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)  # Tue 10:00 London
    assert latest_allowed(at, MSG_HOURS, "Europe/London") == at


def test_early_morning_reminder_moves_to_previous_evening():
    at = datetime(2026, 11, 18, 7, 0, tzinfo=UTC)  # Wed 07:00 -> Tue 18:55
    assert latest_allowed(at, MSG_HOURS, "Europe/London") == datetime(
        2026, 11, 17, 18, 55, tzinfo=UTC
    )


def test_weekend_reminder_moves_to_friday():
    at = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)  # Sunday -> Friday 18:55
    assert latest_allowed(at, MSG_HOURS, "Europe/London") == datetime(
        2026, 11, 20, 18, 55, tzinfo=UTC
    )


def test_reminder_uses_recipient_timezone():
    # 14:00 UTC is 09:00 in New York: allowed there.
    at = datetime(2026, 11, 17, 14, 0, tzinfo=UTC)
    assert latest_allowed(at, MSG_HOURS, "America/New_York") == at
    # 13:00 UTC is 08:00 in New York: moved to the previous evening there (Mon 18:55 EST).
    early = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)
    assert latest_allowed(early, MSG_HOURS, "America/New_York") == datetime(
        2026, 11, 16, 23, 55, tzinfo=UTC
    )


# ---------- Jira adapter ----------

BASE = "https://acme.atlassian.test"


def jira():
    return JiraTickets(BASE, "bot@example.com", "tok", "SUP", httpx.AsyncClient(base_url=BASE))


def test_adf_paragraphs():
    doc = adf("line one\n\nline three")
    assert [len(p["content"]) for p in doc["content"]] == [1, 0, 1]


@respx.mock
async def test_jira_create_is_idempotent_by_label():
    search = respx.post(f"{BASE}/rest/api/3/search/jql").respond(json={"issues": []})
    create = respx.post(f"{BASE}/rest/api/3/issue").respond(json={"key": "SUP-7"})
    key = await jira().create(
        ref="T-1001", summary="VPN down", description="d", priority="urgent", company="Halcyon"
    )
    assert key == "SUP-7"
    fields = json.loads(create.calls.last.request.content)["fields"]
    assert fields["labels"] == ["assistant-t-1001"] and fields["priority"]["name"] == "Highest"
    assert respx.calls.last.request.headers["authorization"].startswith("Basic ")
    # Retry after a crash: the label search finds the issue, nothing new is created.
    search.respond(json={"issues": [{"key": "SUP-7"}]})
    assert (
        await jira().create(
            ref="T-1001", summary="VPN down", description="d", priority="urgent", company="Halcyon"
        )
        == "SUP-7"
    )
    assert create.call_count == 1


@respx.mock
async def test_jira_status_comment_and_errors():
    respx.get(f"{BASE}/rest/api/3/issue/SUP-7").respond(
        json={"fields": {"status": {"name": "In Progress"}}}
    )
    assert await jira().status("SUP-7") == "In Progress"
    respx.get(f"{BASE}/rest/api/3/issue/SUP-9").respond(status_code=404)
    assert await jira().status("SUP-9") is None
    comment = respx.post(f"{BASE}/rest/api/3/issue/SUP-7/comment").respond(json={})
    await jira().comment("SUP-7", "More info")
    assert comment.called
    respx.post(f"{BASE}/rest/api/3/search/jql").respond(status_code=401)
    with pytest.raises(PermanentError):
        await jira().create(ref="T-1", summary="s", description="d", priority="low", company="c")


# ---------- Public holidays ----------

from datetime import date  # noqa: E402

from assistant.booking import candidate_starts  # noqa: E402
from assistant.config_store import DEFAULTS  # noqa: E402
from assistant.workdays import holiday_name, is_working_day  # noqa: E402


def test_uk_bank_holidays_including_observed_days():
    assert holiday_name(date(2026, 12, 25), MSG_HOURS) == "Christmas Day"
    assert not is_working_day(date(2026, 12, 28), MSG_HOURS)  # Boxing Day (observed), a Monday
    assert is_working_day(date(2026, 12, 29), MSG_HOURS)
    assert is_working_day(date(2026, 12, 25), {"days": [4]})  # no calendar configured


def test_client_reminder_never_lands_on_a_bank_holiday():
    # Call on Tue 29 Dec 10:00: 24h before is Mon 28 Dec (observed holiday), so the reminder
    # goes on Thu 24 Dec at 18:55, skipping Christmas Day (Fri) and the weekend.
    at = datetime(2026, 12, 28, 10, 0, tzinfo=UTC)
    assert latest_allowed(at, MSG_HOURS, "Europe/London") == datetime(
        2026, 12, 24, 18, 55, tzinfo=UTC
    )


def test_no_discovery_slots_on_bank_holidays():
    rules = {**DEFAULTS["booking.rules"], "horizon_days": 14}
    starts = candidate_starts(rules, datetime(2026, 12, 21, 9, 0, tzinfo=UTC))
    days = {s.date() for s in starts}
    assert date(2026, 12, 25) not in days and date(2026, 12, 28) not in days
    assert date(2026, 12, 24) in days and date(2026, 12, 29) in days


def test_follow_up_promise_handles_early_morning_and_holidays():
    tue_7am = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)
    assert "opens at 09:00" in follow_up_promise(OFFICE_HOURS, "normal", tue_7am)
    xmas = datetime(2026, 12, 25, 11, 0, tzinfo=UTC)
    msg = follow_up_promise(OFFICE_HOURS, "normal", xmas)
    assert "Christmas Day" in msg and "Tuesday 29 December" in msg
