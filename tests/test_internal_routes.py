"""Camera routes under /api/v1/cameras/{cameraId} (Task 2.6)."""

import pytest
from conftest import FakeSessionFactory, fake_user
from fastapi.testclient import TestClient

from plong_elven.app.api.deps import get_push_sender, get_session_factory
from plong_elven.app.api.main import app
from plong_elven.app.services.auth_service import create_access_token
from plong_elven.core.state.live_state import LiveState, get_live_state

FIVE_MB = 5 * 1024 * 1024


class _UserSession:
    """Knows one user (for WebSocket auth) and no zones (keeps background work off
    the database)."""

    def __init__(self, user=None):
        self.user = user

    async def get(self, model, key, **kw):
        return self.user if self.user is not None and key == self.user.user_id else None

    async def execute(self, stmt):
        user = self.user
        return type("R", (), {"scalar_one_or_none": lambda self: user})()


@pytest.fixture
def live(fake_push_sender):
    state = LiveState()
    app.dependency_overrides.update(
        {
            get_live_state: lambda: state,
            get_session_factory: lambda: FakeSessionFactory(_UserSession()),
            get_push_sender: lambda: fake_push_sender,
        }
    )
    yield state
    app.dependency_overrides.clear()


def test_detection_updates_live_counts_and_publishes(
    internal_headers, fake_push_sender
) -> None:
    admin = fake_user("admin")
    state = LiveState()
    app.dependency_overrides.update(
        {
            get_live_state: lambda: state,
            get_session_factory: lambda: FakeSessionFactory(_UserSession(admin)),
            get_push_sender: lambda: fake_push_sender,
        }
    )
    try:
        client = TestClient(app)
        with client.websocket_connect(
            "/api/v1/crowd/live",
            headers={"Authorization": f"Bearer {create_access_token(admin)}"},
        ) as ws:
            response = client.post(
                "/api/v1/cameras/cam1/detections",
                json={"counts": {"z1": 3}},
                headers=internal_headers,
            )
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
            assert ws.receive_json() == {"cameraId": "cam1", "counts": {"z1": 3}}
    finally:
        app.dependency_overrides.clear()
    assert state.counts == {"z1": 3}


def test_detection_without_connected_clients(live, internal_headers) -> None:
    response = TestClient(app).post(
        "/api/v1/cameras/cam1/detections",
        json={"counts": {"z1": 2}},
        headers=internal_headers,
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert live.counts == {"z1": 2}


ROUTES = [
    ("/api/v1/cameras/cam1/detections", {"json": {"counts": {"z1": 1}}}),
    ("/api/v1/cameras/cam1/frames", {"content": b"jpeg"}),
    ("/api/v1/cameras/cam1/frames/raw", {"content": b"jpeg"}),
]


@pytest.mark.parametrize("path,kwargs", ROUTES)
@pytest.mark.parametrize("headers", [{}, {"X-Internal-Api-Key": "wrong"}])
def test_missing_or_wrong_key_is_401(live, path, kwargs, headers) -> None:
    response = TestClient(app).post(path, headers=headers, **kwargs)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert live.counts == {}
    assert live.frames == {}
    assert live.raw_frames == {}


@pytest.mark.parametrize("path,kwargs", ROUTES)
def test_blank_internal_key_rejects_every_camera_call(
    live, monkeypatch, path, kwargs
) -> None:
    """P2: a blank INTERNAL_API_KEY rejects the call even with an empty header."""
    from plong_elven.core.common.settings import get_settings

    monkeypatch.setenv("INTERNAL_API_KEY", "")
    get_settings.cache_clear()
    response = TestClient(app).post(path, headers={"X-Internal-Api-Key": ""}, **kwargs)
    assert response.status_code == 401
    assert live.counts == {}
    assert live.frames == {}
    assert live.raw_frames == {}


@pytest.mark.parametrize(
    "body,status",
    [
        ({"counts": {f"z{i}": 1 for i in range(51)}}, 422),
        ({"counts": {"z1": -1}}, 422),
        ({"counts": {"z1": 10001}}, 422),
        ({"counts": {"a/b": 1}}, 422),
        ({"counts": {"<script>": 1}}, 422),
        ({"counts": {"z" * 65: 1}}, 422),
        ({"counts": {"z1": "many"}}, 422),
        ({"camera_id": "cam1", "counts": {"z1": 1}}, 200),  # extra field ignored
        ({"counts": {f"z{i}": 10000 for i in range(50)}}, 200),
    ],
)
def test_counts_bounds(live, internal_headers, body, status) -> None:
    response = TestClient(app).post(
        "/api/v1/cameras/cam1/detections", json=body, headers=internal_headers
    )
    assert response.status_code == status
    if status == 422:
        assert response.json()["error"]["code"] == "validation_error"
        assert live.counts == {}
    else:
        assert live.counts == body["counts"]


@pytest.mark.parametrize(
    "suffix,store", [("frames", "frames"), ("frames/raw", "raw_frames")]
)
def test_frame_exactly_5mb_ok_and_5mb_plus_one_is_413(
    live, internal_headers, suffix, store
) -> None:
    client = TestClient(app)
    path = f"/api/v1/cameras/cam1/{suffix}"
    headers = {"Content-Type": "image/jpeg", **internal_headers}
    ok = client.post(path, content=b"x" * FIVE_MB, headers=headers)
    assert ok.status_code == 200
    assert ok.json() == {"status": "ok"}

    too_big = client.post(path, content=b"y" * (FIVE_MB + 1), headers=headers)
    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "payload_too_large"
    assert getattr(live, store) == {"cam1": b"x" * FIVE_MB}


def test_chunked_frame_over_5mb_is_413(live, internal_headers) -> None:
    """No content-length header: the cap is enforced while streaming the body."""

    def chunks():
        for _ in range(6):
            yield b"z" * (1024 * 1024)

    response = TestClient(app).post(
        "/api/v1/cameras/cam9/frames",
        content=chunks(),
        headers={"Content-Type": "image/jpeg", **internal_headers},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert live.frames == {}


@pytest.mark.parametrize("suffix", ["detections", "frames", "frames/raw"])
@pytest.mark.parametrize("bad", ["a%22b", "a%3Cscript%3E", "c" * 65])
def test_bad_camera_id_is_422(live, internal_headers, suffix, bad) -> None:
    response = TestClient(app).post(
        f"/api/v1/cameras/{bad}/{suffix}",
        json={"counts": {"z1": 1}},
        headers=internal_headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert live.counts == {}
    assert live.frames == {}
    assert live.raw_frames == {}


def test_old_camera_routes_are_gone(live, internal_headers) -> None:
    client = TestClient(app)
    assert (
        client.post(
            "/detections",
            json={"camera_id": "cam1", "counts": {"z1": 1}},
            headers=internal_headers,
        ).status_code
        == 404
    )
    assert (
        client.post("/frames/cam1", content=b"j", headers=internal_headers).status_code
        == 404
    )
    assert (
        client.post(
            "/frames/cam1/raw", content=b"j", headers=internal_headers
        ).status_code
        == 404
    )
    assert live.counts == {}
    assert live.frames == {}


def test_camera_reads_its_zones_from_the_db(live, internal_headers) -> None:
    from test_tasks import MemorySession

    from plong_elven.app.db.session import get_db

    app.dependency_overrides[get_db] = lambda: MemorySession()

    response = TestClient(app).get(
        "/api/v1/cameras/cam1/zones", headers=internal_headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "zones": [
            {
                "id": "z1",
                "name": "Main Lobby",
                "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
            }
        ]
    }


def test_camera_zones_need_the_internal_key(live) -> None:
    response = TestClient(app).get("/api/v1/cameras/cam1/zones")
    assert response.status_code == 401
