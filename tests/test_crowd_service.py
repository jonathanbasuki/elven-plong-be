"""Tests for CrowdService and the background session fix (Task 1.7)."""

from datetime import UTC, datetime

import pytest
from conftest import TEST_INTERNAL_KEY, FakeSessionFactory
from fastapi.testclient import TestClient

from plong_elven.api.deps import get_push_sender, get_session_factory
from plong_elven.api.main import app
from plong_elven.db.models import Zone, ZoneCountHistory
from plong_elven.db.session import get_db
from plong_elven.schemas.zones import CrowdOut, HistoryBucketOut, ZoneOut
from plong_elven.services.crowd_service import CrowdService
from plong_elven.services.errors import AppError
from plong_elven.state.live_state import LiveState, get_live_state


class _NoZones:
    """Session that returns no zones."""

    async def get(self, model, key):
        return None


class _ExplodingSession:
    """Session that raises if used (proves background job uses its own session)."""

    async def get(self, *a, **k):
        raise AssertionError("background job used the request session")


async def _exploding_db():
    yield _ExplodingSession()


# --- KEY TEST: Review Focus #2 - background job must use its own session ---


def test_background_job_uses_its_own_session(fake_push_sender) -> None:
    """The background alert job must never touch the request session.

    This test overrides get_db to raise if called, while providing a separate
    session factory for the background job. The post must succeed, proving
    the background work opens its own session.
    """
    factory = FakeSessionFactory(_NoZones())
    live = LiveState()
    app.dependency_overrides.update(
        {
            get_db: _exploding_db,
            get_session_factory: lambda: factory,
            get_live_state: lambda: live,
            get_push_sender: lambda: fake_push_sender,
        }
    )
    try:
        response = TestClient(app).post(
            "/api/v1/cameras/cam1/detections",
            json={"counts": {"z1": 4}},
            headers={"X-Internal-Api-Key": TEST_INTERNAL_KEY},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert factory.opened == 1
    assert live.counts == {"z1": 4}
    assert "z1" in live.observed_at


# --- CrowdService unit tests ---

ZONE = Zone(
    zone_id="z1",
    name="Main Lobby",
    map_image_url="u",
    polygon=[],
    warning_threshold=5,
    crowded_threshold=10,
)


class _ZoneSession:
    """Minimal fake session for CrowdService tests."""

    def __init__(self, zone: Zone | None = ZONE):
        self._zone = zone
        self.added: list = []
        self.committed = 0

    async def get(self, model, key):
        if self._zone is None:
            return None
        if model is Zone and key == self._zone.zone_id:
            return self._zone
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        pass

    async def scalars(self, stmt):
        return type("R", (), {"all": lambda self: []})()


def test_record_live_updates_counts_and_observed_at() -> None:
    live = LiveState()
    svc = CrowdService(_ZoneSession(), live)
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    svc.record_live({"z1": 5, "z2": 3}, now)
    assert live.counts == {"z1": 5, "z2": 3}
    assert live.observed_at["z1"] == now
    assert live.observed_at["z2"] == now


@pytest.mark.asyncio
async def test_record_sample_creates_bucket_on_first_sample() -> None:
    live = LiveState()
    session = _ZoneSession()
    svc = CrowdService(session, live)
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    await svc.record_sample("z1", 5, now)
    assert "z1" in live.buckets
    assert live.buckets["z1"].n == 1
    assert live.buckets["z1"].peak == 5


@pytest.mark.asyncio
async def test_record_sample_accumulates_in_same_bucket() -> None:
    live = LiveState()
    session = _ZoneSession()
    svc = CrowdService(session, live)
    t0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    await svc.record_sample("z1", 4, t0)
    await svc.record_sample("z1", 8, t0)
    await svc.record_sample("z1", 6, t0)
    assert live.buckets["z1"].n == 3
    assert live.buckets["z1"].avg == 6.0
    assert live.buckets["z1"].peak == 8


@pytest.mark.asyncio
async def test_record_sample_flushes_old_bucket_to_history() -> None:
    live = LiveState()
    session = _ZoneSession()
    svc = CrowdService(session, live)
    t0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    t1 = datetime(2026, 11, 3, 12, 5, 1, tzinfo=UTC)  # next bucket
    await svc.record_sample("z1", 4, t0)
    await svc.record_sample("z1", 8, t0)
    # Sample in next window triggers flush
    await svc.record_sample("z1", 2, t1)
    assert session.committed == 1
    assert len(session.added) == 1
    assert isinstance(session.added[0], ZoneCountHistory)
    assert session.added[0].avg_count == 6.0
    assert session.added[0].peak_count == 8


@pytest.mark.asyncio
async def test_list_zones_returns_zone_payloads() -> None:
    class _MultiZoneSession:
        async def scalars(self, stmt):
            zones = [
                Zone(
                    zone_id="z1",
                    name="A",
                    map_image_url="u1",
                    polygon=[],
                    warning_threshold=5,
                    crowded_threshold=10,
                ),
                Zone(
                    zone_id="z2",
                    name="B",
                    map_image_url="u2",
                    polygon=[],
                    warning_threshold=3,
                    crowded_threshold=6,
                ),
            ]
            return type("R", (), {"all": lambda self: zones})()

    live = LiveState()
    live.counts["z1"] = 7
    svc = CrowdService(_MultiZoneSession(), live)
    result = await svc.list_zones()
    assert [z.zoneId for z in result] == ["z1", "z2"]
    assert result[0] == ZoneOut(zoneId="z1", name="A", mapImageUrl="u1", polygon=[])


@pytest.mark.asyncio
async def test_get_zone_returns_zone_without_live_fields() -> None:
    live = LiveState()
    live.counts["z1"] = 12
    svc = CrowdService(_ZoneSession(), live)
    result = await svc.get_zone("z1")
    assert result == ZoneOut(
        zoneId="z1", name="Main Lobby", mapImageUrl="u", polygon=[]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count", "status"),
    [(0, "safe"), (4, "safe"), (5, "warning"), (7, "warning"), (10, "crowded")],
)
async def test_crowd_classifies_live_count(count: int, status: str) -> None:
    live = LiveState()
    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    CrowdService(_ZoneSession(), live).record_live({"z1": count}, now)
    result = await CrowdService(_ZoneSession(), live).crowd("z1")
    assert result == CrowdOut(zoneId="z1", count=count, status=status, observedAt=now)


@pytest.mark.asyncio
async def test_crowd_before_first_detection_is_zero_safe_null() -> None:
    result = await CrowdService(_ZoneSession(), LiveState()).crowd("z1")
    assert result == CrowdOut(zoneId="z1", count=0, status="safe", observedAt=None)


@pytest.mark.asyncio
async def test_crowd_not_found_raises_app_error() -> None:
    with pytest.raises(AppError) as exc:
        await CrowdService(_ZoneSession(None), LiveState()).crowd("z9")
    assert exc.value.status_code == 404
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_get_zone_not_found_raises_app_error() -> None:
    svc = CrowdService(_ZoneSession(None), LiveState())
    with pytest.raises(AppError) as exc:
        await svc.get_zone("z9")
    assert exc.value.status_code == 404
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_history_returns_recent_buckets() -> None:
    class _HistorySession:
        async def scalars(self, stmt):
            rows = [
                ZoneCountHistory(
                    zone_id="z1",
                    bucket_start=datetime(2026, 11, 3, 12, 5, tzinfo=UTC),
                    avg_count=5.5,
                    peak_count=8,
                ),
                ZoneCountHistory(
                    zone_id="z1",
                    bucket_start=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
                    avg_count=3.0,
                    peak_count=4,
                ),
            ]
            return type("R", (), {"all": lambda self: rows})()

    svc = CrowdService(_HistorySession(), LiveState())
    result = await svc.history("z1", limit=12)
    assert result == [
        HistoryBucketOut(
            bucketStart=datetime(2026, 11, 3, 12, 5, tzinfo=UTC),
            avgCount=5.5,
            peakCount=8,
        ),
        HistoryBucketOut(
            bucketStart=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
            avgCount=3.0,
            peakCount=4,
        ),
    ]
