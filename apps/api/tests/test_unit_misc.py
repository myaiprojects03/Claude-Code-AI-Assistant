from assistant.models import Message
from assistant.orchestrator import trim_history
from assistant.outbox.worker import backoff_seconds


def test_backoff_grows_and_caps():
    assert 4 <= backoff_seconds(1) <= 6
    assert 8 <= backoff_seconds(2) <= 12
    assert backoff_seconds(30) <= 3600 * 1.2


def test_history_window_never_starts_mid_tool_exchange():
    msgs = [
        Message(role="tool"),
        Message(role="assistant"),
        Message(role="user"),
        Message(role="assistant"),
    ]
    assert [m.role for m in trim_history(msgs)] == ["user", "assistant"]
    assert trim_history([Message(role="tool")]) == []
