"""WS /api/v1/crowd/live authentication (Task 2.6)."""

from contextlib import asynccontextmanager

import pytest
from conftest import FakeSessionFactory, fake_user
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from plong_elven.api.deps import get_session_factory
from plong_elven.api.main import app
from plong_elven.services.auth_service import AuthService, create_access_token


class _UserSession:
    def __init__(self, user):
        self.user = user

    async def get(self, model, key, **kw):
        return self.user if key == self.user.id else None

    async def execute(self, stmt):
        user = self.user
        return type("R", (), {"scalar_one_or_none": lambda self: user})()


@pytest.fixture
def admin():
    return fake_user("admin")


@pytest.fixture
def factory(admin):
    f = FakeSessionFactory(_UserSession(admin))
    app.dependency_overrides[get_session_factory] = lambda: f
    yield f
    app.dependency_overrides.clear()


def _assert_rejected(path: str, **kwargs) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as exc,
        TestClient(app).websocket_connect(path, **kwargs) as ws,
    ):
        ws.receive_text()
    assert exc.value.code == 1008


def test_rejects_without_credentials(factory) -> None:
    _assert_rejected("/api/v1/crowd/live")


def test_rejects_stream_scoped_ticket(factory, admin) -> None:
    ticket = AuthService(factory.session).mint_ticket(admin, "stream:cam1").ticket
    _assert_rejected(f"/api/v1/crowd/live?ticket={ticket}")


def test_rejects_access_token_as_ticket(factory, admin) -> None:
    _assert_rejected(f"/api/v1/crowd/live?ticket={create_access_token(admin)}")


def test_rejects_garbage_bearer(factory) -> None:
    _assert_rejected("/api/v1/crowd/live", headers={"Authorization": "Bearer nope"})


def test_rejects_ticket_as_bearer(factory, admin) -> None:
    ticket = AuthService(factory.session).mint_ticket(admin, "crowd:live").ticket
    _assert_rejected(
        "/api/v1/crowd/live", headers={"Authorization": f"Bearer {ticket}"}
    )


def test_accepts_crowd_ticket(factory, admin) -> None:
    ticket = AuthService(factory.session).mint_ticket(admin, "crowd:live").ticket
    with TestClient(app).websocket_connect(f"/api/v1/crowd/live?ticket={ticket}"):
        pass
    assert factory.opened == 1


def test_auth_session_is_closed_before_accept(admin) -> None:
    """P5: an open socket must not hold a pool connection."""
    events: list[str] = []

    @asynccontextmanager
    async def factory():
        events.append("open")
        yield _UserSession(admin)
        events.append("close")

    app.dependency_overrides[get_session_factory] = lambda: factory
    try:
        with TestClient(app).websocket_connect(
            "/api/v1/crowd/live",
            headers={"Authorization": f"Bearer {create_access_token(admin)}"},
        ):
            assert events == ["open", "close"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("role", ["floor_staff", "ops_manager", "admin"])
def test_accepts_bearer_header(role) -> None:
    user = fake_user(role)
    app.dependency_overrides[get_session_factory] = lambda: FakeSessionFactory(
        _UserSession(user)
    )
    try:
        with TestClient(app).websocket_connect(
            "/api/v1/crowd/live",
            headers={"Authorization": f"Bearer {create_access_token(user)}"},
        ):
            pass
    finally:
        app.dependency_overrides.clear()


def test_old_ws_counts_is_gone() -> None:
    with (
        pytest.raises(WebSocketDisconnect),
        TestClient(app).websocket_connect("/ws/counts") as ws,
    ):
        ws.receive_text()
