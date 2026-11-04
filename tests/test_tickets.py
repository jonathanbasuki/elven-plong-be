# tests/test_tickets.py
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from conftest import TEST_JWT_SECRET, fake_db, fake_user
from fastapi.testclient import TestClient

from plong_elven.api.deps import get_current_user
from plong_elven.api.main import app
from plong_elven.db.session import get_db
from plong_elven.services import auth_service
from plong_elven.services.auth_service import (
    AuthService,
    create_access_token,
    new_refresh_token,
)
from plong_elven.services.errors import AppError


class _UserById:
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


@pytest.mark.asyncio
async def test_ticket_round_trip(admin) -> None:
    svc = AuthService(_UserById(admin))
    ticket = svc.mint_ticket(admin, "stream:cam1")
    assert ticket.expiresIn == 60
    assert await svc.verify_ticket(ticket.ticket, "stream:cam1") is admin


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minted,expected",
    [
        ("stream:cam1", "crowd:live"),  # wrong scope
        ("stream:cam1", "stream:cam2"),  # wrong camera
    ],
)
async def test_ticket_scope_mismatch_is_401(admin, minted, expected) -> None:
    svc = AuthService(_UserById(admin))
    with pytest.raises(AppError) as exc:
        await svc.verify_ticket(svc.mint_ticket(admin, minted).ticket, expected)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_ticket_is_401(admin, monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "TICKET_SECONDS", -1)
    svc = AuthService(_UserById(admin))
    with pytest.raises(AppError):
        await svc.verify_ticket(
            svc.mint_ticket(admin, "crowd:live").ticket, "crowd:live"
        )


@pytest.mark.asyncio
async def test_access_token_is_not_a_ticket(admin) -> None:
    with pytest.raises(AppError):
        await AuthService(_UserById(admin)).verify_ticket(
            create_access_token(admin), "crowd:live"
        )


@pytest.mark.asyncio
async def test_ticket_is_not_an_access_token(admin) -> None:
    svc = AuthService(_UserById(admin))
    with pytest.raises(AppError):
        await svc.user_from_access_token(svc.mint_ticket(admin, "crowd:live").ticket)


@pytest.mark.asyncio
async def test_inactive_user_ticket_is_401(admin) -> None:
    admin.is_active = False
    svc = AuthService(_UserById(admin))
    # Mint while active, then deactivate and verify
    admin.is_active = True
    ticket = svc.mint_ticket(admin, "stream:cam1")
    admin.is_active = False
    with pytest.raises(AppError) as exc:
        await svc.verify_ticket(ticket.ticket, "stream:cam1")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_user_ticket_is_401() -> None:
    ops_user = fake_user("ops_manager")
    svc = AuthService(_UserById(ops_user))
    # Mint ticket for ops_manager then try to verify (P4: verify requires admin role)
    ticket = svc.mint_ticket(ops_user, "stream:cam1")
    with pytest.raises(AppError) as exc:
        await svc.verify_ticket(ticket.ticket, "stream:cam1")
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "role,status", [("admin", 200), ("ops_manager", 403), ("floor_staff", 403)]
)
def test_only_admin_mints(role, status) -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user(role)
    try:
        response = TestClient(app).post("/dev/tickets", json={"scope": "stream:cam1"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status


@pytest.mark.parametrize("scope", ["stream:", "stream:a/b", "crowd:other", "admin"])
def test_bad_scope_is_422(scope) -> None:
    app.dependency_overrides[get_current_user] = lambda: fake_user("admin")
    try:
        assert (
            TestClient(app).post("/dev/tickets", json={"scope": scope}).status_code
            == 422
        )
    finally:
        app.dependency_overrides.clear()


# --- Review hardening: hostile tokens, error shape, transport ---


def _claims(user, **over):
    now = datetime.now(UTC)
    claims = {
        "sub": str(user.id),
        "type": "ticket",
        "scope": "crowd:live",
        "iat": now,
        "exp": now + timedelta(seconds=60),
    }
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


async def _assert_invalid_ticket(svc, token, scope="crowd:live") -> None:
    with pytest.raises(AppError) as exc:
        await svc.verify_ticket(token, scope)
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthenticated"
    assert exc.value.message == "Invalid ticket"  # fixed text: never echoes the token


@pytest.mark.asyncio
async def test_ticket_without_exp_is_401(admin) -> None:
    token = jwt.encode(_claims(admin, exp=None), TEST_JWT_SECRET, algorithm="HS256")
    await _assert_invalid_ticket(AuthService(_UserById(admin)), token)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["sub", "type", "scope"])
async def test_ticket_missing_claim_is_401(admin, missing) -> None:
    token = jwt.encode(
        _claims(admin, **{missing: None}), TEST_JWT_SECRET, algorithm="HS256"
    )
    await _assert_invalid_ticket(AuthService(_UserById(admin)), token)


@pytest.mark.asyncio
async def test_wrong_algorithm_ticket_is_401(admin) -> None:
    token = jwt.encode(_claims(admin), TEST_JWT_SECRET, algorithm="HS512")
    await _assert_invalid_ticket(AuthService(_UserById(admin)), token)


@pytest.mark.asyncio
async def test_alg_none_ticket_is_401(admin) -> None:
    token = jwt.encode(_claims(admin), None, algorithm="none")
    await _assert_invalid_ticket(AuthService(_UserById(admin)), token)


@pytest.mark.asyncio
async def test_wrong_secret_ticket_is_401(admin) -> None:
    token = jwt.encode(_claims(admin), "x" * 40, algorithm="HS256")
    await _assert_invalid_ticket(AuthService(_UserById(admin)), token)


@pytest.mark.asyncio
async def test_tampered_ticket_is_401(admin) -> None:
    svc = AuthService(_UserById(admin))
    good = svc.mint_ticket(admin, "stream:cam1").ticket
    # Re-sign nothing: swap the payload for one with another scope.
    header, _, sig = good.split(".")
    forged_payload = jwt.encode(
        _claims(admin, scope="crowd:live"), "x" * 40, algorithm="HS256"
    ).split(".")[1]
    await _assert_invalid_ticket(svc, f"{header}.{forged_payload}.{sig}")


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
async def test_garbage_ticket_is_401(admin, token) -> None:
    await _assert_invalid_ticket(AuthService(_UserById(admin)), token)


@pytest.mark.asyncio
async def test_refresh_token_is_not_a_ticket(admin) -> None:
    raw, _ = new_refresh_token()
    await _assert_invalid_ticket(AuthService(_UserById(admin)), raw)


@pytest.mark.asyncio
async def test_expired_ticket_error_shape(admin, monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "TICKET_SECONDS", -1)
    svc = AuthService(_UserById(admin))
    await _assert_invalid_ticket(svc, svc.mint_ticket(admin, "crowd:live").ticket)


@pytest.mark.asyncio
async def test_deleted_user_ticket_is_401(admin) -> None:
    other = fake_user("admin")
    svc = AuthService(_UserById(other))
    await _assert_invalid_ticket(svc, svc.mint_ticket(admin, "crowd:live").ticket)


def test_mint_response_body() -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("admin")
    try:
        response = TestClient(app).post("/dev/tickets", json={"scope": "crowd:live"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"ticket", "expiresIn"}
    assert body["expiresIn"] == 60
    claims = jwt.decode(body["ticket"], TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["type"] == "ticket"
    assert claims["scope"] == "crowd:live"
    assert claims["exp"] - claims["iat"] == 60


@pytest.mark.parametrize("role", ["ops_manager", "floor_staff"])
def test_non_admin_mint_error_shape(role) -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user(role)
    try:
        response = TestClient(app).post("/dev/tickets", json={"scope": "crowd:live"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "wrong_role"


@pytest.mark.parametrize(
    "scope", ["stream:" + "a" * 65, "stream:cam1\n", "crowd:live ", "STREAM:cam1"]
)
def test_more_bad_scopes_are_422(scope) -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("admin")
    try:
        response = TestClient(app).post("/dev/tickets", json={"scope": scope})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_ticket_as_bearer_header_is_401() -> None:
    admin = fake_user("admin")
    ticket = AuthService(_UserById(admin)).mint_ticket(admin, "crowd:live").ticket
    app.dependency_overrides[get_db] = lambda: _UserById(admin)
    try:
        response = TestClient(app).post(
            "/dev/tickets",
            json={"scope": "crowd:live"},
            headers={"Authorization": f"Bearer {ticket}"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert ticket not in response.text


def test_ticket_in_body_or_query_does_not_authenticate_mint() -> None:
    admin = fake_user("admin")
    ticket = AuthService(_UserById(admin)).mint_ticket(admin, "crowd:live").ticket
    app.dependency_overrides[get_db] = lambda: _UserById(admin)
    try:
        response = TestClient(app).post(
            f"/dev/tickets?ticket={ticket}",
            json={"scope": "crowd:live", "ticket": ticket},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401
    assert ticket not in response.text
