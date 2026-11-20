"""Phase 8: production settings (separate env file, real SMTP, local JWKS fetch)."""

import os
import subprocess
import sys
from email.message import EmailMessage

from assistant.settings import get_settings


def test_prod_env_file_replaces_dev_env(tmp_path):
    # With ASSISTANT_ENV_FILE set, ONLY that file is read: dev's real Slack webhook in .env
    # must not reach the public demo.
    env_file = tmp_path / ".env.prod"
    env_file.write_text("DASHBOARD_URL=https://demo.example:8443\nAPP_ENV=dev\n")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("SLACK_", "JIRA_", "DASHBOARD_URL"))
    }
    env["ASSISTANT_ENV_FILE"] = str(env_file)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from assistant.settings import get_settings as g; s = g();"
            "print(s.dashboard_url); print(repr(s.slack_notify_webhook_url))",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,  # no stray .env in the working directory either
    ).stdout.split()
    assert out == ["https://demo.example:8443", "''"]


class _FakeSMTP:
    calls: list = []

    def __init__(self, host, port, timeout):
        _FakeSMTP.calls.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):
        _FakeSMTP.calls.append(("starttls",))

    def login(self, user, password):
        _FakeSMTP.calls.append(("login", user, password))

    def send_message(self, msg):
        _FakeSMTP.calls.append(("send", msg["To"]))


def _send(monkeypatch, **settings):
    from assistant.outbox import handlers

    s = get_settings()
    for k, v in settings.items():
        monkeypatch.setattr(s, k, v)
    monkeypatch.setattr(handlers.smtplib, "SMTP", _FakeSMTP)
    _FakeSMTP.calls = []
    msg = EmailMessage()
    msg["To"] = "a@b.example"
    handlers._send_smtp(msg)
    return _FakeSMTP.calls


def test_smtp_signs_in_over_starttls_when_configured(monkeypatch):
    calls = _send(
        monkeypatch,
        smtp_host="smtp.example",
        smtp_port=587,
        smtp_starttls=True,
        smtp_username="bot@example",
        smtp_password="pw",
    )
    assert calls == [
        ("connect", "smtp.example", 587),
        ("starttls",),
        ("login", "bot@example", "pw"),
        ("send", "a@b.example"),
    ]


def test_smtp_plain_for_mailpit(monkeypatch):
    calls = _send(monkeypatch, smtp_host="localhost", smtp_port=1025)
    assert calls == [("connect", "localhost", 1025), ("send", "a@b.example")]


def test_jwks_can_be_fetched_locally():
    from assistant.auth import _jwk_client

    issuer = "https://demo.example/auth/realms/assistant"
    assert _jwk_client(issuer).uri == f"{issuer}/protocol/openid-connect/certs"
    local = "http://127.0.0.1:8180/auth/realms/assistant/protocol/openid-connect/certs"
    assert _jwk_client(issuer, local).uri == local
