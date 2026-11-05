"""Tests for the AuthService layer."""

from uuid import uuid4

import jwt
import pytest

from plong_elven.common.settings import get_settings
from plong_elven.db.models import Role, User
from plong_elven.services import auth_service
from plong_elven.services.auth_service import AuthService, create_access_token
from plong_elven.services.errors import AppError

SECRET = "s" * 40


@pytest.fixture
def strong_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _user() -> User:
    return User(
        user_id=uuid4(),
        role_id=1,
        role=Role(role_id=1, name="floor_staff"),
        email="a@b.io",
        password_hash="x",
        full_name="A",
        is_active=True,
        user_status_id=1,
    )


class _UserSession:
    def __init__(self, user):
        self.user = user

    async def execute(self, stmt):
        user = self.user
        return type("R", (), {"scalar_one_or_none": lambda self: user})()


@pytest.mark.asyncio
async def test_access_token_round_trip(strong_secret) -> None:
    user = _user()
    token = create_access_token(user)
    assert await AuthService(_UserSession(user)).user_from_access_token(token) is user


@pytest.mark.asyncio
async def test_non_access_token_type_is_rejected(strong_secret) -> None:
    token = jwt.encode(
        {"sub": str(uuid4()), "type": "refresh"}, SECRET, algorithm="HS256"
    )
    with pytest.raises(AppError) as exc:
        await AuthService(_UserSession(_user())).user_from_access_token(token)
    assert (exc.value.status_code, exc.value.message) == (401, "Invalid access token")


def test_weak_secret_is_503(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "short")
    get_settings.cache_clear()
    try:
        with pytest.raises(AppError) as exc:
            auth_service.jwt_secret()
        assert exc.value.status_code == 503
    finally:
        get_settings.cache_clear()
