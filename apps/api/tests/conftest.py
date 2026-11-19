import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from assistant import alerts, config_store
from assistant.models import Base

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL not set (start Postgres: see README)"
)


@pytest.fixture(autouse=True)
def _reset_caches():
    from assistant import ratelimit

    alerts._reset_for_tests()
    config_store.clear_cache()
    ratelimit.reset()  # per-IP counters are in memory: one test's chats mustn't limit the next
    yield


# Real integrations are configured in .env for the running app. Tests must never reach
# them (Phase 7 finding: a worker test posted to the real Slack webhook on every run).
OFFLINE = {
    "slack_notify_webhook_url": "",
    "slack_alert_webhook_url": "",
    "ticketing_provider": "local",
    "jira_base_url": "",
    "jira_email": "",
    "jira_api_token": "",
    "crm_provider": "local",
    "hubspot_token": "",
    "calendar_provider": "local",
    "google_service_account_file": "",
    "smtp_host": "smtp.invalid",  # nothing listens: email handlers must be faked in tests
}


@pytest.fixture(autouse=True)
def _no_real_integrations(monkeypatch):
    from assistant.settings import get_settings

    s = get_settings()
    for key, value in OFFLINE.items():
        monkeypatch.setattr(s, key, value)
    yield


@pytest.fixture(scope="session")
async def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def sm(engine):
    """Sessionmaker on a clean database.

    DELETE (children first) rather than TRUNCATE: tests create a handful of rows, and
    TRUNCATE's file rewrites are slow on WSL's virtual disk (~5s/test vs milliseconds).
    """
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    return async_sessionmaker(engine, expire_on_commit=False)
