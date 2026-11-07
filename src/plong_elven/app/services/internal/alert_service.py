"""Alert service for evaluating alert rules and sending notifications on status changes.

This module implements the AlertRule seam (SOLID Open/Closed principle): new alert
types can be added by implementing AlertRule and adding to the rules list, without
modifying existing rule classes.

Alert cooldowns prevent notification spam when counts hover near thresholds. A count
hovering right at the threshold (e.g. 9/10/11 people) flips the classified status
between safe and crowded on every detection, and each re-entry into "crowded" is a
"transition" that would otherwise re-fire a push. The cooldown throttles that.
"""

from datetime import datetime, timedelta

from plong_elven.app.db.models import Zone
from plong_elven.app.services.internal.notification_service import NotificationService
from plong_elven.app.services.ports import Alert, AlertRule
from plong_elven.core.helpers.zone_status import classify
from plong_elven.core.state.live_state import LiveState


class StatusChangeRule:
    """Alert rule that fires when a zone transitions into a specific status.

    The 'crowded' alert is one instance of this class.
    """

    def __init__(
        self,
        status: str,
        cooldown: timedelta | None,
        audience_role: str,
        title: str,
        body_template: str,
    ) -> None:
        """Initialize a status change rule.

        Args:
            status: The status that triggers this alert (e.g. "crowded")
            cooldown: Minimum time between alerts of this type for the same zone,
                      or None if no cooldown applies
            audience_role: The role name whose users should receive this alert
            title: Push notification title
            body_template: Body template with {zone} and {count} placeholders
        """
        self._status = status
        self._cooldown = cooldown
        self._audience_role = audience_role
        self._title = title
        self._body_template = body_template

    def evaluate(
        self, zone: Zone, count: int, previous_status: str | None
    ) -> Alert | None:
        """Evaluate if an alert should fire for the given zone and count.

        Returns an Alert only when the zone newly transitions into this rule's
        target status (not when it stays in the same status).
        """
        current_status = classify(count, zone.threshold)
        if current_status != self._status:
            return None
        if current_status == previous_status:
            return None
        return Alert(
            key=self._status,
            cooldown=self._cooldown,
            audience_role=self._audience_role,
            title=self._title,
            body=self._body_template.format(zone=zone.name, count=count),
        )


def default_alert_rules() -> list[AlertRule]:
    """Return the default alert rules: one 'crowded' rule (5 min cooldown) that
    pushes to ops managers when a zone reaches its threshold."""
    return [
        StatusChangeRule(
            status="crowded",
            cooldown=timedelta(minutes=5),
            audience_role="ops_manager",
            title="Zone crowded — act now",
            body_template="{zone} is crowded ({count} people). Broadcast a task now.",
        ),
    ]


class AlertService:
    """Service for evaluating alert rules and sending notifications.

    Runs each rule in the provided list against zone count updates and sends
    notifications through the NotificationService when rules fire. Manages
    cooldown state to prevent notification spam.
    """

    def __init__(
        self,
        notifier: NotificationService,
        rules: list[AlertRule],
        live: LiveState,
    ) -> None:
        """Initialize the alert service.

        Args:
            notifier: Service for sending push notifications
            rules: List of alert rules to evaluate on each count update
            live: Live state holding last_status and last_alert_sent_at
        """
        self._notifier = notifier
        self._rules = rules
        self._live = live

    async def on_count(self, zone: Zone, count: int, now: datetime) -> None:
        """Process a count update for a zone and send alerts if rules fire.

        Algorithm (preserving api/detections.py:131-144):
        1. Read previous status from live state
        2. Classify and store new status
        3. For each rule, if it returns an alert:
           - Skip if cooldown applies and not enough time has passed
           - Otherwise store the timestamp and send the notification
        """
        zone_id = zone.zone_id
        previous = self._live.last_status.get(zone_id)
        current_status = classify(count, zone.threshold)
        self._live.last_status[zone_id] = current_status

        for rule in self._rules:
            alert = rule.evaluate(zone, count, previous)
            if alert is None:
                continue

            if alert.cooldown is not None:
                last_sent = self._live.last_alert_sent_at.get((zone_id, alert.key))
                if last_sent is not None and now - last_sent < alert.cooldown:
                    continue

            self._live.last_alert_sent_at[(zone_id, alert.key)] = now
            await self._notifier.notify_role(
                alert.audience_role, alert.title, alert.body
            )
