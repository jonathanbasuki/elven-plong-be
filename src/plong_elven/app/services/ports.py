"""Protocol definitions for services and dependency injection seams.

These protocols define the contracts that implementations must follow,
allowing different implementations (real, fake) to be swapped for testing.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from plong_elven.app.db.models import Zone


@runtime_checkable
class PushSender(Protocol):
    """Contract for sending push notifications to device tokens.

    Implementations must be fire-and-forget: failures are logged, never raised.
    """

    async def send(self, tokens: list[str], title: str, body: str) -> None:
        """Send a push notification with the given title and body to all tokens."""
        ...


@dataclass(frozen=True)
class Alert:
    """An alert to send when a zone status changes.

    Attributes:
        key: Unique identifier for this alert type (e.g. "warning", "crowded")
        cooldown: Minimum time between alerts of the same key for the same zone,
                  or None if no cooldown applies
        audience_role: The role name whose users should receive this alert
        title: Push notification title
        body: Push notification body text
    """

    key: str
    cooldown: timedelta | None
    audience_role: str
    title: str
    body: str


class AlertRule(Protocol):
    """Contract for evaluating whether an alert should be sent for a zone.

    Implementations decide based on count, thresholds, and previous status.
    """

    def evaluate(
        self, zone: "Zone", count: int, previous_status: str | None
    ) -> Alert | None:
        """Evaluate if an alert should fire for the given zone and count.

        Args:
            zone: The zone being evaluated
            count: Current person count
            previous_status: Previous status string, or None if first observation

        Returns:
            An Alert to send, or None if no alert should fire
        """
        ...
