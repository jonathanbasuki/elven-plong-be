"""Camera admin routes under /dev/cameras/{cameraId} (Task 2.7).

Ported from the deleted tests/test_stream.py and tests/test_zones_api.py, plus
the admin-role and ticket checks that replace the old ?key= query parameter.
"""

from contextlib import asynccontextmanager

import pytest
from conftest import TEST_INTERNAL_KEY, FakeSessionFactory, fake_db, fake_user
from fastapi.testclient import TestClient
from test_tasks import MemorySession

from plong_elven.app.api.deps import get_current_user, get_db, get_session_factory
from plong_elven.app.api.main import app
from plong_elven.app.services.auth_service import AuthService, create_access_token
from plong_elven.core.state.live_state import LiveState, get_live_state

JPEG = b"\xff\xd8fake-jpeg-bytes\xff\xd9"
LAYOUT = {
    "zones": [
        {
            "id": "z1",
            "name": "Left",
            "polygon": [[0, 0], [0.5, 0], [0.5, 1]],
            "threshold": 8,
        }
    ],
}


class _UserSession:
    def __init__(self, user):
        self.user = user

    async def get(self, model, key, **kw):
        return self.user if key == self.user.user_id else None


@pytest.fixture
def live():
    state = LiveState()
    app.dependency_overrides[get_live_state] = lambda: state
    app.dependency_overrides[get_db] = fake_db
    yield state
    app.dependency_overrides.clear()


@pytest.fixture
def admin(live):
    user = fake_user("admin")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_session_factory] = lambda: FakeSessionFactory(
        _UserSession(user)
    )
    return user


@pytest.fixture
def zones_db(live):
    """An empty zone table; the layout routes read and write it."""
    session = MemorySession()
    session.zones.clear()
    app.dependency_overrides[get_db] = lambda: session
    return session


def _ticket(user, scope: str) -> str:
    return AuthService(None).mint_ticket(user, scope).ticket


def _post_frame(client, camera_id: str, body: bytes, headers, raw: bool) -> None:
    suffix = "/raw" if raw else ""
    response = client.post(
        f"/api/v1/cameras/{camera_id}/frames{suffix}",
        content=body,
        headers={"Content-Type": "image/jpeg", **headers},
    )
    assert response.status_code == 200


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


# --- snapshot (was GET /frames/{camera_id}/latest) ---


def test_snapshot_returns_the_posted_raw_jpeg(admin, internal_headers) -> None:
    client = TestClient(app)
    _post_frame(client, "cam2", JPEG, internal_headers, raw=True)

    response = client.get("/dev/cameras/cam2/snapshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG


def test_snapshot_is_independent_of_annotated_frame(admin, internal_headers) -> None:
    client = TestClient(app)
    annotated = b"\xff\xd8annotated-with-overlay\xff\xd9"
    raw = b"\xff\xd8plain-camera-view\xff\xd9"
    _post_frame(client, "cam3", annotated, internal_headers, raw=False)
    _post_frame(client, "cam3", raw, internal_headers, raw=True)

    response = client.get("/dev/cameras/cam3/snapshot")

    assert response.content == raw
    assert response.content != annotated


def test_snapshot_404_before_any_frame_posted(admin) -> None:
    response = TestClient(app).get("/dev/cameras/never-posted/snapshot")

    _assert_error(response, 404, "not_found")


def test_snapshot_requires_auth(live) -> None:
    response = TestClient(app).get("/dev/cameras/cam1/snapshot")

    _assert_error(response, 401, "unauthenticated")


@pytest.mark.parametrize("role", ["ops_manager", "floor_staff"])
def test_snapshot_requires_admin(live, role) -> None:
    app.dependency_overrides[get_current_user] = lambda: fake_user(role)

    response = TestClient(app).get("/dev/cameras/cam1/snapshot")

    _assert_error(response, 403, "wrong_role")


def test_snapshot_key_query_param_no_longer_works(live) -> None:
    response = TestClient(app).get(
        "/dev/cameras/cam1/snapshot", params={"key": TEST_INTERNAL_KEY}
    )

    _assert_error(response, 401, "unauthenticated")


# --- stream (was GET /stream/{camera_id}) ---


def test_stream_with_matching_ticket_streams(admin, internal_headers) -> None:
    client = TestClient(app)
    _post_frame(client, "cam1", JPEG, internal_headers, raw=False)

    with client.stream(
        "GET",
        "/dev/cameras/cam1/stream",
        params={"ticket": _ticket(admin, "stream:cam1")},
    ) as response:
        assert response.status_code == 200
        assert "multipart/x-mixed-replace" in response.headers["content-type"]
        chunk = next(response.iter_bytes())
        assert JPEG in chunk


def test_stream_404_before_any_frame_posted(admin) -> None:
    response = TestClient(app).get(
        "/dev/cameras/never-posted/stream",
        params={"ticket": _ticket(admin, "stream:never-posted")},
    )

    _assert_error(response, 404, "not_found")


def test_stream_without_ticket_is_401(admin) -> None:
    response = TestClient(app).get("/dev/cameras/cam1/stream")

    _assert_error(response, 401, "unauthenticated")
    assert response.headers["www-authenticate"] == "Bearer"


def test_stream_with_other_camera_ticket_is_401(admin, internal_headers) -> None:
    client = TestClient(app)
    _post_frame(client, "cam1", JPEG, internal_headers, raw=False)

    response = client.get(
        "/dev/cameras/cam1/stream",
        params={"ticket": _ticket(admin, "stream:cam2")},
    )

    _assert_error(response, 401, "unauthenticated")


@pytest.mark.parametrize("kind", ["crowd_ticket", "access_token", "garbage"])
def test_stream_rejects_non_stream_tickets(admin, internal_headers, kind) -> None:
    client = TestClient(app)
    _post_frame(client, "cam1", JPEG, internal_headers, raw=False)
    token = {
        "crowd_ticket": _ticket(admin, "crowd:live"),
        "access_token": create_access_token(admin),
        "garbage": "not-a-jwt",
    }[kind]

    response = client.get("/dev/cameras/cam1/stream", params={"ticket": token})

    _assert_error(response, 401, "unauthenticated")


def test_stream_key_query_param_no_longer_works(admin, internal_headers) -> None:
    client = TestClient(app)
    _post_frame(client, "cam1", JPEG, internal_headers, raw=False)

    response = client.get("/dev/cameras/cam1/stream", params={"key": TEST_INTERNAL_KEY})

    _assert_error(response, 401, "unauthenticated")


def test_stream_ignores_bearer_header(admin, internal_headers) -> None:
    """Only a ?ticket= opens the stream; a bearer header alone does not."""
    client = TestClient(app)
    _post_frame(client, "cam1", JPEG, internal_headers, raw=False)

    response = client.get(
        "/dev/cameras/cam1/stream",
        headers={"Authorization": f"Bearer {create_access_token(admin)}"},
    )

    _assert_error(response, 401, "unauthenticated")


def test_stream_auth_session_is_closed_before_streaming(live, internal_headers) -> None:
    """Like P5: an open MJPEG stream must not hold a pool connection."""
    user = fake_user("admin")
    events: list[str] = []

    @asynccontextmanager
    async def factory():
        events.append("open")
        yield _UserSession(user)
        events.append("close")

    app.dependency_overrides[get_session_factory] = lambda: factory
    client = TestClient(app)
    _post_frame(client, "cam1", JPEG, internal_headers, raw=False)

    with client.stream(
        "GET",
        "/dev/cameras/cam1/stream",
        params={"ticket": _ticket(user, "stream:cam1")},
    ) as response:
        next(response.iter_bytes())
        assert events == ["open", "close"]


# --- layout (was GET/PUT /zones/{camera_id}) ---


def test_get_layout_is_empty_without_zones(admin, zones_db) -> None:
    response = TestClient(app).get("/dev/cameras/cam1/layout")

    assert response.status_code == 200
    assert response.json() == {"zones": []}


def test_put_then_get_layout_round_trips_through_the_db(admin, zones_db) -> None:
    client = TestClient(app)

    put = client.put("/dev/cameras/cam1/layout", json=LAYOUT)
    assert put.status_code == 200
    assert put.json() == {"status": "ok", "zones": 1}

    get = client.get("/dev/cameras/cam1/layout")
    assert get.json() == LAYOUT
    assert zones_db.zones["z1"].threshold == 8


def _zone(**overrides) -> dict:
    return {
        "id": "z1",
        "name": "Z",
        "polygon": [[0, 0], [1, 0], [1, 1]],
        "threshold": 10,
        **overrides,
    }


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"zones": [_zone(polygon=[[0, 0], [1, 1]])]},
            id="two-point-polygon",
        ),
        pytest.param(
            {
                "zones": [_zone(polygon=[[0, 0], [1, 0], [1.5, 1]])],
            },
            id="point-out-of-bounds",
        ),
        pytest.param(
            {
                "zones": [_zone(polygon=[[0, 0], [1, 0], [1, 1, 1]])],
            },
            id="three-coordinate-point",
        ),
        pytest.param(
            {"zones": [_zone(id="a/b")]},
            id="bad-zone-id",
        ),
        pytest.param(
            {"zones": [_zone(name="")]},
            id="empty-name",
        ),
        pytest.param(
            {
                "zones": [_zone(id=f"z{i}") for i in range(51)],
            },
            id="51-zones",
        ),
        pytest.param({"zones": [_zone(threshold=0)]}, id="zero-threshold"),
        pytest.param(
            {"zones": [{k: v for k, v in _zone().items() if k != "threshold"}]},
            id="missing-threshold",
        ),
        pytest.param({}, id="missing-zones"),
    ],
)
def test_put_layout_rejects_bad_input(admin, zones_db, body) -> None:
    response = TestClient(app).put("/dev/cameras/cam1/layout", json=body)

    _assert_error(response, 422, "validation_error")
    assert zones_db.commits == 0


def test_put_layout_accepts_50_zones(admin, zones_db) -> None:
    body = {"zones": [_zone(id=f"z{i}") for i in range(50)]}

    response = TestClient(app).put("/dev/cameras/cam1/layout", json=body)

    assert response.json() == {"status": "ok", "zones": 50}
    assert len(zones_db.zones) == 50


def test_put_layout_refuses_to_delete_a_zone_with_tasks(admin, zones_db) -> None:
    client = TestClient(app)
    client.put(
        "/dev/cameras/cam1/layout", json={"zones": [_zone(id="z1"), _zone(id="z2")]}
    )
    zones_db.add_task(zone_id="z2")

    response = client.put("/dev/cameras/cam1/layout", json={"zones": [_zone(id="z1")]})

    _assert_error(response, 409, "zone_in_use")
    assert sorted(zones_db.zones) == ["z1", "z2"]


def test_get_layout_requires_auth(live, zones_db) -> None:
    response = TestClient(app).get("/dev/cameras/cam1/layout")

    _assert_error(response, 401, "unauthenticated")


def test_put_layout_requires_auth(live, zones_db) -> None:
    response = TestClient(app).put("/dev/cameras/cam1/layout", json=LAYOUT)

    _assert_error(response, 401, "unauthenticated")
    assert zones_db.commits == 0


def test_put_layout_internal_key_no_longer_works(
    live, zones_db, internal_headers
) -> None:
    response = TestClient(app).put(
        "/dev/cameras/cam1/layout", json=LAYOUT, headers=internal_headers
    )

    _assert_error(response, 401, "unauthenticated")


@pytest.mark.parametrize("role", ["ops_manager", "floor_staff"])
@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_layout_requires_admin(live, zones_db, role, method) -> None:
    app.dependency_overrides[get_current_user] = lambda: fake_user(role)

    response = TestClient(app).request(
        method, "/dev/cameras/cam1/layout", json=LAYOUT if method == "PUT" else None
    )

    _assert_error(response, 403, "wrong_role")
    assert zones_db.commits == 0


# --- cameraId pattern and old routes ---


@pytest.mark.parametrize(
    ("method", "suffix"),
    [("GET", "layout"), ("PUT", "layout"), ("GET", "snapshot"), ("GET", "stream")],
)
@pytest.mark.parametrize("camera_id", ["a%22b", "a.b", "x" * 65])
def test_bad_camera_id_is_422(admin, zones_db, method, suffix, camera_id) -> None:
    response = TestClient(app).request(
        method,
        f"/dev/cameras/{camera_id}/{suffix}",
        json=LAYOUT if method == "PUT" else None,
        params={"ticket": _ticket(admin, f"stream:{camera_id}")},
    )

    _assert_error(response, 422, "validation_error")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/frames/cam1/latest"),
        ("GET", "/stream/cam1"),
        ("GET", "/zones/cam1"),
        ("PUT", "/zones/cam1"),
    ],
)
def test_old_camera_routes_are_gone(live, method, path) -> None:
    response = TestClient(app).request(
        method, path, params={"key": TEST_INTERNAL_KEY}, json={}
    )

    assert response.status_code in (404, 405)
