"""Dev page and manual count under /dev/cameras/{cameraId} (Task 2.8).

Ported from the deleted tests/test_ui.py (Task 1.6 XSS coverage), plus the
admin-role check on the manual count and the dev-only registration check.
"""

import pytest
from conftest import FakeSessionFactory, fake_db, fake_user
from fastapi.testclient import TestClient

from plong_elven.app.api.deps import (
    get_current_user,
    get_db,
    get_push_sender,
    get_session_factory,
)
from plong_elven.app.api.main import app, create_app
from plong_elven.app.api.routers.dev import render_page
from plong_elven.core.common.settings import get_settings
from plong_elven.core.state.live_state import LiveState, get_live_state


class _NoZonesSession:
    """Knows no zones, so the background history/alert work stays off the DB."""

    async def get(self, model, key, **kw):
        return None

    async def execute(self, stmt):
        return type("R", (), {"scalar_one_or_none": lambda self: None})()


@pytest.fixture
def live(fake_push_sender):
    state = LiveState()
    app.dependency_overrides.update(
        {
            get_live_state: lambda: state,
            get_db: fake_db,
            get_session_factory: lambda: FakeSessionFactory(_NoZonesSession()),
            get_push_sender: lambda: fake_push_sender,
        }
    )
    yield state
    app.dependency_overrides.clear()


def _as(role: str) -> None:
    user = fake_user(role)
    app.dependency_overrides[get_current_user] = lambda: user


# --- the page (ported from tests/test_ui.py) ---


def test_page_renders_with_camera_id_substituted() -> None:
    response = TestClient(app).get("/dev/cameras/cam1")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "cam1" in response.text
    assert "__CAMERA_ID__" not in response.text


@pytest.mark.parametrize("bad", ["%3Cscript%3E", "a%22b", "a" * 65])
def test_page_rejects_unsafe_camera_id(bad) -> None:
    response = TestClient(app).get(f"/dev/cameras/{bad}")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_page_accepts_dash_underscore_ids() -> None:
    response = TestClient(app).get("/dev/cameras/cam_1-A")
    assert response.status_code == 200
    assert 'const CAMERA_ID = "cam_1-A";' in response.text


def test_render_escapes_hostile_id_in_html_and_js() -> None:
    """Defence in depth behind the path pattern: a hostile id cannot close the
    <script> block or break out of the JS string or the HTML text."""
    hostile = '</script><script>alert(1)</script>"<b>&'
    page = render_page(hostile)

    assert page.count("</script>") == 1  # only the page's own closing tag
    assert "<script>alert(1)" not in page
    assert "<b>" not in page
    assert "&lt;/script&gt;&lt;script&gt;alert(1)" in page  # HTML context
    assert '"\\u003c/script\\u003e\\u003cscript\\u003ealert(1)' in page  # JS context


def test_page_needs_no_login() -> None:
    # No bearer header and no overrides: the page itself is open (dev only).
    assert TestClient(app).get("/dev/cameras/cam1").status_code == 200


def test_page_has_no_internal_key_and_no_localstorage() -> None:
    text = TestClient(app).get("/dev/cameras/cam1").text
    assert "X-Internal-Api-Key" not in text
    assert "localStorage" not in text
    assert "?key=" not in text
    for old in ("KEY", "const qs", "${qs}", "authHeaders"):
        assert old not in text


def test_page_uses_new_contract() -> None:
    text = TestClient(app).get("/dev/cameras/cam1").text
    assert "/api/v1/auth/login" in text
    assert "/dev/tickets" in text
    assert '"stream:" + CAMERA_ID' in text
    assert '"crowd:live"' in text
    assert "/api/v1/crowd/live?ticket=" in text
    assert "/dev/cameras/${CAMERA_ID}/stream?ticket=" in text
    assert "/dev/cameras/${CAMERA_ID}/snapshot" in text
    assert "/dev/cameras/${CAMERA_ID}/layout" in text
    assert "/dev/cameras/${CAMERA_ID}/detections" in text
    assert "msg.cameraId" in text
    assert "Bearer " in text
    assert "body.error.message" in text
    assert "status === 401" in text
    # Old unprefixed routes are gone.
    for old in ("/ws/counts", "/frames/", "/zones/", "`/stream/", '"/detections"'):
        assert old not in text


# --- manual count ---


def test_manual_count_requires_login(live) -> None:
    response = TestClient(app).post(
        "/dev/cameras/cam1/detections", json={"counts": {"z1": 1}}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize("role", ["ops_manager", "floor_staff"])
def test_manual_count_requires_admin(live, role) -> None:
    _as(role)
    response = TestClient(app).post(
        "/dev/cameras/cam1/detections", json={"counts": {"z1": 1}}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "wrong_role"
    assert live.counts == {}


def test_manual_count_feeds_live_state(live) -> None:
    _as("admin")
    response = TestClient(app).post(
        "/dev/cameras/cam1/detections", json={"counts": {"z1": 7}}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert live.counts == {"z1": 7}


def test_manual_count_validates_body_and_camera_id(live) -> None:
    _as("admin")
    client = TestClient(app)
    assert (
        client.post(
            "/dev/cameras/cam1/detections", json={"counts": {"z1": -1}}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/dev/cameras/a%22b/detections", json={"counts": {"z1": 1}}
        ).status_code
        == 422
    )
    assert live.counts == {}


# --- dev only ---


def test_dev_routes_absent_outside_dev(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("INTERNAL_API_KEY", "k" * 32)
    monkeypatch.setenv("JWT_SECRET", "s" * 32)
    get_settings.cache_clear()

    prod_app = create_app()
    assert not [p for p in prod_app.openapi()["paths"] if p.startswith("/dev/")]

    client = TestClient(prod_app)
    assert client.get("/dev/cameras/cam1").status_code == 404
    assert client.post("/dev/tickets", json={"scope": "stream:cam1"}).status_code == 404
    for suffix in ("layout", "snapshot", "stream"):
        assert client.get(f"/dev/cameras/cam1/{suffix}").status_code == 404
    assert (
        client.post(
            "/dev/cameras/cam1/detections", json={"counts": {"z1": 1}}
        ).status_code
        == 404
    )
