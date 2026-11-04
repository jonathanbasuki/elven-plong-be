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

from plong_elven.common.settings import get_settings
from plong_elven.db.models import Role, User, Zone


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
    """Fake async session for dispatch/broadcast tests."""

    async def get(self, model, key):
        if model is Zone and key == "z1":
            return Zone(
                zone_id="z1",
                name="Main Lobby",
                map_image_url="https://example.com/main-lobby.png",
                polygon=[[0.0, 0.0], [1.0, 1.0]],
                warning_threshold=5,
                crowded_threshold=10,
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
        id=uuid4(),
        role_id=role_ids.get(role_name, 1),
        role=Role(id=role_ids.get(role_name, 1), name=role_name),
        email="test@plong.test",
        password_hash="x",
        full_name="Test User",
        is_active=True,
        status="idle",
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
