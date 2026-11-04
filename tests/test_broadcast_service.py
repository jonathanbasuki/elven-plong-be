# tests/test_broadcast_service.py
"""Tests for BroadcastService, including the FOR UPDATE lock on accept."""

from uuid import uuid4

import pytest
from conftest import fake_db, fake_user
from fastapi.testclient import TestClient

from plong_elven.api.deps import get_current_user
from plong_elven.api.main import app
from plong_elven.db.session import get_db
from plong_elven.services.broadcast_service import BroadcastService
from plong_elven.services.errors import AppError


class _CapturingSession:
    """Session that captures statements for assertion."""

    def __init__(self) -> None:
        self.statements: list = []

    async def scalar(self, stmt):
        self.statements.append(stmt)


@pytest.mark.asyncio
async def test_accept_locks_the_broadcast_row_first() -> None:
    """The accept path must use SELECT ... FOR UPDATE on the broadcast row."""
    session = _CapturingSession()
    with pytest.raises(AppError) as exc:
        await BroadcastService(session, notifier=None).accept(uuid4(), fake_user())
    assert exc.value.status_code == 404
    # The first statement must have FOR UPDATE
    stmt = session.statements[0]
    assert stmt._for_update_arg is not None


def test_role_check_runs_before_body_validation() -> None:
    """Review Focus 5: floor_staff POSTing invalid body now gets 403 (was 422)."""
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("floor_staff")
    try:
        # staffNeeded=0 is invalid (Pydantic ge=1), but role check runs first
        response = TestClient(app).post(
            "/api/v1/broadcasts", json={"zoneId": "z1", "staffNeeded": 0}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403  # was 422 before Stage 1; see Review Focus 5


def test_broadcast_service_does_not_import_fastapi() -> None:
    """Services stay framework-free: the module source never imports fastapi."""
    from pathlib import Path

    import plong_elven.services.broadcast_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "fastapi" not in source


@pytest.mark.asyncio
async def test_list_returns_dtos_newest_first() -> None:
    """BroadcastService.list returns BroadcastOut DTOs, not dicts."""
    from test_broadcasts import MemorySession

    from plong_elven.schemas.broadcasts import BroadcastOut

    session = MemorySession()
    b = session.add_broadcast()
    result = await BroadcastService(session, notifier=None).list("z1", "open", 20)
    assert result and all(isinstance(item, BroadcastOut) for item in result)
    assert result[0].broadcastId == b.broadcast_id
