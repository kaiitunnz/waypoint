"""Backend-neutral notification contracts.

These types are the seam between notification *producers* (the inbox and
session-interaction mappers in :mod:`waypoint.notifications.render`) and
notification *channels* (Telegram today, WhatsApp/Slack later). Nothing here
references a coding-agent backend or a specific message service.
"""

from datetime import datetime
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

IntentKind = Literal["inbox", "approval", "question", "plan_approval"]


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    # Primary content (markdown/plan/question body) renders as a blockquote;
    # metadata lines (sender/session) render plain.
    quote: bool = False


class ChoiceItem(BaseModel):
    label: str
    description: str | None = None


class ChoiceListBlock(BaseModel):
    type: Literal["choice_list"] = "choice_list"
    label: str | None = None
    choices: list[ChoiceItem] = Field(default_factory=list)


PreviewBlock = Annotated[
    TextBlock | ChoiceListBlock,
    Field(discriminator="type"),
]


class NotificationIntent(BaseModel):
    """A rendered, JSON-serializable notification, independent of any channel.

    Built once (before persistence) so a retry sends the same preview even if
    the source session title or transcript changes afterwards.
    """

    model_config = ConfigDict(extra="forbid")

    dedupe_key: str
    kind: IntentKind
    subject: str
    # Relative Waypoint path (``/inbox/<id>`` or ``/session/<id>``); the origin
    # is joined from the validated ``public_base_url`` at render time so no host
    # or token is ever persisted in the intent.
    target_path: str
    source_session_id: str | None = None
    preview_blocks: list[PreviewBlock] = Field(default_factory=list)
    created_at: datetime


class OutboundMessage(BaseModel):
    """A channel-independent, ready-to-send message.

    Deliberately carries no approval/answer operation: v1 delivery is one-way,
    and the URL button opens Waypoint's own authenticated UI.
    """

    intent_id: str
    text: str
    url: str
    button_label: str


class DeliveryResult(BaseModel):
    status: Literal["sent", "retry", "failed"]
    # Seconds the channel asks us to wait before the next attempt (Telegram
    # ``retry_after``); ``None`` lets the worker pick its backoff.
    retry_after: float | None = None
    http_status: int | None = None
    # Non-secret, truncated reason for logs/status. Never a raw response body.
    error: str | None = None


class ChannelCapabilities(BaseModel):
    # v1 channels are outbound-only. A future two-way channel sets this True,
    # ingests verified updates, and maps a signed action id onto the existing
    # Waypoint approval/answer APIs.
    supports_inbound: bool = False


class ChannelHealth(BaseModel):
    channel_id: str
    available: bool
    # Redacted reason (e.g. "token environment variable is unset"); never the
    # variable value or a bot token.
    detail: str | None = None


@runtime_checkable
class NotificationChannel(Protocol):
    id: str
    capabilities: ChannelCapabilities

    async def start(self) -> ChannelHealth: ...

    async def send(self, message: OutboundMessage) -> DeliveryResult: ...

    async def stop(self) -> None: ...


# ── Inbound contract (defined for the future two-way design; unused in v1) ──


class InboundMessage(BaseModel):
    channel_id: str
    sender_id: str
    text: str | None = None
    # Opaque, signed id a future channel maps back to a Waypoint approval or
    # question; v1 never mints one.
    action_id: str | None = None
    received_at: datetime


@runtime_checkable
class InboundActionRouter(Protocol):
    async def route(self, message: InboundMessage) -> None: ...


# ── Durable-outbox row shape ──
#
# The storage layer stays notification-agnostic: it persists and returns
# ``(channel_id, dedupe_key, intent_json)`` primitives, and the service owns
# this model. Keeping the pydantic types out of storage avoids an import cycle
# (storage → notifications package → service → storage).


class DeliveryRecord(BaseModel):
    """A claimed outbox row the worker delivers."""

    id: str
    channel_id: str
    dedupe_key: str
    intent: NotificationIntent
    status: str
    attempts: int


class NotificationStatus(BaseModel):
    enabled: bool
    channels: list[ChannelHealth] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


def sanitize_metadata_interaction(value: Any) -> dict[str, Any] | None:
    """Return a metadata ``interaction`` dict when present and dict-shaped."""
    return value if isinstance(value, dict) else None
