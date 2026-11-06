"""Every route is protected (walker) and the role matrix (Task 2.11).

The walker enumerates the app's real routes from `app.routes`, descending into
included routers, so it sees WebSocket routes and `include_in_schema=False`
routes that `app.openapi()` hides. Each route that is not on an explicit
allow-list is probed twice:

1. anonymously, which must be rejected (HTTP 401 `unauthenticated`, WS close
   1008);
2. with every credential EXCEPT the one the route expects, which must also be
   rejected. This catches a route guarded by the wrong mechanism, for example a
   camera route that accepts a bearer token instead of the internal key.

Expected auth is assigned by convention: `/api/v1/cameras/` uses the internal key,
`.../stream` uses a `stream:<cameraId>` ticket, the WebSocket takes a bearer or
a `crowd:live` ticket, and everything else takes a bearer access token.
"""

import re
from uuid import uuid4

import pytest
from conftest import (
    TEST_INTERNAL_KEY,
    FakePushSender,
    FakeSessionFactory,
    NullSession,
    fake_user,
)
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from plong_elven.api.deps import (
    get_current_user,
    get_push_sender,
    get_session_factory,
)
from plong_elven.api.main import app, create_app
from plong_elven.db.session import get_db
from plong_elven.services.auth_service import AuthService, create_access_token

# --- allow-lists -------------------------------------------------------------

PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    # Authenticated by the refresh token in its body, exactly like /refresh.
    ("POST", "/api/v1/auth/logout"),
    # [TBD-1] RESOLVED 2026-11-03: the dev page is open (dev only); every API
    # call it makes still needs an admin login.
    ("GET", "/dev/cameras/{cameraId}"),
}
# FastAPI's built-in docs routes (plain Starlette routes, not part of §6.1).
BUILTIN_DOCS = {
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
}
ALLOWED_OPEN = PUBLIC | BUILTIN_DOCS

PARAMS = {
    "cameraId": "cam1",
    "zoneId": "z1",
    "taskId": str(uuid4()),
    "staffId": str(uuid4()),
}

# --- walker ------------------------------------------------------------------


def walk_routes(application: FastAPI) -> set[tuple[str, str]]:
    """Every (method, path) the app serves; WebSockets use method "WS"."""
    found: set[tuple[str, str]] = set()

    def visit(routes: list[BaseRoute], prefix: str) -> None:
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:  # FastAPI 0.159 `_IncludedRouter`
                visit(included.routes, prefix + route.include_context.prefix)
                continue
            path = prefix + route.path
            if isinstance(route, WebSocketRoute) or type(route).__name__ == (
                "APIWebSocketRoute"
            ):
                found.add(("WS", path))
            else:
                for method in route.methods or ():
                    if method != "HEAD":
                        found.add((method, path))

    visit(application.routes, "")
    return found


def expected_auth(route: tuple[str, str]) -> str:
    method, path = route
    if path.startswith("/api/v1/cameras/"):
        return "internal"
    if method == "WS":
        return "bearer_or_ticket"
    if path.endswith("/stream"):
        return "ticket"
    return "bearer"


def _url(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: PARAMS.get(m.group(1), "x"), path)


class _AdminSession(NullSession):
    """Loads one active admin for any lookup, so a real bearer token or ticket
    is accepted by whatever check reads it."""

    def __init__(self, user) -> None:
        self.user = user

    async def get(self, model, key, **kw):
        return self.user

    async def scalar(self, stmt):
        return self.user

    async def execute(self, stmt):
        result = await super().execute(stmt)
        result.scalar_one_or_none = lambda: self.user
        return result


def _wrong_credentials(route, admin, session) -> tuple[dict, dict]:
    """Every credential except the one `route` expects, all valid."""
    auth = AuthService(session)
    bearer = {"Authorization": f"Bearer {create_access_token(admin)}"}
    internal = {"X-Internal-Api-Key": TEST_INTERNAL_KEY}
    stream_ticket = auth.mint_ticket(admin, f"stream:{PARAMS['cameraId']}").ticket
    crowd_ticket = auth.mint_ticket(admin, "crowd:live").ticket
    kind = expected_auth(route)
    if kind == "internal":
        return bearer, {"ticket": stream_ticket}
    if kind == "ticket":
        return bearer | internal, {}
    if kind == "bearer_or_ticket":
        return internal, {}
    return internal, {"ticket": crowd_ticket}


def _rejected(client: TestClient, route, headers, params) -> tuple[bool, str]:
    method, path = route
    if method == "WS":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = _url(path) + (f"?{query}" if query else "")
        try:
            with client.websocket_connect(url, headers=headers):
                return False, "WebSocket accepted"
        except WebSocketDisconnect as exc:
            return exc.code == 1008, f"closed with {exc.code}"
    response = client.request(method, _url(path), headers=headers, params=params)
    if response.status_code != 401:
        return False, f"HTTP {response.status_code}"
    code = response.json().get("error", {}).get("code")
    return code == "unauthenticated", f"401 with code {code!r}"


def _const(value):
    """A zero-arg dependency override. A default-arg lambda (`lambda s=x: s`)
    would be read by FastAPI as a query parameter."""

    def dependency():
        return value

    return dependency


def find_unprotected(application: FastAPI) -> dict[tuple[str, str], str]:
    """Routes reachable without their expected auth, mapped to the reason."""
    admin = fake_user("admin")
    admin_session = _AdminSession(admin)
    offenders: dict[tuple[str, str], str] = {}
    probes = [
        ("anonymous", NullSession(), lambda route: ({}, {})),
        (
            "wrong credentials",
            admin_session,
            lambda route: _wrong_credentials(route, admin, admin_session),
        ),
    ]
    try:
        for label, session, creds in probes:
            application.dependency_overrides.update(
                {
                    get_db: _const(session),
                    get_session_factory: _const(FakeSessionFactory(session)),
                    get_push_sender: FakePushSender,
                }
            )
            client = TestClient(application)
            for route in sorted(walk_routes(application) - ALLOWED_OPEN):
                if route in offenders:
                    continue
                headers, params = creds(route)
                ok, detail = _rejected(client, route, headers, params)
                if not ok:
                    offenders[route] = f"{label}: {detail}"
    finally:
        application.dependency_overrides.clear()
    return offenders


# --- walker tests ------------------------------------------------------------

ROUTES = walk_routes(app)


def test_walker_sees_websocket_dev_and_openapi_routes() -> None:
    openapi = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops}
    assert openapi <= ROUTES
    assert ("WS", "/api/v1/crowd/live") in ROUTES
    assert ("GET", "/dev/cameras/{cameraId}") in ROUTES  # conftest forces APP_ENV=dev
    assert ("POST", "/dev/cameras/{cameraId}/detections") in ROUTES


def test_allow_list_has_no_stale_entries() -> None:
    assert ALLOWED_OPEN <= ROUTES


def test_no_route_is_reachable_without_its_expected_auth() -> None:
    assert find_unprotected(app) == {}


def test_walker_flags_deliberately_unprotected_routes() -> None:
    extra = create_app()

    @extra.get("/api/v1/oops")
    async def oops() -> dict:
        return {}

    @extra.post("/api/v1/hidden", include_in_schema=False)
    async def hidden() -> dict:
        return {}

    @extra.post("/api/v1/cameras/{cameraId}/bearer-guarded")
    async def wrong_mechanism(_user=Depends(get_current_user)) -> dict:  # noqa: B008
        return {}

    @extra.websocket("/api/v1/open-ws")
    async def open_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close()

    flagged = find_unprotected(extra)
    assert set(flagged) == {
        ("GET", "/api/v1/oops"),
        ("POST", "/api/v1/hidden"),
        ("POST", "/api/v1/cameras/{cameraId}/bearer-guarded"),
        ("WS", "/api/v1/open-ws"),
    }
    assert flagged[("POST", "/api/v1/cameras/{cameraId}/bearer-guarded")].startswith(
        "wrong credentials"
    )


# --- role matrix -------------------------------------------------------------

ANY = {"floor_staff", "ops_manager", "admin"}
DISPATCH_ROUTES = {
    ("POST", "/api/v1/tasks/broadcast"),
    ("POST", "/api/v1/tasks/{taskId}/accept"),
    ("POST", "/api/v1/tasks/{taskId}/complete"),
    ("POST", "/api/v1/tasks/{taskId}/finish"),
}
ADMIN_CAMERA_ROUTES = {
    ("GET", "/dev/cameras/{cameraId}/layout"),
    ("PUT", "/dev/cameras/{cameraId}/layout"),
    ("GET", "/dev/cameras/{cameraId}/snapshot"),
}
ROLE_MATRIX = {
    ("GET", "/api/v1/profile"): ANY,
    ("PUT", "/api/v1/profile/push-token"): ANY,
    ("GET", "/api/v1/zones"): ANY,
    ("GET", "/api/v1/zones/{zoneId}"): ANY,
    ("GET", "/api/v1/zones/{zoneId}/crowd"): ANY,
    ("GET", "/api/v1/zones/{zoneId}/crowd/history"): ANY,
    ("GET", "/api/v1/tasks"): ANY,
    ("GET", "/api/v1/tasks/{taskId}"): ANY,
    ("GET", "/api/v1/staff"): ANY,
    ("GET", "/api/v1/staff/{staffId}"): ANY,
    ("POST", "/api/v1/tasks/broadcast"): {"ops_manager"},
    ("POST", "/api/v1/tasks/{taskId}/accept"): {"floor_staff"},
    ("POST", "/api/v1/tasks/{taskId}/complete"): {"floor_staff"},
    ("POST", "/api/v1/tasks/{taskId}/finish"): {"ops_manager"},
    ("GET", "/dev/cameras/{cameraId}/layout"): {"admin"},
    ("PUT", "/dev/cameras/{cameraId}/layout"): {"admin"},
    ("GET", "/dev/cameras/{cameraId}/snapshot"): {"admin"},
    ("POST", "/dev/tickets"): {"admin"},
    ("POST", "/dev/cameras/{cameraId}/detections"): {"admin"},
}
BODIES = {
    ("PUT", "/api/v1/profile/push-token"): {"pushToken": "t"},
    ("POST", "/api/v1/tasks/broadcast"): {
        "zoneId": "z1",
        "totalStaff": 1,
        "instruction": "x",
    },
    ("PUT", "/dev/cameras/{cameraId}/layout"): {"zones": []},
    ("POST", "/dev/tickets"): {"scope": "crowd:live"},
    ("POST", "/dev/cameras/{cameraId}/detections"): {"counts": {"z1": 1}},
}


def test_every_bearer_route_is_in_the_role_matrix() -> None:
    bearer = {r for r in ROUTES - ALLOWED_OPEN if expected_auth(r) == "bearer"}
    assert bearer == set(ROLE_MATRIX)


def test_matrix_encodes_design_role_rules() -> None:
    """§6.3: admin has no dispatch powers; camera admin routes are admin-only."""
    for route in DISPATCH_ROUTES:
        assert "admin" not in ROLE_MATRIX[route], route
    for route in ADMIN_CAMERA_ROUTES:
        assert ROLE_MATRIX[route] == {"admin"}, route


def _matrix_user(role: str):
    user = fake_user(role)
    # conftest's "test@plong.test" uses a reserved TLD that EmailStr rejects.
    user.email = f"{role}@plongelven.io"
    return user


@pytest.mark.parametrize("route", sorted(ROLE_MATRIX))
@pytest.mark.parametrize("role", sorted(ANY))
def test_role_matrix(route, role, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ZONES_PATH", str(tmp_path / "z.json"))
    app.dependency_overrides.update(
        {
            get_db: lambda: NullSession(),
            get_session_factory: lambda: FakeSessionFactory(NullSession()),
            get_push_sender: FakePushSender,
            get_current_user: lambda: _matrix_user(role),
        }
    )
    method, path = route
    try:
        response = TestClient(app).request(method, _url(path), json=BODIES.get(route))
    finally:
        app.dependency_overrides.clear()
    if role in ROLE_MATRIX[route]:
        # P7: fakes legitimately 404 unknown ids, so assert "not 401/403".
        assert response.status_code not in (401, 403), (route, role, response.text)
    else:
        assert response.status_code == 403, (route, role, response.text)
        assert response.json()["error"]["code"] == "wrong_role"
