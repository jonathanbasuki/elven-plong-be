"""Zones and crowd read routes (Task 2.9, design §6.1/§6.2)."""

from datetime import UTC, datetime

import pytest
from conftest import FakeSession, FakeSessionFactory, fake_db, fake_user
from fastapi.testclient import TestClient

from plong_elven.app.api.deps import get_current_user, get_session_factory
from plong_elven.app.api.main import app
from plong_elven.app.db.models import Zone, ZoneLog
from plong_elven.app.db.session import get_db
from plong_elven.app.schemas.zones import CrowdOut, HistoryBucketOut
from plong_elven.core.state.live_state import LiveState, get_live_state


async def fake_current_user():
    return fake_user()


class _HistorySession(FakeSession):
    """FakeSession whose scalars() returns two history rows, newest first."""

    def __init__(self) -> None:
        self.statements: list = []

    async def scalars(self, stmt):
        self.statements.append(stmt)
        rows = [
            ZoneLog(
                zone_id="z1",
                bucket_start=datetime(2026, 11, 3, 12, 5, tzinfo=UTC),
                avg_count=5.5,
                peak_count=8,
            ),
            ZoneLog(
                zone_id="z1",
                bucket_start=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
                avg_count=3.0,
                peak_count=4,
            ),
        ]
        return type("R", (), {"all": lambda self: rows})()


class _ZoneListSession(FakeSession):
    async def scalars(self, stmt):
        if stmt.column_descriptions[0]["entity"] is not Zone:
            return await super().scalars(stmt)  # the task-expiry sweep finds none
        zone = await self.get(Zone, "z1")
        return type("R", (), {"all": lambda self: [zone]})()


@pytest.fixture
def client_as():
    """Return a factory: client_as(role, live=None, db=fake_db) -> TestClient."""

    def _make(role: str | None = "floor_staff", live=None, db=fake_db):
        app.dependency_overrides[get_db] = db
        if role is not None:
            app.dependency_overrides[get_current_user] = lambda: fake_user(role)
        state = live or LiveState()
        app.dependency_overrides[get_live_state] = lambda: state
        app.dependency_overrides[get_session_factory] = lambda: FakeSessionFactory(
            FakeSession()
        )
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


# --- Ported from tests/test_dispatch.py (zone tests) ---


def test_crowd_uses_latest_detection_count(client_as, internal_headers) -> None:
    client = client_as("floor_staff")
    detection = client.post(
        "/api/v1/cameras/cam1/detections",
        json={"counts": {"z1": 12}},
        headers=internal_headers,
    )
    response = client.get("/api/v1/zones/z1/crowd")

    assert detection.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 12
    assert body["status"] == "crowded"
    assert body["zoneId"] == "z1"
    assert body["observedAt"].endswith("Z")


def test_crowd_returns_zero_before_first_detection(client_as) -> None:
    response = client_as("floor_staff").get("/api/v1/zones/z1/crowd")

    assert response.status_code == 200
    assert response.json() == {
        "zoneId": "z1",
        "count": 0,
        "status": "safe",
        "observedAt": None,
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/zones/z9",
        "/api/v1/zones/z9/crowd",
    ],
)
def test_unknown_zone_uses_contract_error_shape(client_as, path) -> None:
    response = client_as("floor_staff").get(path)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert set(response.json()["error"]) == {"code", "message"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/zones",
        "/api/v1/zones/z1",
        "/api/v1/zones/z1/crowd",
        "/api/v1/zones/z1/crowd/history",
    ],
)
def test_zone_routes_require_auth(client_as, path) -> None:
    response = client_as(None).get(path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


# --- New ---


def test_zone_payload_has_no_live_fields(client_as) -> None:
    live = LiveState()
    live.counts["z1"] = 12
    response = client_as("floor_staff", live=live).get("/api/v1/zones/z1")

    assert response.status_code == 200
    assert response.json() == {
        "zoneId": "z1",
        "name": "Main Lobby",
        "threshold": 10,
        "isActiveTask": False,
    }


def test_list_zones_returns_zone_payloads(client_as) -> None:
    async def db():
        yield _ZoneListSession()

    response = client_as("floor_staff", db=db).get("/api/v1/zones")

    assert response.status_code == 200
    assert response.json() == [
        {
            "zoneId": "z1",
            "name": "Main Lobby",
            "threshold": 10,
            "isActiveTask": False,
        }
    ]


def test_list_zones_empty_is_200_empty_list(client_as) -> None:
    assert client_as("floor_staff").get("/api/v1/zones").json() == []


def test_history_returns_buckets_newest_first(client_as) -> None:
    async def db():
        yield _HistorySession()

    response = client_as("floor_staff", db=db).get("/api/v1/zones/z1/crowd/history")

    assert response.status_code == 200
    assert response.json() == [
        {"bucketStart": "2026-11-03T12:05:00Z", "avgCount": 5.5, "peakCount": 8},
        {"bucketStart": "2026-11-03T12:00:00Z", "avgCount": 3.0, "peakCount": 4},
    ]


@pytest.mark.parametrize(("query", "expected"), [("", 12), ("?limit=288", 288)])
def test_history_limit_default_and_max(client_as, query, expected) -> None:
    session = _HistorySession()

    async def db():
        yield session

    response = client_as("floor_staff", db=db).get(
        f"/api/v1/zones/z1/crowd/history{query}"
    )

    assert response.status_code == 200
    assert session.statements[0]._limit_clause.value == expected


@pytest.mark.parametrize("limit", ["0", "289", "-1", "abc"])
def test_history_limit_bounds(client_as, limit) -> None:
    response = client_as("floor_staff").get(
        f"/api/v1/zones/z1/crowd/history?limit={limit}"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("role", ["floor_staff", "ops_manager", "admin"])
def test_zone_reads_allow_any_role(client_as, role) -> None:
    client = client_as(role)
    for path in (
        "/api/v1/zones",
        "/api/v1/zones/z1",
        "/api/v1/zones/z1/crowd",
        "/api/v1/zones/z1/crowd/history",
    ):
        assert client.get(path).status_code == 200, (role, path)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/dispatch/zones",
        "/api/v1/dispatch/zones/z1",
        "/api/v1/dispatch/zones/z1/history",
    ],
)
def test_old_dispatch_zone_routes_are_gone(client_as, path) -> None:
    assert client_as("floor_staff").get(path).status_code == 404


def test_crowd_and_history_datetimes_are_iso8601_utc() -> None:
    """Review Focus 5: UTC datetimes serialize with Z, naive ones are read as UTC."""
    aware = datetime(2026, 11, 3, 19, 0, tzinfo=UTC)
    naive = datetime(2026, 11, 3, 19, 0)
    crowd = CrowdOut(zoneId="z1", count=1, status="safe", observedAt=aware)
    bucket = HistoryBucketOut(bucketStart=naive, avgCount=1.0, peakCount=1)

    assert crowd.model_dump(mode="json")["observedAt"] == "2026-11-03T19:00:00Z"
    assert bucket.model_dump(mode="json")["bucketStart"] == "2026-11-03T19:00:00Z"
