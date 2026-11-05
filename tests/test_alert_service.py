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
ZONE = Zone(zone_id="z1", name="Main Lobby", polygon=[], threshold=10)


class _Tokens:
    async def scalars(self, stmt):
        return type("R", (), {"all": lambda self: ["tok-mgr"]})()


def _service(sender, rules=None, live=None):
    notifier = NotificationService(FakeSessionFactory(_Tokens()), sender)
    return AlertService(notifier, rules or default_alert_rules(), live or LiveState())


@pytest.mark.asyncio
async def test_reaching_threshold_pushes_managers(fake_push_sender) -> None:
    await _service(fake_push_sender).on_count(ZONE, 12, T0)
    assert fake_push_sender.calls == [
        (
            ["tok-mgr"],
            "Zone crowded — act now",
            "Main Lobby is crowded (12 people). Broadcast a task now.",
        )
    ]


@pytest.mark.asyncio
async def test_below_threshold_is_safe_and_silent(fake_push_sender) -> None:
    await _service(fake_push_sender).on_count(ZONE, 9, T0)
    assert fake_push_sender.calls == []


@pytest.mark.asyncio
async def test_same_status_does_not_repeat(fake_push_sender) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 12, T0)
    await svc.on_count(ZONE, 13, T0 + timedelta(seconds=1))
    assert len(fake_push_sender.calls) == 1


@pytest.mark.asyncio
async def test_flapping_back_into_crowded_within_cooldown_is_suppressed(
    fake_push_sender,
) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 10, T0)
    await svc.on_count(ZONE, 9, T0 + timedelta(seconds=1))
    await svc.on_count(ZONE, 10, T0 + timedelta(minutes=4))
    assert len(fake_push_sender.calls) == 1
    await svc.on_count(ZONE, 9, T0 + timedelta(minutes=5))
    await svc.on_count(ZONE, 10, T0 + timedelta(minutes=5, seconds=1))
    assert len(fake_push_sender.calls) == 2


def test_only_a_crowded_rule_by_default() -> None:
    (rule,) = default_alert_rules()
    assert rule.evaluate(ZONE, 10, "safe").key == "crowded"
    assert rule.evaluate(ZONE, 9, None) is None


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
