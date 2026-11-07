"""In-memory live state for crowd detection, frames, alerts, and history buckets.

All dicts are per-zone or per-camera key. A single LiveState instance is used
as a module singleton; tests can create isolated instances.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Bucket:
    """Accumulates detection samples within a 5-minute window for avg/peak history."""

    start: datetime
    sum_count: int = 0
    n: int = 0
    peak: int = 0

    def add(self, count: int) -> None:
        self.sum_count += count
        self.n += 1
        self.peak = max(self.peak, count)

    @property
    def avg(self) -> float:
        return self.sum_count / self.n if self.n else 0.0


@dataclass
class LiveState:
    """Holds all in-memory state that was previously in module globals across
    detections.py and stream.py. A fresh instance has empty dicts (tests use
    this for isolation); the module singleton is shared by all request handlers.
    """

    counts: dict[str, int] = field(default_factory=dict)
    observed_at: dict[str, datetime] = field(default_factory=dict)
    buckets: dict[str, Bucket] = field(default_factory=dict)
    last_status: dict[str, str] = field(default_factory=dict)
    last_alert_sent_at: dict[tuple[str, str], datetime] = field(default_factory=dict)
    frames: dict[str, bytes] = field(default_factory=dict)
    raw_frames: dict[str, bytes] = field(default_factory=dict)


# Module singleton used by routers.
live_state = LiveState()


def get_live_state() -> LiveState:
    """FastAPI dependency returning the singleton. Tests can override via
    app.dependency_overrides[get_live_state]."""
    return live_state
