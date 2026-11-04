# tests/test_broadcasts.py
"""Broadcast and staff routes (Task 2.10, design §6.1-6.3).

`MemorySession` is a small in-memory stand-in for AsyncSession. It evaluates the
simple `where` / `order_by` / `limit` clauses BroadcastService builds, so the
broadcast rules (slot limit, one open broadcast per zone, 20 s expiry, accept /
complete / finish) run end to end through the HTTP routes without Postgres.
The row lock itself is proven on real Postgres in test_broadcast_race_pg.py.
"""

import operator as op
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import fake_user
from fastapi.testclient import TestClient
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, Null

from plong_elven.api.deps import get_current_user, get_notification_service
from plong_elven.api.main import app
from plong_elven.db.models import Broadcast, BroadcastResponse, User, Zone
from plong_elven.db.session import get_db
from plong_elven.schemas.broadcasts import AcceptedStaffOut, BroadcastOut, FinishOut

_OPS = {
    operators.eq: op.eq,
    operators.ne: op.ne,
    operators.lt: op.lt,
    operators.gt: op.gt,
    operators.is_: lambda a, b: a is b,
    operators.is_not: lambda a, b: a is not b,
}


def _value(element):
    if isinstance(element, Null):
        return None
    if isinstance(element, BindParameter):
        return element.effective_value
    raise NotImplementedError(f"unsupported clause value {element!r}")


def _matches(obj, clause) -> bool:
    if clause is None:
        return True
    if isinstance(clause, BinaryExpression):
        left = getattr(obj, clause.left.key)
        return _OPS[clause.operator](left, _value(clause.right))
    # BooleanClauseList (AND of criteria)
    return all(_matches(obj, c) for c in clause.clauses)


class _Rows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class MemorySession:
    """Records statements and commits; stores zones, users, broadcasts, responses."""

    def __init__(self) -> None:
        self.zones: dict[str, Zone] = {
            "z1": Zone(
                zone_id="z1",
                name="Main Lobby",
                map_image_url="https://example.com/z1.png",
                polygon=[[0.0, 0.0], [1.0, 1.0]],
                warning_threshold=5,
                crowded_threshold=10,
            )
        }
        self.users: dict[UUID, User] = {}
        self.broadcasts: list[Broadcast] = []
        self.responses: list[BroadcastResponse] = []
        self.statements: list = []
        self.commits = 0

    # --- helpers for tests ---
    def add_user(self, user: User) -> User:
        self.users[user.id] = user
        return user

    def add_broadcast(
        self,
        *,
        zone_id: str = "z1",
        status: str = "open",
        staff_needed: int = 2,
        expires_in: float = 20,
        created_at: datetime | None = None,
    ) -> Broadcast:
        now = datetime.now(UTC)
        b = Broadcast(
            broadcast_id=uuid4(),
            zone_id=zone_id,
            status=status,
            staff_needed=staff_needed,
            created_at=created_at or now,
            expires_at=now + timedelta(seconds=expires_in),
        )
        self.broadcasts.append(b)
        return b

    def _table_rows(self, stmt) -> list:
        entity = stmt.column_descriptions[0]["entity"]
        if entity is None:  # select(func.count()).select_from(...)
            entity = BroadcastResponse
        return {
            Broadcast: self.broadcasts,
            BroadcastResponse: self.responses,
        }[entity]

    def _select(self, stmt) -> list:
        rows = [r for r in self._table_rows(stmt) if _matches(r, stmt.whereclause)]
        for clause in reversed(stmt._order_by_clauses):
            desc = clause.modifier is operators.desc_op
            key = clause.element.key if desc else clause.key
            rows.sort(key=lambda r, k=key: getattr(r, k), reverse=desc)
        if stmt._limit_clause is not None:
            rows = rows[: stmt._limit]
        return rows

    # --- AsyncSession surface used by BroadcastService ---
    async def get(self, model, key):
        if model is Zone:
            return self.zones.get(key)
        if model is User:
            return self.users.get(key)
        if model is BroadcastResponse:
            for r in self.responses:
                if (
                    r.broadcast_id == key["broadcast_id"]
                    and r.staff_id == key["staff_id"]
                ):
                    return r
            return None
        return None

    async def scalar(self, stmt):
        self.statements.append(stmt)
        rows = self._select(stmt)
        if stmt.column_descriptions[0]["entity"] is None:
            return len(rows)
        return rows[0] if rows else None

    async def scalars(self, stmt):
        self.statements.append(stmt)
        return _Rows(self._select(stmt))

    async def execute(self, stmt):
        # select(BroadcastResponse, User).join(User, ...).where(...)
        self.statements.append(stmt)
        rows = [r for r in self.responses if _matches(r, stmt.whereclause)]
        rows.sort(key=lambda r: r.responded_at)
        return _Rows([(r, self.users[r.staff_id]) for r in rows])

    def add(self, obj) -> None:
        now = datetime.now(UTC)
        if isinstance(obj, Broadcast):
            obj.broadcast_id = obj.broadcast_id or uuid4()
            obj.status = obj.status or "open"
            obj.created_at = obj.created_at or now
            self.broadcasts.append(obj)
        elif isinstance(obj, BroadcastResponse):
            obj.responded_at = obj.responded_at or now
            self.responses.append(obj)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass

    async def refresh(self, _obj) -> None:
        pass


class _NoNotifier:
    async def notify_role(self, *_args) -> None:
        pass


@pytest.fixture
def session() -> MemorySession:
    return MemorySession()


@pytest.fixture
def client_as(session):
    """Return a factory: client_as(role) -> (TestClient, user) bound to `session`."""

    def _make(role: str) -> tuple[TestClient, User]:
        user = session.add_user(fake_user(role))
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_notification_service] = _NoNotifier
        return TestClient(app), user

    yield _make
    app.dependency_overrides.clear()


def _err(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


# --- GET /api/v1/broadcasts ---------------------------------------------------


def test_expired_open_broadcast_is_closed_and_filtered(session, client_as) -> None:
    """Review Focus 1: closed and committed first, then excluded from status=open."""
    client, _ = client_as("floor_staff")
    b = session.add_broadcast(expires_in=-1)

    response = client.get("/api/v1/broadcasts?zoneId=z1&status=open")

    assert response.status_code == 200
    assert response.json() == []
    assert b.status == "closed"
    assert session.commits == 1


def test_expired_sweep_respects_zone_filter(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    session.zones["z2"] = Zone(zone_id="z2", name="Z2", map_image_url="u", polygon=[])
    other = session.add_broadcast(zone_id="z2", expires_in=-1)

    response = client.get("/api/v1/broadcasts?zoneId=z1&status=open")

    assert response.json() == []
    assert other.status == "open"
    assert session.commits == 0


def test_list_without_status_returns_newest_first_including_closed(
    session, client_as
) -> None:
    client, _ = client_as("ops_manager")
    now = datetime.now(UTC)
    old = session.add_broadcast(status="closed", created_at=now - timedelta(hours=1))
    new = session.add_broadcast(status="open", created_at=now)

    body = client.get("/api/v1/broadcasts?zoneId=z1").json()

    assert [item["broadcastId"] for item in body] == [
        str(new.broadcast_id),
        str(old.broadcast_id),
    ]
    assert [item["status"] for item in body] == ["open", "closed"]


def test_open_lookup_returns_the_open_broadcast(session, client_as) -> None:
    """Replaces GET /dispatch/broadcasts/current: ?zoneId=..&status=open."""
    client, _ = client_as("floor_staff")
    session.add_broadcast(status="finished")
    b = session.add_broadcast(staff_needed=3)

    body = client.get("/api/v1/broadcasts?zoneId=z1&status=open").json()

    assert len(body) == 1
    assert set(body[0]) == {
        "broadcastId",
        "zoneId",
        "status",
        "staffNeeded",
        "expiresAt",
        "acceptedCount",
        "completedCount",
        "acceptedStaff",
    }
    assert body[0]["broadcastId"] == str(b.broadcast_id)
    assert body[0]["staffNeeded"] == 3
    assert body[0]["acceptedCount"] == 0
    assert body[0]["acceptedStaff"] == []


def test_list_empty_is_200_empty_list(client_as) -> None:
    client, _ = client_as("admin")
    response = client.get("/api/v1/broadcasts?zoneId=z9&status=open")
    assert response.status_code == 200
    assert response.json() == []


def test_limit_bounds(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    _err(client.get("/api/v1/broadcasts?limit=0"), 422, "validation_error")
    _err(client.get("/api/v1/broadcasts?limit=51"), 422, "validation_error")
    _err(client.get("/api/v1/broadcasts?status=nope"), 422, "validation_error")

    assert client.get("/api/v1/broadcasts").status_code == 200
    listing = [s for s in session.statements if s._limit_clause is not None]
    assert listing[-1]._limit == 20

    for i in range(3):
        session.add_broadcast(status="closed", created_at=datetime(2026, 1, 1 + i))
    assert len(client.get("/api/v1/broadcasts?limit=2").json()) == 2


def test_list_requires_auth() -> None:
    _err(TestClient(app).get("/api/v1/broadcasts"), 401, "unauthenticated")


# --- POST /api/v1/broadcasts --------------------------------------------------


def test_create_broadcast_requires_manager_role(client_as) -> None:
    """Moved from tests/test_dispatch.py with the new URL."""
    client, _ = client_as("floor_staff")
    response = client.post(
        "/api/v1/broadcasts", json={"zoneId": "z1", "staffNeeded": 3}
    )
    _err(response, 403, "wrong_role")


def test_admin_cannot_create_broadcast(client_as) -> None:
    client, _ = client_as("admin")
    response = client.post(
        "/api/v1/broadcasts", json={"zoneId": "z1", "staffNeeded": 1}
    )
    _err(response, 403, "wrong_role")


def test_create_broadcast_returns_201_with_20s_expiry(session, client_as) -> None:
    client, _ = client_as("ops_manager")
    before = datetime.now(UTC)

    response = client.post(
        "/api/v1/broadcasts", json={"zoneId": "z1", "staffNeeded": 2}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["zoneId"] == "z1"
    assert body["status"] == "open"
    assert body["staffNeeded"] == 2
    assert body["acceptedCount"] == 0
    assert body["expiresAt"].endswith("Z")
    expires = datetime.fromisoformat(body["expiresAt"])
    assert timedelta(seconds=19) < expires - before <= timedelta(seconds=21)
    assert len(session.broadcasts) == 1
    assert session.commits == 1


def test_create_rejects_bad_body_and_unknown_zone(client_as) -> None:
    client, _ = client_as("ops_manager")
    bad = client.post("/api/v1/broadcasts", json={"zoneId": "z1", "staffNeeded": 0})
    _err(bad, 422, "validation_error")
    snake = client.post("/api/v1/broadcasts", json={"zone_id": "z1", "staff_needed": 1})
    _err(snake, 422, "validation_error")
    unknown = client.post("/api/v1/broadcasts", json={"zoneId": "z9", "staffNeeded": 1})
    _err(unknown, 404, "not_found")


def test_409_error_shape(session, client_as) -> None:
    """One open broadcast per zone: a live open broadcast blocks a new one."""
    client, _ = client_as("ops_manager")
    session.add_broadcast()

    response = client.post(
        "/api/v1/broadcasts", json={"zoneId": "z1", "staffNeeded": 1}
    )

    assert response.status_code == 409
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "broadcast_open"
    assert isinstance(response.json()["error"]["message"], str)


def test_create_closes_an_expired_open_broadcast_first(session, client_as) -> None:
    client, _ = client_as("ops_manager")
    stale = session.add_broadcast(expires_in=-1)

    response = client.post(
        "/api/v1/broadcasts", json={"zoneId": "z1", "staffNeeded": 1}
    )

    assert response.status_code == 201
    assert stale.status == "closed"
    assert len(session.broadcasts) == 2


# --- POST /api/v1/broadcasts/{id}/accept --------------------------------------


def test_accept_adds_staff_and_marks_delegating(session, client_as) -> None:
    client, user = client_as("floor_staff")
    b = session.add_broadcast(staff_needed=2)

    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept")

    assert response.status_code == 200
    body = response.json()
    assert body["acceptedCount"] == 1
    assert body["acceptedStaff"] == [
        {"staffId": str(user.id), "name": user.full_name, "completedAt": None}
    ]
    assert user.status == "delegating"


def test_accept_twice_is_idempotent(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    b = session.add_broadcast()
    client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept")

    again = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept")

    assert again.status_code == 200
    assert again.json()["acceptedCount"] == 1
    assert len(session.responses) == 1


def test_accept_expired_is_409_expired_and_closes(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    b = session.add_broadcast(expires_in=-1)

    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept")

    _err(response, 409, "expired")
    assert b.status == "closed"
    assert session.commits == 1


def test_accept_when_slots_full_is_409(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    b = session.add_broadcast(staff_needed=1)
    other = session.add_user(fake_user("floor_staff"))
    session.add(BroadcastResponse(broadcast_id=b.broadcast_id, staff_id=other.id))

    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept")

    _err(response, 409, "slots_full")
    assert len(session.responses) == 1


def test_accept_while_delegating_is_409(session, client_as) -> None:
    client, user = client_as("floor_staff")
    user.status = "delegating"
    b = session.add_broadcast()

    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept")

    _err(response, 409, "already_delegating")


def test_accept_unknown_broadcast_is_404(client_as) -> None:
    client, _ = client_as("floor_staff")
    _err(client.post(f"/api/v1/broadcasts/{uuid4()}/accept"), 404, "not_found")


@pytest.mark.parametrize("role", ["ops_manager", "admin"])
def test_only_floor_staff_can_accept(session, client_as, role) -> None:
    client, _ = client_as(role)
    b = session.add_broadcast()
    _err(client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept"), 403, "wrong_role")


# --- POST /api/v1/broadcasts/{id}/complete ------------------------------------


def test_complete_marks_done_and_idle(session, client_as) -> None:
    client, user = client_as("floor_staff")
    b = session.add_broadcast()
    client.post(f"/api/v1/broadcasts/{b.broadcast_id}/accept")

    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/complete")

    assert response.status_code == 200
    assert response.json()["completedCount"] == 1
    assert response.json()["acceptedStaff"][0]["completedAt"].endswith("Z")
    assert user.status == "idle"


def test_complete_without_accepting_is_403_not_your_response(
    session, client_as
) -> None:
    client, _ = client_as("floor_staff")
    b = session.add_broadcast()
    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/complete")
    _err(response, 403, "not_your_response")


@pytest.mark.parametrize("role", ["ops_manager", "admin"])
def test_only_floor_staff_can_complete(session, client_as, role) -> None:
    client, _ = client_as(role)
    b = session.add_broadcast()
    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/complete")
    _err(response, 403, "wrong_role")


# --- POST /api/v1/broadcasts/{id}/finish --------------------------------------


def test_finish_after_everyone_completed(session, client_as) -> None:
    client, _ = client_as("ops_manager")
    b = session.add_broadcast()
    staff = session.add_user(fake_user("floor_staff"))
    session.add(
        BroadcastResponse(
            broadcast_id=b.broadcast_id,
            staff_id=staff.id,
            completed_at=datetime.now(UTC),
        )
    )

    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/finish")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"broadcastId", "status", "finishedAt"}
    assert body["status"] == "finished"
    assert body["finishedAt"].endswith("Z")
    assert b.status == "finished"


@pytest.mark.parametrize("completed", [None, "none-accepted"])
def test_finish_before_all_done_is_409(session, client_as, completed) -> None:
    client, _ = client_as("ops_manager")
    b = session.add_broadcast()
    if completed is None:  # one accepted, not completed
        staff = session.add_user(fake_user("floor_staff"))
        session.add(BroadcastResponse(broadcast_id=b.broadcast_id, staff_id=staff.id))

    response = client.post(f"/api/v1/broadcasts/{b.broadcast_id}/finish")

    _err(response, 409, "not_all_done")
    assert b.status == "open"


@pytest.mark.parametrize("role", ["floor_staff", "admin"])
def test_floor_staff_cannot_finish(session, client_as, role) -> None:
    client, _ = client_as(role)
    b = session.add_broadcast()
    _err(client.post(f"/api/v1/broadcasts/{b.broadcast_id}/finish"), 403, "wrong_role")


# --- GET /api/v1/staff/{staffId} ----------------------------------------------


@pytest.mark.parametrize("role", ["floor_staff", "ops_manager", "admin"])
def test_staff_status_any_role(session, client_as, role) -> None:
    client, _ = client_as(role)
    staff = session.add_user(fake_user("floor_staff"))
    staff.status = "delegating"

    response = client.get(f"/api/v1/staff/{staff.id}")

    assert response.status_code == 200
    assert response.json() == {
        "staffId": str(staff.id),
        "name": staff.full_name,
        "status": "delegating",
    }


def test_staff_unknown_is_404(client_as) -> None:
    client, _ = client_as("floor_staff")
    _err(client.get(f"/api/v1/staff/{uuid4()}"), 404, "not_found")


def test_staff_requires_auth() -> None:
    _err(TestClient(app).get(f"/api/v1/staff/{uuid4()}"), 401, "unauthenticated")


# --- DTOs and removed routes --------------------------------------------------


def test_datetimes_are_iso8601_utc() -> None:
    """Review Focus 5: Pydantic encodes UTC datetimes with a Z suffix."""
    out = BroadcastOut(
        broadcastId=uuid4(),
        zoneId="z1",
        status="open",
        staffNeeded=1,
        expiresAt=datetime(2026, 11, 3, 19, 0, tzinfo=UTC),
        acceptedCount=1,
        completedCount=1,
        acceptedStaff=[
            AcceptedStaffOut(
                staffId=uuid4(), name="A", completedAt=datetime(2026, 11, 3, 19, 0)
            )
        ],
    ).model_dump(mode="json")
    assert out["expiresAt"] == "2026-11-03T19:00:00Z"
    assert out["acceptedStaff"][0]["completedAt"] == "2026-11-03T19:00:00Z"
    finish = FinishOut(
        broadcastId=uuid4(), status="finished", finishedAt=datetime(2026, 1, 1)
    ).model_dump(mode="json")
    assert finish["finishedAt"].endswith("Z")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/dispatch/broadcasts/current?zoneId=z1"),
        ("POST", "/api/v1/dispatch/broadcasts"),
        ("POST", f"/api/v1/dispatch/broadcasts/{uuid4()}/respond"),
        ("POST", f"/api/v1/dispatch/broadcasts/{uuid4()}/complete"),
        ("POST", f"/api/v1/dispatch/broadcasts/{uuid4()}/finish"),
        ("GET", f"/api/v1/dispatch/staff/{uuid4()}"),
    ],
)
def test_old_dispatch_routes_are_gone(client_as, method, path) -> None:
    client, _ = client_as("ops_manager")
    _err(client.request(method, path), 404, "not_found")
