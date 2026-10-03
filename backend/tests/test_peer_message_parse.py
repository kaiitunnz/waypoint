"""Unit tests for the Claude peer-message parser and the peer-message contract."""

import pytest

from waypoint.backends.claude_code.normalize import (
    classify_injected_user_turn,
    parse_peer_message,
)
from waypoint.backends.peer_messages import PeerMessage, peer_message_event

CROSS_SESSION = (
    '<cross-session-message from="uds:/run/cc/42.sock" from-name="peer-a" '
    'from-mode="prompting">\n'
    "  First line with a literal <tag> &amp; entity.\n"
    "  Second line.\n"
    "</cross-session-message>"
)
DELIVERED = (
    "Another Claude session sent a message:\n"
    + CROSS_SESSION
    + "\n\nThis came from another Claude session."
)
HANDBACK = (
    '<agent-message from="agent-x">\n'
    "[Subagent hand-back] preamble. The report follows:\n"
    "  Report line.\n"
    "</agent-message>"
)


def test_parse_cross_session_message() -> None:
    parsed = parse_peer_message(CROSS_SESSION)
    assert parsed is not None
    assert parsed.channel == "cross_session"
    assert parsed.sender_address == "uds:/run/cc/42.sock"
    assert parsed.sender_name == "peer-a"
    assert parsed.sender_mode == "prompting"
    # Bodies are raw text: dedented and stripped, never unescaped.
    assert parsed.body == "First line with a literal <tag> &amp; entity.\nSecond line."
    assert not parsed.is_handback


def test_delivered_and_queued_forms_share_the_dedup_key() -> None:
    queued = parse_peer_message(CROSS_SESSION)
    delivered = parse_peer_message(DELIVERED)
    assert queued is not None and delivered is not None
    assert delivered.body == queued.body
    assert delivered.dedup_key == queued.dedup_key


def test_parse_agent_message_and_handback() -> None:
    agent = parse_peer_message('<agent-message from="a1">\nhi\n</agent-message>')
    assert agent is not None
    assert (agent.channel, agent.sender_name, agent.body) == ("agent", None, "hi")
    handback = parse_peer_message(HANDBACK)
    assert handback is not None
    assert handback.is_handback
    assert handback.body == "Report line."


@pytest.mark.parametrize(
    "content",
    [
        None,
        42,
        "",
        "plain text",
        "Quoting: " + CROSS_SESSION,
        '<cross-session-message from="x">no close',
        "<cross-session-message>no sender</cross-session-message>",
        '<agent-message from="">empty sender</agent-message>',
        '<agent-message from="a1">   </agent-message>',
    ],
)
def test_parse_rejects_malformed_content(content: object) -> None:
    assert parse_peer_message(content) is None


def test_classify_peer_message() -> None:
    assert classify_injected_user_turn({"origin": {"kind": "peer"}}, "x") == (
        "peer_message"
    )
    assert classify_injected_user_turn({}, DELIVERED) == "peer_message"
    assert classify_injected_user_turn(
        {}, "Another Claude session sent a message:\nhi"
    ) == ("none")
    task = "<task-notification><summary>s</summary></task-notification>"
    assert classify_injected_user_turn({"origin": {"kind": "peer"}}, task) == (
        "task_notification"
    )
    assert classify_injected_user_turn({"origin": {"kind": "peer"}}, [{}]) == "none"


def test_peer_message_event_omits_absent_sender_fields() -> None:
    text, metadata = peer_message_event(
        PeerMessage(channel="agent", sender_address="a1", body="hi")
    )
    assert text == "hi"
    assert metadata == {
        "method": "peer_message",
        "kind": "peer_message",
        "peer_message": {"version": 1, "channel": "agent", "sender_address": "a1"},
    }
