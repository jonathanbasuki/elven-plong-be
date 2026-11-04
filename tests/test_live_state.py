# tests/test_live_state.py
from plong_elven.state.live_state import Bucket, LiveState, get_live_state, live_state


def test_fresh_instances_do_not_share_state() -> None:
    a, b = LiveState(), LiveState()
    a.counts["z1"] = 3
    a.frames["cam1"] = b"x"
    assert b.counts == {} and b.frames == {}


def test_provider_returns_the_singleton() -> None:
    assert get_live_state() is live_state


def test_bucket_average_and_peak() -> None:
    from datetime import UTC, datetime

    bucket = Bucket(start=datetime(2026, 1, 1, tzinfo=UTC))
    for n in (2, 6, 4):
        bucket.add(n)
    assert (bucket.avg, bucket.peak, bucket.n) == (4.0, 6, 3)
