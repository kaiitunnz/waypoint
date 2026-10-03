"""Unit tests for the Claude peer-message parser and its task-notification card."""

import pytest

from waypoint.backends.claude_code.normalize import (
    build_peer_message_metadata,
    classify_injected_user_turn,
    parse_peer_message,
)
from waypoint.backends.task_notifications import (
    CAPTURE_DISABLED,
    NOT_CAPTURED_ON_IMPORT,
    TASK_NOTIFICATION_INLINE_LIMIT,
)

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
    assert parsed.sender_address == "uds:/run/cc/42.sock"
    assert parsed.sender_name == "peer-a"
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
    assert (agent.sender_address, agent.sender_name, agent.body) == ("a1", None, "hi")
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


def test_peer_message_card_spills_a_long_body_like_any_task_report() -> None:
    long = "x" * (TASK_NOTIFICATION_INLINE_LIMIT + 10)
    parsed = parse_peer_message(f'<agent-message from="a1">\n{long}\n</agent-message>')
    assert parsed is not None
    text, metadata = build_peer_message_metadata(
        parsed, record_uuid="u1", allow_output_capture=True, capture_enabled=True
    )
    assert text == "Message from a1"
    payload = metadata["task_notification"]
    assert (payload["id"], payload["kind"]) == ("u1", "message")
    assert payload["result_truncated"] is True
    assert payload["output_available"] is True
    assert metadata["capture_inline_blobs"][0]["text"] == long

    _, imported = build_peer_message_metadata(
        parsed, record_uuid=None, allow_output_capture=False, capture_enabled=False
    )
    assert imported["task_notification"]["output_unavailable_reason"] == (
        NOT_CAPTURED_ON_IMPORT
    )

    _, disabled = build_peer_message_metadata(
        parsed, record_uuid=None, allow_output_capture=True, capture_enabled=False
    )
    assert "capture_inline_blobs" not in disabled
    assert disabled["task_notification"]["output_unavailable_reason"] == (
        CAPTURE_DISABLED
    )
