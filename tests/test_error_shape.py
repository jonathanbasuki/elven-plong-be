# tests/test_error_shape.py
"""Verify all errors follow the {"error": {"code", "message"}} shape (Task 2.2)."""

from conftest import fake_db, fake_user
from fastapi.testclient import TestClient

from plong_elven.api.deps import get_current_user
from plong_elven.api.main import app
from plong_elven.db.session import get_db

PROFILE = "/api/v1/profile"
BROADCAST_TASK = "/api/v1/tasks/broadcast"


def _error(response, status, code):
    assert response.status_code == status
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == code
    assert isinstance(response.json()["error"]["message"], str)


def test_401_shape_and_header() -> None:
    response = TestClient(app).get(PROFILE)
    _error(response, 401, "unauthenticated")
    assert response.headers["www-authenticate"] == "Bearer"


def test_403_shape() -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("floor_staff")
    try:
        response = TestClient(app).post(
            BROADCAST_TASK,
            json={"zoneId": "z1", "totalStaff": 1, "instruction": "x"},
        )
    finally:
        app.dependency_overrides.clear()
    _error(response, 403, "wrong_role")


def test_404_for_unknown_path() -> None:
    _error(TestClient(app).get("/no-such-path"), 404, "not_found")


def test_422_shape() -> None:
    _error(TestClient(app).post("/api/v1/auth/login", json={}), 422, "validation_error")
