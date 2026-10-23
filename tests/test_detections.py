from fastapi.testclient import TestClient

from plong_elven.api.main import app


def test_post_detection_without_connected_clients() -> None:
    response = TestClient(app).post(
        "/detections", json={"camera_id": "cam1", "counts": {"z1": 2}}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ws_counts_receives_broadcast_detection() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws/counts") as ws:
        response = client.post(
            "/detections", json={"camera_id": "cam1", "counts": {"z1": 3}}
        )
        assert response.status_code == 200
        assert ws.receive_json() == {"camera_id": "cam1", "counts": {"z1": 3}}


def test_post_detection_rejects_wrong_internal_api_key(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    response = TestClient(app).post(
        "/detections",
        json={"camera_id": "cam1", "counts": {"z1": 1}},
        headers={"X-Internal-Api-Key": "wrong"},
    )

    assert response.status_code == 401
