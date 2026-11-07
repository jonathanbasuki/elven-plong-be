# tests/test_auth_routes.py
"""Auth camelCase and /me routes (Task 2.5)."""

from collections.abc import AsyncIterator

from conftest import FakeSession, fake_db, fake_user
from fastapi.testclient import TestClient

from plong_elven.app.api.deps import get_current_user
from plong_elven.app.api.main import app
from plong_elven.app.db.session import get_db
from plong_elven.app.schemas.auth import TokenResponse


class _CommitSession(FakeSession):
    """FakeSession that also accepts commit(), which set_push_token needs."""

    async def commit(self) -> None:
        return None


async def _commit_db() -> AsyncIterator[_CommitSession]:
    yield _CommitSession()


def test_me_returns_camel_case_user() -> None:
    user = fake_user("ops_manager")
    # conftest's "test@plong.test" uses a reserved TLD that EmailStr rejects.
    user.email = "ops@plongelven.io"
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).get("/api/v1/profile")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "email", "fullName", "role", "isActive"}
    assert body["role"] == "ops_manager"


def test_put_push_token_stores_token() -> None:
    user = fake_user("floor_staff")
    app.dependency_overrides[get_db] = _commit_db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        response = TestClient(app).put(
            "/api/v1/profile/push-token", json={"pushToken": "abc"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert user.push_token == "abc"


def test_old_auth_profile_route_is_gone() -> None:
    response = TestClient(app).get("/api/v1/auth/profile")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_refresh_rejects_snake_case_body() -> None:
    response = TestClient(app).post("/api/v1/auth/refresh", json={"refresh_token": "x"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_token_response_fields_are_camel_case() -> None:
    assert set(TokenResponse.model_fields) == {
        "accessToken",
        "refreshToken",
        "tokenType",
        "expiresIn",
        "user",
    }
