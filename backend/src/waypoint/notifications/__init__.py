"""Backend-neutral notification center.

Observes durable inbox creation and actionable session interactions, then
delivers a bounded, linked preview to configured outbound channels (Telegram in
v1). Producers are backend-neutral; channels are pluggable via
:mod:`waypoint.notifications.registry`.
"""

from waypoint.notifications.contracts import (
    ChannelHealth,
    DeliveryRecord,
    NotificationChannel,
    NotificationIntent,
    NotificationStatus,
    SuppressionReason,
)
from waypoint.notifications.render import (
    intent_from_event,
    intent_from_inbox_item,
    render_message,
)
from waypoint.notifications.service import NotificationService

__all__ = [
    "ChannelHealth",
    "DeliveryRecord",
    "NotificationChannel",
    "NotificationIntent",
    "NotificationService",
    "NotificationStatus",
    "SuppressionReason",
    "intent_from_event",
    "intent_from_inbox_item",
    "render_message",
]
