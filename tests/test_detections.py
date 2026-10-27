from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from plong_elven.api.main import app
from plong_elven.db.session import get_db


class _FakeSession:
    """No zones configured: the status-change notifier looks up "z1", finds
    nothing, and returns early — keeps these tests off the real database."""

    async def get(self, model, key):
        return None


async def _fake_db() -> AsyncIterator[_FakeSession]:
    yield _FakeSession()


def test_post_detection_without_connected_clients() -> None:
    app.dependency_overrides[get_db] = _fake_db
    try:
        response = TestClient(app).post(
            "/detections", json={"camera_id": "cam1", "counts": {"z1": 2}}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ws_counts_receives_broadcast_detection() -> None:
    app.dependency_overrides[get_db] = _fake_db
    try:
        client = TestClient(app)
        with client.websocket_connect("/ws/counts") as ws:
            response = client.post(
                "/detections", json={"camera_id": "cam1", "counts": {"z1": 3}}
            )
            assert response.status_code == 200
            assert ws.receive_json() == {"camera_id": "cam1", "counts": {"z1": 3}}
    finally:
        app.dependency_overrides.clear()


def test_post_detection_rejects_wrong_internal_api_key(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    response = TestClient(app).post(
        "/detections",
        json={"camera_id": "cam1", "counts": {"z1": 1}},
        headers={"X-Internal-Api-Key": "wrong"},
    )

    assert response.status_code == 401
