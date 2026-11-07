"""Tests for TaskService, including the FOR UPDATE lock on accept."""

from uuid import uuid4

import pytest
from conftest import fake_db, fake_user
from fastapi.testclient import TestClient

from plong_elven.app.api.deps import get_current_user
from plong_elven.app.api.main import app
from plong_elven.app.db.session import get_db
from plong_elven.app.services.errors import AppError
from plong_elven.app.services.task_service import TaskService


class _CapturingSession:
    """Session that captures statements for assertion."""

    def __init__(self) -> None:
        self.statements: list = []

    async def scalar(self, stmt):
        self.statements.append(stmt)


@pytest.mark.asyncio
async def test_accept_locks_the_task_row_first() -> None:
    """The accept path must use SELECT ... FOR UPDATE on the task row."""
    session = _CapturingSession()
    with pytest.raises(AppError) as exc:
        await TaskService(session, notifier=None).accept(uuid4(), fake_user())
    assert exc.value.status_code == 404
    stmt = session.statements[0]
    assert stmt._for_update_arg is not None


def test_role_check_runs_before_body_validation() -> None:
    """floor_staff POSTing an invalid body gets 403, not 422."""
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("floor_staff")
    try:
        # totalStaff=0 and no instruction are invalid, but the role check runs first
        response = TestClient(app).post(
            "/api/v1/tasks/broadcast", json={"zoneId": "z1", "totalStaff": 0}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_task_service_does_not_import_fastapi() -> None:
    """Services stay framework-free: the module source never imports fastapi."""
    from pathlib import Path

    import plong_elven.app.services.task_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "fastapi" not in source


@pytest.mark.asyncio
async def test_list_returns_dtos_newest_first() -> None:
    """TaskService.list returns TaskOut DTOs, not dicts."""
    from test_tasks import MemorySession

    from plong_elven.app.schemas.tasks import TaskOut

    session = MemorySession()
    t = session.add_task()
    result = await TaskService(session, notifier=None).list("z1", ["open"], 20)
    assert result and all(isinstance(item, TaskOut) for item in result)
    assert result[0].taskId == t.task_id


def test_task_phase_is_ongoing_while_open_or_ongoing() -> None:
    from plong_elven.app.db.models import TaskStatusId
    from plong_elven.app.services.task_service import task_phase

    assert task_phase(TaskStatusId.OPEN) == "ongoing"
    assert task_phase(TaskStatusId.CLOSED) == "completed"
    assert task_phase(TaskStatusId.FINISHED) == "completed"
    assert task_phase(TaskStatusId.ONGOING) == "ongoing"
    assert task_phase(TaskStatusId.COMPLETE) == "completed"


def test_crowd_log_end_rules() -> None:
    from datetime import UTC, datetime, timedelta

    from plong_elven.app.db.models import Task
    from plong_elven.app.services.task_service import crowd_log_end

    t0 = datetime(2026, 11, 6, 17, 32, tzinfo=UTC)
    now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    done = Task(
        created_at=t0,
        expires_at=t0 + timedelta(seconds=20),
        finished_at=datetime(2026, 11, 6, 18, 12, tzinfo=UTC),
    )
    from uuid import uuid4

    from plong_elven.app.schemas.tasks import AcceptedStaffOut

    def person(completed_at):
        status = "ongoing" if completed_at is None else "completed"
        return AcceptedStaffOut(
            staffId=uuid4(), name="x", status=status, completedAt=completed_at
        )

    at = lambda hh, mm: datetime(2026, 11, 6, hh, mm, tzinfo=UTC)
    assert crowd_log_end(done, [person(at(18, 0))], now) == done.finished_at
    closed = Task(created_at=t0, expires_at=t0 + timedelta(seconds=20))
    # someone still working: up to now
    assert crowd_log_end(closed, [person(at(17, 50)), person(None)], now) == now
    # everyone completed but never finished: stop at the last completion
    both_done = [person(at(17, 50)), person(at(18, 5))]
    assert crowd_log_end(closed, both_done, now) == at(18, 5)
    assert crowd_log_end(closed, [], now) == closed.expires_at  # nobody came


@pytest.mark.asyncio
async def test_last_completion_queues_the_footage_job() -> None:
    """The footage job goes through `defer` (FastAPI BackgroundTasks), not a
    loose asyncio task that can be garbage-collected or lost."""
    from test_tasks import MemorySession

    from plong_elven.app.db.models import TaskStatusId, UserStatusId
    from plong_elven.app.services.footage_service import process_task_footage

    session = MemorySession()
    t = session.add_task()
    user = session.add_user(fake_user("floor_staff"))
    user.user_status_id = UserStatusId.DELEGATING
    session.add_respondent(t, user)
    deferred = []

    await TaskService(session, notifier=None).complete(
        t.task_id, user, lambda *args: deferred.append(args)
    )

    assert t.task_status_id == TaskStatusId.COMPLETE
    assert deferred == [(process_task_footage, t.task_id)]
