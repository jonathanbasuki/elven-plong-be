# tests/test_alert_service.py
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeSessionFactory

from plong_elven.db.models import Zone
from plong_elven.services.internal.alert_service import (
    AlertService,
    StatusChangeRule,
    default_alert_rules,
)
from plong_elven.services.internal.notification_service import NotificationService
from plong_elven.services.ports import Alert
from plong_elven.state.live_state import LiveState

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ZONE = Zone(
    zone_id="z1",
    name="Main Lobby",
    map_image_url="u",
    polygon=[],
    warning_threshold=5,
    crowded_threshold=10,
)


class _Tokens:
    async def scalars(self, stmt):
        return type("R", (), {"all": lambda self: ["tok-mgr"]})()


def _service(sender, rules=None, live=None):
    notifier = NotificationService(FakeSessionFactory(_Tokens()), sender)
    return AlertService(notifier, rules or default_alert_rules(), live or LiveState())


@pytest.mark.asyncio
async def test_entering_warning_pushes_managers(fake_push_sender) -> None:
    await _service(fake_push_sender).on_count(ZONE, 7, T0)
    assert fake_push_sender.calls == [
        (
            ["tok-mgr"],
            "Crowd warning",
            "Main Lobby is getting busy (7 people detected).",
        )
    ]


@pytest.mark.asyncio
async def test_same_status_does_not_repeat(fake_push_sender) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 7, T0)
    await svc.on_count(ZONE, 8, T0 + timedelta(seconds=1))
    assert len(fake_push_sender.calls) == 1


@pytest.mark.asyncio
async def test_flapping_back_into_warning_within_cooldown_is_suppressed(
    fake_push_sender,
) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 7, T0)
    await svc.on_count(ZONE, 2, T0 + timedelta(seconds=1))
    await svc.on_count(ZONE, 7, T0 + timedelta(minutes=4))
    assert len(fake_push_sender.calls) == 1
    await svc.on_count(ZONE, 2, T0 + timedelta(minutes=5))
    await svc.on_count(ZONE, 7, T0 + timedelta(minutes=5, seconds=1))
    assert len(fake_push_sender.calls) == 2


@pytest.mark.asyncio
async def test_escalation_to_crowded_is_never_throttled(fake_push_sender) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 7, T0)
    await svc.on_count(ZONE, 12, T0 + timedelta(seconds=1))
    assert fake_push_sender.calls[1][1] == "Zone crowded — act now"


@pytest.mark.asyncio
async def test_extra_rule_fires_without_editing_status_change_rule(
    fake_push_sender,
) -> None:
    class EveryCountRule:
        def evaluate(self, zone, count, previous_status):
            return Alert(
                key="every",
                cooldown=None,
                audience_role="floor_staff",
                title="Count",
                body=f"{zone.name}: {count}",
            )

    svc = _service(fake_push_sender, rules=[*default_alert_rules(), EveryCountRule()])
    await svc.on_count(ZONE, 1, T0)
    assert fake_push_sender.calls == [(["tok-mgr"], "Count", "Main Lobby: 1")]
    assert isinstance(default_alert_rules()[0], StatusChangeRule)
