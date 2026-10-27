from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi.testclient import TestClient

from plong_elven.api.auth import get_current_user
from plong_elven.api.detections import latest_counts
from plong_elven.api.main import app
from plong_elven.db.models import Role, User, Zone
from plong_elven.db.session import get_db


class _EmptyScalars:
    def all(self):
        return []


class FakeSession:
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


async def fake_db() -> AsyncIterator[FakeSession]:
    yield FakeSession()


def fake_user(role_name: str = "floor_staff") -> User:
    return User(
        id=uuid4(),
        role_id=1,
        role=Role(id=1, name=role_name),
        email="test@plong.test",
        password_hash="x",
        full_name="Test User",
        is_active=True,
        status="idle",
    )


async def fake_current_user() -> User:
    return fake_user()


def test_dispatch_zone_uses_latest_detection_count() -> None:
    latest_counts.clear()
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    try:
        client = TestClient(app)
        detection = client.post(
            "/detections", json={"camera_id": "cam1", "counts": {"z1": 7}}
        )
        response = client.get("/api/v1/dispatch/zones/z1")
    finally:
        app.dependency_overrides.clear()
        latest_counts.clear()

    assert detection.status_code == 200
    assert response.status_code == 200
    assert response.json()["currentCount"] == 7
    assert response.json()["status"] == "warning"


def test_dispatch_zone_returns_zero_before_first_detection() -> None:
    latest_counts.clear()
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    try:
        response = TestClient(app).get("/api/v1/dispatch/zones/z1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["currentCount"] == 0
    assert response.json()["status"] == "safe"


def test_dispatch_zone_not_found_uses_contract_error_shape() -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    try:
        response = TestClient(app).get("/api/v1/dispatch/zones/z9")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_dispatch_zone_requires_auth() -> None:
    app.dependency_overrides[get_db] = fake_db
    try:
        response = TestClient(app).get("/api/v1/dispatch/zones/z1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_create_broadcast_requires_manager_role() -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("floor_staff")
    try:
        response = TestClient(app).post(
            "/api/v1/dispatch/broadcasts", json={"zoneId": "z1", "staffNeeded": 3}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "wrong_role"
