# Force test environment before any plong_elven imports.
# This ensures get_settings() sees test values, not .env secrets.
import os

os.environ["APP_ENV"] = "dev"

TEST_INTERNAL_KEY = "123"
os.environ["INTERNAL_API_KEY"] = TEST_INTERNAL_KEY

TEST_JWT_SECRET = "test-jwt-secret-at-least-forty-characters"
os.environ["JWT_SECRET"] = TEST_JWT_SECRET

"""Shared fixtures for the test suite."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from plong_elven.app.db.models import (
    Role,
    Task,
    TaskRespondent,
    User,
    UserStatusId,
    Zone,
)
from plong_elven.core.common.settings import get_settings


class FakeSessionFactory:
    """A callable that returns an async context manager yielding a fixed session.
    Used to test services that open their own session from a factory."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.opened = 0

    def __call__(self):
        factory = self

        @asynccontextmanager
        async def _ctx():
            factory.opened += 1
            yield factory.session

        return _ctx()


class FakePushSender:
    """A fake PushSender that records all calls for test assertions.

    Implements the PushSender protocol with a calls list that stores
    (tokens, title, body) tuples for each send() invocation.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, str]] = []

    async def send(self, tokens: list[str], title: str, body: str) -> None:
        """Record the call and return None."""
        self.calls.append((tokens, title, body))


@pytest.fixture
def fake_push_sender() -> FakePushSender:
    """Provide a FakePushSender instance for testing."""
    return FakePushSender()


class _EmptyScalars:
    def all(self):
        return []


class FakeSession:
    """Fake async session for task tests."""

    async def get(self, model, key):
        if model is Zone and key == "z1":
            return Zone(
                zone_id="z1",
                name="Main Lobby",
                polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                threshold=10,
                is_active_task=False,
            )
        return None

    async def scalars(self, stmt):
        return _EmptyScalars()


class _EmptyResult:
    """An execute() result with no rows."""

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return None

    def first(self):
        return None

    def all(self):
        return []

    def scalars(self):
        return _EmptyScalars()


class NullSession:
    """An async session that finds nothing and writes nothing (Task 2.11)."""

    async def get(self, model, key, **kw):
        return None

    async def scalar(self, stmt):
        return None

    async def scalars(self, stmt):
        return _EmptyScalars()

    async def execute(self, stmt, *args, **kw):
        return _EmptyResult()

    def add(self, obj) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def refresh(self, obj, *args, **kw) -> None:
        pass


async def fake_db() -> AsyncIterator[FakeSession]:
    yield FakeSession()


def fake_user(role_name: str = "floor_staff") -> User:
    role_ids = {"floor_staff": 1, "ops_manager": 2, "admin": 3}
    return User(
        user_id=uuid4(),
        role_id=role_ids.get(role_name, 1),
        role=Role(role_id=role_ids.get(role_name, 1), name=role_name),
        email="test@plong.test",
        password_hash="x",
        full_name="Test User",
        is_active=True,
        user_status_id=UserStatusId.IDLE,
        zone_id=None,
    )


@pytest.fixture
def internal_headers() -> dict[str, str]:
    """Headers with the test internal API key."""
    return {"X-Internal-Api-Key": TEST_INTERNAL_KEY}


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the settings cache before and after each test.

    This ensures monkeypatch.setenv changes take effect and tests don't
    leak state to each other through the lru_cache.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- real Postgres (DATABASE_URL); skips without one unless REQUIRE_PG=1 ---


@pytest_asyncio.fixture
async def factory():
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail("REQUIRE_PG=1 but DATABASE_URL is not set")
        pytest.skip("DATABASE_URL not set (no Postgres)")

    engine = create_async_engine(db_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as e:
        await engine.dispose()
        if os.environ.get("REQUIRE_PG") == "1":
            pytest.fail(f"REQUIRE_PG=1 but no Postgres reachable: {e}")
        pytest.skip(f"no Postgres reachable: {e}")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def pg_zone(zone_id: str) -> Zone:
    return Zone(
        zone_id=zone_id, name="Race", polygon=[[0, 0], [1, 0], [1, 1]], threshold=10
    )


async def pg_cleanup(factory, zone_id: str, user_ids=()) -> None:
    """Delete a test zone with its tasks and respondents (its zone_logs go by
    ON DELETE CASCADE), plus the given users."""
    async with factory() as s:
        task_ids = select(Task.task_id).where(Task.zone_id == zone_id)
        await s.execute(
            delete(TaskRespondent).where(TaskRespondent.task_id.in_(task_ids))
        )
        await s.execute(delete(Task).where(Task.zone_id == zone_id))
        if user_ids:
            await s.execute(delete(User).where(User.user_id.in_(user_ids)))
        await s.execute(delete(Zone).where(Zone.zone_id == zone_id))
        await s.commit()
