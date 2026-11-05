"""Task and staff routes.

`MemorySession` is a small in-memory stand-in for AsyncSession. It evaluates the
simple `where` / `order_by` / `limit` clauses TaskService builds, so the task
rules (zone active flag, slot limit, 20 s expiry, home-zone skip, accept /
complete / finish) run end to end through the HTTP routes without Postgres.
The row lock itself is proven on real Postgres in test_task_race_pg.py.
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
from plong_elven.db.models import (
    Task,
    TaskRespondent,
    TaskRespondentStatusId,
    TaskStatusId,
    User,
    UserStatusId,
    Zone,
)
from plong_elven.db.session import get_db
from plong_elven.schemas.tasks import AcceptedStaffOut, FinishOut, TaskOut

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

    def __iter__(self):
        return iter(self._rows)


class MemorySession:
    """Records statements and commits; stores zones, users, tasks, respondents."""

    def __init__(self) -> None:
        self.zones: dict[str, Zone] = {
            "z1": Zone(
                zone_id="z1",
                name="Main Lobby",
                polygon=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                threshold=10,
                is_active_task=False,
            )
        }
        self.users: dict[UUID, User] = {}
        self.tasks: list[Task] = []
        self.respondents: list[TaskRespondent] = []
        self.statements: list = []
        self.commits = 0

    # --- helpers for tests ---
    def add_user(self, user: User) -> User:
        self.users[user.user_id] = user
        return user

    def add_task(
        self,
        *,
        zone_id: str = "z1",
        status: TaskStatusId = TaskStatusId.OPEN,
        total_staff: int = 2,
        expires_in: float = 20,
        created_at: datetime | None = None,
    ) -> Task:
        """A task as a broadcast would leave it: the zone is marked active."""
        now = datetime.now(UTC)
        t = Task(
            task_id=uuid4(),
            zone_id=zone_id,
            task_status_id=status,
            total_staff=total_staff,
            instruction="Guide people to exit B",
            created_at=created_at or now,
            expires_at=now + timedelta(seconds=expires_in),
        )
        self.tasks.append(t)
        if status != TaskStatusId.FINISHED:
            self.zones[zone_id].is_active_task = True
        return t

    def add_respondent(self, task: Task, staff: User, completed: bool = False):
        now = datetime.now(UTC)
        self.add(
            TaskRespondent(
                task_id=task.task_id,
                staff_id=staff.user_id,
                task_respondent_status_id=(
                    TaskRespondentStatusId.COMPLETED
                    if completed
                    else TaskRespondentStatusId.ONGOING
                ),
                responded_at=now,
                completed_at=now if completed else None,
            )
        )

    def _table_rows(self, stmt) -> list:
        entity = stmt.column_descriptions[0]["entity"]
        if entity is None:  # select(func.count()).select_from(...)
            name = stmt.get_final_froms()[0].name
            return {"task": self.tasks, "task_respondents": self.respondents}[name]
        if entity is Zone:
            return sorted(self.zones.values(), key=lambda z: z.zone_id)
        return {Task: self.tasks, TaskRespondent: self.respondents}[entity]

    def _select(self, stmt) -> list:
        rows = [r for r in self._table_rows(stmt) if _matches(r, stmt.whereclause)]
        for clause in reversed(stmt._order_by_clauses):
            desc = getattr(clause, "modifier", None) is operators.desc_op
            key = clause.element.key if desc else clause.key
            rows.sort(key=lambda r, k=key: getattr(r, k), reverse=desc)
        if stmt._limit_clause is not None:
            rows = rows[: stmt._limit]
        return rows

    # --- AsyncSession surface used by TaskService ---
    async def get(self, model, key, **_kw):
        if model is Zone:
            return self.zones.get(key)
        if model is User:
            return self.users.get(key)
        if model is TaskRespondent:
            for r in self.respondents:
                if r.task_id == key["task_id"] and r.staff_id == key["staff_id"]:
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
        # select(TaskRespondent, User).join(User, ...).where(...)
        self.statements.append(stmt)
        rows = [r for r in self.respondents if _matches(r, stmt.whereclause)]
        rows.sort(key=lambda r: r.responded_at)
        return _Rows([(r, self.users[r.staff_id]) for r in rows])

    def add(self, obj) -> None:
        now = datetime.now(UTC)
        if isinstance(obj, Task):
            obj.task_id = obj.task_id or uuid4()
            obj.created_at = obj.created_at or now
            self.tasks.append(obj)
        elif isinstance(obj, TaskRespondent):
            obj.responded_at = obj.responded_at or now
            self.respondents.append(obj)
        elif isinstance(obj, Zone):
            obj.is_active_task = bool(obj.is_active_task)
            self.zones[obj.zone_id] = obj

    async def delete(self, obj) -> None:
        if isinstance(obj, Zone):
            del self.zones[obj.zone_id]

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass

    async def refresh(self, _obj) -> None:
        pass


class _RecordingNotifier:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple, dict]] = []

    async def notify_role(self, *args, **kwargs) -> None:
        self.calls.append((args, kwargs))


@pytest.fixture
def session() -> MemorySession:
    return MemorySession()


@pytest.fixture
def notifier() -> _RecordingNotifier:
    return _RecordingNotifier()


@pytest.fixture
def client_as(session, notifier):
    """Return a factory: client_as(role) -> (TestClient, user) bound to `session`."""

    def _make(role: str) -> tuple[TestClient, User]:
        user = session.add_user(fake_user(role))
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_notification_service] = lambda: notifier
        return TestClient(app), user

    yield _make
    app.dependency_overrides.clear()


def _err(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


BODY = {"zoneId": "z1", "totalStaff": 2, "instruction": "Guide people to exit B"}


# --- GET /api/v1/tasks --------------------------------------------------------


def test_expired_open_task_is_closed_and_filtered(session, client_as) -> None:
    """Closed and committed first, then excluded from status=open."""
    client, _ = client_as("floor_staff")
    t = session.add_task(expires_in=-1)

    response = client.get("/api/v1/tasks?zoneId=z1&status=open")

    assert response.status_code == 200
    assert response.json() == []
    assert t.task_status_id == TaskStatusId.CLOSED
    assert session.commits == 1


def test_expired_task_with_nobody_frees_the_zone(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    session.add_task(expires_in=-1)

    client.get("/api/v1/tasks")

    assert session.zones["z1"].is_active_task is False


def test_expired_task_with_respondents_keeps_the_zone_active(
    session, client_as
) -> None:
    client, _ = client_as("floor_staff")
    t = session.add_task(expires_in=-1)
    session.add_respondent(t, session.add_user(fake_user("floor_staff")))

    client.get("/api/v1/tasks")

    assert t.task_status_id == TaskStatusId.CLOSED
    assert session.zones["z1"].is_active_task is True


def test_expired_sweep_respects_zone_filter(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    session.zones["z2"] = Zone(zone_id="z2", name="Z2", polygon=[], threshold=5)
    other = session.add_task(zone_id="z2", expires_in=-1)

    response = client.get("/api/v1/tasks?zoneId=z1&status=open")

    assert response.status_code == 200
    assert other.task_status_id == TaskStatusId.OPEN


def test_list_without_status_returns_newest_first_including_closed(
    session, client_as
) -> None:
    client, _ = client_as("floor_staff")
    now = datetime.now(UTC)
    old = session.add_task(
        status=TaskStatusId.CLOSED, created_at=now - timedelta(minutes=5)
    )
    new = session.add_task(created_at=now)

    response = client.get("/api/v1/tasks?zoneId=z1")

    ids = [t["taskId"] for t in response.json()]
    assert ids == [str(new.task_id), str(old.task_id)]
    assert response.json()[1]["status"] == "closed"


def test_open_lookup_returns_the_open_task(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    t = session.add_task(total_staff=3)

    response = client.get("/api/v1/tasks?zoneId=z1&status=open")

    assert response.status_code == 200
    (body,) = response.json()
    assert body == {
        "taskId": str(t.task_id),
        "zoneId": "z1",
        "status": "open",
        "totalStaff": 3,
        "instruction": "Guide people to exit B",
        "expiresAt": body["expiresAt"],
        "acceptedCount": 0,
        "completedCount": 0,
        "acceptedStaff": [],
    }
    assert body["expiresAt"].endswith("Z")


def test_list_empty_is_200_empty_list(client_as) -> None:
    client, _ = client_as("ops_manager")
    response = client.get("/api/v1/tasks?zoneId=nowhere&status=open")
    assert response.status_code == 200
    assert response.json() == []


def test_limit_bounds(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    for _ in range(3):
        session.add_task(status=TaskStatusId.FINISHED)

    assert len(client.get("/api/v1/tasks?limit=2").json()) == 2
    _err(client.get("/api/v1/tasks?limit=0"), 422, "validation_error")
    _err(client.get("/api/v1/tasks?limit=51"), 422, "validation_error")
    _err(client.get("/api/v1/tasks?status=bogus"), 422, "validation_error")


def test_list_requires_auth() -> None:
    _err(TestClient(app).get("/api/v1/tasks"), 401, "unauthenticated")


# --- POST /api/v1/tasks/broadcast ---------------------------------------------


def test_broadcast_requires_manager_role(client_as) -> None:
    client, _ = client_as("floor_staff")
    response = client.post("/api/v1/tasks/broadcast", json=BODY)
    _err(response, 403, "wrong_role")
    assert "ops_manager" in response.json()["error"]["message"]


def test_admin_cannot_broadcast(client_as) -> None:
    client, _ = client_as("admin")
    _err(client.post("/api/v1/tasks/broadcast", json=BODY), 403, "wrong_role")


def test_broadcast_returns_201_and_marks_zone_active(
    session, client_as, notifier
) -> None:
    client, _ = client_as("ops_manager")

    response = client.post("/api/v1/tasks/broadcast", json=BODY)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "open"
    assert body["totalStaff"] == 2
    assert body["instruction"] == "Guide people to exit B"
    expires = datetime.fromisoformat(body["expiresAt"])
    assert timedelta(seconds=18) < expires - datetime.now(UTC) <= timedelta(seconds=20)
    assert session.zones["z1"].is_active_task is True


def test_broadcast_pushes_to_staff_outside_the_zone(client_as, notifier) -> None:
    client, _ = client_as("ops_manager")

    client.post("/api/v1/tasks/broadcast", json=BODY)

    ((args, kwargs),) = notifier.calls
    assert args == ("floor_staff", "New task", "Main Lobby: Guide people to exit B")
    assert kwargs == {"exclude_zone_id": "z1"}


@pytest.mark.parametrize(
    "body",
    [
        {"zoneId": "z1", "totalStaff": 0, "instruction": "x"},
        {"zoneId": "z1", "totalStaff": 1},
        {"zoneId": "z1", "totalStaff": 1, "instruction": ""},
        {"zoneId": "z1", "totalStaff": 1, "instruction": "   "},
        {"zoneId": "z1", "totalStaff": 1, "instruction": "x" * 51},
    ],
)
def test_broadcast_rejects_bad_body(client_as, body) -> None:
    client, _ = client_as("ops_manager")
    _err(client.post("/api/v1/tasks/broadcast", json=body), 422, "validation_error")


def test_broadcast_accepts_a_50_char_instruction(client_as) -> None:
    client, _ = client_as("ops_manager")
    body = {**BODY, "instruction": "x" * 50}
    assert client.post("/api/v1/tasks/broadcast", json=body).status_code == 201


def test_broadcast_unknown_zone_is_404(client_as) -> None:
    client, _ = client_as("ops_manager")
    body = {**BODY, "zoneId": "nope"}
    _err(client.post("/api/v1/tasks/broadcast", json=body), 404, "not_found")


def test_broadcast_while_zone_active_is_409(session, client_as) -> None:
    client, _ = client_as("ops_manager")
    session.add_task()

    response = client.post("/api/v1/tasks/broadcast", json=BODY)

    assert response.json() == {
        "error": {
            "code": "broadcast_open",
            "message": "This zone already has an active task.",
        }
    }
    assert response.status_code == 409


def test_broadcast_blocked_while_closed_task_has_unfinished_staff(
    session, client_as
) -> None:
    """Closed for new acceptances, but staff are still on it: zone stays active."""
    client, _ = client_as("ops_manager")
    t = session.add_task(status=TaskStatusId.CLOSED, expires_in=-60)
    session.add_respondent(t, session.add_user(fake_user("floor_staff")))

    _err(client.post("/api/v1/tasks/broadcast", json=BODY), 409, "broadcast_open")


def test_broadcast_after_expired_task_with_nobody(session, client_as) -> None:
    client, _ = client_as("ops_manager")
    old = session.add_task(expires_in=-1)

    response = client.post("/api/v1/tasks/broadcast", json=BODY)

    assert response.status_code == 201
    assert old.task_status_id == TaskStatusId.CLOSED
    assert response.json()["taskId"] != str(old.task_id)


def test_broadcast_after_finish_is_allowed(session, client_as) -> None:
    client, _ = client_as("ops_manager")
    session.add_task(status=TaskStatusId.FINISHED)

    assert client.post("/api/v1/tasks/broadcast", json=BODY).status_code == 201


# --- POST /api/v1/tasks/{id}/accept -------------------------------------------


def test_accept_adds_staff_and_marks_delegating(session, client_as) -> None:
    client, user = client_as("floor_staff")
    t = session.add_task(total_staff=2)

    response = client.post(f"/api/v1/tasks/{t.task_id}/accept")

    assert response.status_code == 200
    body = response.json()
    assert body["acceptedCount"] == 1
    assert body["acceptedStaff"] == [
        {
            "staffId": str(user.user_id),
            "name": user.full_name,
            "status": "ongoing",
            "completedAt": None,
        }
    ]
    assert user.user_status_id == UserStatusId.DELEGATING


def test_accept_twice_is_idempotent(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    t = session.add_task()
    client.post(f"/api/v1/tasks/{t.task_id}/accept")

    again = client.post(f"/api/v1/tasks/{t.task_id}/accept")

    assert again.status_code == 200
    assert again.json()["acceptedCount"] == 1
    assert len(session.respondents) == 1


def test_accept_expired_is_409_expired_and_closes(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    t = session.add_task(expires_in=-1)

    response = client.post(f"/api/v1/tasks/{t.task_id}/accept")

    _err(response, 409, "expired")
    assert t.task_status_id == TaskStatusId.CLOSED
    assert session.commits == 1


@pytest.mark.parametrize("status", [TaskStatusId.CLOSED, TaskStatusId.FINISHED])
def test_accept_not_open_is_409_expired(session, client_as, status) -> None:
    """A task already closed (e.g. by a list sweep) or finished takes no one,
    even with free slots and a future expiresAt."""
    client, user = client_as("floor_staff")
    t = session.add_task(status=status, total_staff=2)

    response = client.post(f"/api/v1/tasks/{t.task_id}/accept")

    _err(response, 409, "expired")
    assert session.respondents == []
    assert user.user_status_id == UserStatusId.IDLE
    assert t.task_status_id == status


def test_accept_in_own_home_zone_is_409(session, client_as) -> None:
    client, user = client_as("floor_staff")
    user.zone_id = "z1"
    t = session.add_task()

    _err(client.post(f"/api/v1/tasks/{t.task_id}/accept"), 409, "own_zone")
    assert session.respondents == []


def test_accept_from_another_home_zone_is_allowed(session, client_as) -> None:
    client, user = client_as("floor_staff")
    user.zone_id = "z2"
    t = session.add_task()

    assert client.post(f"/api/v1/tasks/{t.task_id}/accept").status_code == 200


def test_accept_when_slots_full_is_409(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    t = session.add_task(total_staff=1)
    session.add_respondent(t, session.add_user(fake_user("floor_staff")))

    response = client.post(f"/api/v1/tasks/{t.task_id}/accept")

    _err(response, 409, "slots_full")
    assert len(session.respondents) == 1


def test_accept_while_delegating_is_409(session, client_as) -> None:
    client, user = client_as("floor_staff")
    user.user_status_id = UserStatusId.DELEGATING
    t = session.add_task()

    _err(client.post(f"/api/v1/tasks/{t.task_id}/accept"), 409, "already_delegating")


def test_accept_unknown_task_is_404(client_as) -> None:
    client, _ = client_as("floor_staff")
    _err(client.post(f"/api/v1/tasks/{uuid4()}/accept"), 404, "not_found")


@pytest.mark.parametrize("role", ["ops_manager", "admin"])
def test_only_floor_staff_can_accept(session, client_as, role) -> None:
    client, _ = client_as(role)
    t = session.add_task()
    _err(client.post(f"/api/v1/tasks/{t.task_id}/accept"), 403, "wrong_role")


# --- POST /api/v1/tasks/{id}/complete -----------------------------------------


def test_complete_marks_done_and_idle(session, client_as) -> None:
    client, user = client_as("floor_staff")
    t = session.add_task()
    client.post(f"/api/v1/tasks/{t.task_id}/accept")

    response = client.post(f"/api/v1/tasks/{t.task_id}/complete")

    assert response.status_code == 200
    body = response.json()
    assert body["completedCount"] == 1
    assert body["acceptedStaff"][0]["status"] == "completed"
    assert body["acceptedStaff"][0]["completedAt"].endswith("Z")
    assert user.user_status_id == UserStatusId.IDLE


def test_repeat_complete_does_not_free_a_user_on_another_task(
    session, client_as
) -> None:
    client, user = client_as("floor_staff")
    done = session.add_task(status=TaskStatusId.FINISHED)
    session.add_respondent(done, user, completed=True)
    user.user_status_id = UserStatusId.DELEGATING  # now on a different task

    response = client.post(f"/api/v1/tasks/{done.task_id}/complete")

    assert response.status_code == 200
    assert user.user_status_id == UserStatusId.DELEGATING


def test_complete_without_accepting_is_403_not_your_response(
    session, client_as
) -> None:
    client, _ = client_as("floor_staff")
    t = session.add_task()
    response = client.post(f"/api/v1/tasks/{t.task_id}/complete")
    _err(response, 403, "not_your_response")


@pytest.mark.parametrize("role", ["ops_manager", "admin"])
def test_only_floor_staff_can_complete(session, client_as, role) -> None:
    client, _ = client_as(role)
    t = session.add_task()
    _err(client.post(f"/api/v1/tasks/{t.task_id}/complete"), 403, "wrong_role")


# --- POST /api/v1/tasks/{id}/finish -------------------------------------------


def test_finish_after_everyone_completed_frees_the_zone(session, client_as) -> None:
    client, _ = client_as("ops_manager")
    t = session.add_task()
    session.add_respondent(t, session.add_user(fake_user("floor_staff")), True)

    response = client.post(f"/api/v1/tasks/{t.task_id}/finish")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"taskId", "status", "finishedAt"}
    assert body["status"] == "finished"
    assert body["finishedAt"].endswith("Z")
    assert t.task_status_id == TaskStatusId.FINISHED
    assert session.zones["z1"].is_active_task is False


@pytest.mark.parametrize("accepted", [True, False])
def test_finish_before_all_done_is_409(session, client_as, accepted) -> None:
    client, _ = client_as("ops_manager")
    t = session.add_task()
    if accepted:  # one accepted, not completed
        session.add_respondent(t, session.add_user(fake_user("floor_staff")))

    response = client.post(f"/api/v1/tasks/{t.task_id}/finish")

    _err(response, 409, "not_all_done")
    assert t.task_status_id == TaskStatusId.OPEN
    assert session.zones["z1"].is_active_task is True


@pytest.mark.parametrize("role", ["floor_staff", "admin"])
def test_floor_staff_cannot_finish(session, client_as, role) -> None:
    client, _ = client_as(role)
    t = session.add_task()
    _err(client.post(f"/api/v1/tasks/{t.task_id}/finish"), 403, "wrong_role")


# --- GET /api/v1/staff/{staffId} ----------------------------------------------


@pytest.mark.parametrize("role", ["floor_staff", "ops_manager", "admin"])
def test_staff_status_any_role(session, client_as, role) -> None:
    client, _ = client_as(role)
    staff = session.add_user(fake_user("floor_staff"))
    staff.user_status_id = UserStatusId.DELEGATING
    staff.zone_id = "z1"

    response = client.get(f"/api/v1/staff/{staff.user_id}")

    assert response.status_code == 200
    assert response.json() == {
        "staffId": str(staff.user_id),
        "name": staff.full_name,
        "status": "delegating",
        "zoneId": "z1",
    }


def test_staff_unknown_is_404(client_as) -> None:
    client, _ = client_as("floor_staff")
    _err(client.get(f"/api/v1/staff/{uuid4()}"), 404, "not_found")


def test_staff_requires_auth() -> None:
    _err(TestClient(app).get(f"/api/v1/staff/{uuid4()}"), 401, "unauthenticated")


# --- DTOs and removed routes --------------------------------------------------


def test_datetimes_are_iso8601_utc() -> None:
    """Pydantic encodes UTC datetimes with a Z suffix."""
    out = TaskOut(
        taskId=uuid4(),
        zoneId="z1",
        status="open",
        totalStaff=1,
        instruction="x",
        expiresAt=datetime(2026, 11, 3, 19, 0, tzinfo=UTC),
        acceptedCount=1,
        completedCount=1,
        acceptedStaff=[
            AcceptedStaffOut(
                staffId=uuid4(),
                name="A",
                status="completed",
                completedAt=datetime(2026, 11, 3, 19, 0),
            )
        ],
    ).model_dump(mode="json")
    assert out["expiresAt"] == "2026-11-03T19:00:00Z"
    assert out["acceptedStaff"][0]["completedAt"] == "2026-11-03T19:00:00Z"
    finish = FinishOut(
        taskId=uuid4(), status="finished", finishedAt=datetime(2026, 1, 1)
    ).model_dump(mode="json")
    assert finish["finishedAt"].endswith("Z")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/broadcasts"),
        ("POST", "/api/v1/broadcasts"),
        ("POST", f"/api/v1/broadcasts/{uuid4()}/accept"),
        ("POST", f"/api/v1/broadcasts/{uuid4()}/complete"),
        ("POST", f"/api/v1/broadcasts/{uuid4()}/finish"),
        ("GET", "/api/v1/dispatch/broadcasts/current?zoneId=z1"),
    ],
)
def test_old_broadcast_routes_are_gone(client_as, method, path) -> None:
    client, _ = client_as("ops_manager")
    _err(client.request(method, path), 404, "not_found")
