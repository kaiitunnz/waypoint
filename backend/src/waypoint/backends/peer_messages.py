"""Backend-neutral peer-message contract.

A peer message is input another agent session sends straight to this one,
bypassing Waypoint: a turn the agent starts on its own. It is stored as a
``USER_INPUT`` event marked ``metadata.kind == "peer_message"``, which the
frontend renders as a collapsible card labeled with its sender. Any agent plugin
may emit one; each owns how it detects the message and builds a
:class:`PeerMessage`, and :func:`peer_message_event` turns that into the event's
``(text, metadata)``.
"""

from dataclasses import dataclass
from typing import Any

PEER_MESSAGE_KIND = "peer_message"
PEER_MESSAGE_VERSION = 1


@dataclass(frozen=True)
class PeerMessage:
    channel: str  # cross_session | agent
    sender_address: str
    body: str
    sender_name: str | None = None
    sender_mode: str | None = None


def peer_message_event(message: PeerMessage) -> tuple[str, dict[str, Any]]:
    """Build the ``(text, metadata)`` for a peer-message ``USER_INPUT`` event."""
    payload: dict[str, Any] = {
        "version": PEER_MESSAGE_VERSION,
        "channel": message.channel,
        "sender_address": message.sender_address,
    }
    if message.sender_name:
        payload["sender_name"] = message.sender_name
    if message.sender_mode:
        payload["sender_mode"] = message.sender_mode
    return message.body, {
        "method": PEER_MESSAGE_KIND,
        "kind": PEER_MESSAGE_KIND,
        PEER_MESSAGE_KIND: payload,
    }
