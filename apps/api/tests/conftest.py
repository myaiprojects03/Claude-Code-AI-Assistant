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
    alerts._reset_for_tests()
    config_store.clear_cache()
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
