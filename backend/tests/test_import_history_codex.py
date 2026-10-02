import base64

from openai_codex.generated.v2_all import Turn

from waypoint.backends.codex.history import turns_to_events
from waypoint.schemas import EventKind


def _turn(**overrides: object) -> Turn:
    base: dict[str, object] = {
        "id": "turn1",
        "status": "completed",
        "startedAt": 1_700_000_000,
        "completedAt": 1_700_000_010,
        "items": [],
    }
    base.update(overrides)
    return Turn.model_validate(base)


def test_user_message_becomes_user_input_event() -> None:
    turn = _turn(
        items=[
            {
                "type": "userMessage",
                "id": "item-u1",
                "content": [
                    {"type": "text", "text": "please fix the bug"},
                ],
            }
        ]
    )
    events = turns_to_events([turn], "sess-1")
    assert len(events) == 1
    event = events[0]
    assert event.kind == EventKind.USER_INPUT
    assert event.text == "please fix the bug"
    assert event.session_id == "sess-1"


def test_agent_message_becomes_full_agent_output_event() -> None:
    turn = _turn(
        items=[
            {
                "type": "agentMessage",
                "id": "item-a1",
                "text": "Here is the full assistant reply.",
            }
        ]
    )
    events = turns_to_events([turn], "sess-1")
    assert len(events) == 1
    event = events[0]
    assert event.kind == EventKind.AGENT_OUTPUT
    assert event.text == "Here is the full assistant reply."
    assert event.metadata["item_id"] == "item-a1"
    assert event.metadata["item_type"] == "agentMessage"


def test_command_execution_item_synthesizes_paired_tool_call_and_result() -> None:
    turn = _turn(
        items=[
            {
                "type": "commandExecution",
                "id": "item-c1",
                "command": "ls -la",
                "commandActions": [],
                "cwd": "/tmp",
                "status": "completed",
                "aggregatedOutput": "file1\nfile2",
            }
        ]
    )
    events = turns_to_events([turn], "sess-1")
    assert len(events) == 2

    call, result = events
    assert call.kind == EventKind.TOOL_CALL
    assert call.text == "$ ls -la"
    assert result.kind == EventKind.TOOL_RESULT
    assert result.text == "$ ls -la\nfile1\nfile2"

    for event in (call, result):
        assert event.metadata["item_id"] == "item-c1"
        assert event.metadata["item_type"] == "commandExecution"
        assert event.metadata["tool_name"] == "Bash"
        assert event.metadata["payload"]["item"]["id"] == "item-c1"
        assert event.metadata["payload"]["item"]["command"] == "ls -la"

    assert call.metadata["method"] == "item/started"
    assert result.metadata["method"] == "item/completed"
    # The completed result carries the neutral outcome flag so imported
    # sessions resolve tool outcomes in telemetry, not just live ones.
    assert "is_error" not in call.metadata
    assert result.metadata["is_error"] is False


def test_unknown_item_type_becomes_generic_tool_pair() -> None:
    # A thread item whose type the pinned SDK does not model (a newer CLI's item
    # ahead of the SDK union) imports as a generic tool pair carrying the raw
    # item, not dropped and not crashing the whole import.
    turn = _turn(
        items=[
            {
                "type": "waypointFutureItem",
                "id": "item-x1",
                "path": "/root/plan_review",
                "detail": {"nested": "kept"},
            }
        ]
    )
    events = turns_to_events([turn], "sess-1")
    assert [event.kind for event in events] == [
        EventKind.TOOL_CALL,
        EventKind.TOOL_RESULT,
    ]
    call, result = events
    assert call.text == "waypointFutureItem"
    assert call.metadata["tool_name"] == "waypointFutureItem"
    assert result.metadata["item_id"] == call.metadata["item_id"] == "item-x1"
    assert result.text == '{"path":"/root/plan_review","detail":{"nested":"kept"}}'
    item = result.metadata["payload"]["item"]
    assert item["path"] == "/root/plan_review"
    assert item["detail"] == {"nested": "kept"}


def test_multiple_turns_preserve_sequence_order() -> None:
    first = _turn(
        id="turn1",
        items=[
            {
                "type": "userMessage",
                "id": "item-u1",
                "content": [{"type": "text", "text": "hi"}],
            }
        ],
    )
    second = _turn(
        id="turn2",
        items=[{"type": "agentMessage", "id": "item-a1", "text": "hello back"}],
    )
    events = turns_to_events([first, second], "sess-1")
    assert [event.text for event in events] == ["hi", "hello back"]


def test_reasoning_summary_imports_as_one_thinking_output() -> None:
    turn = _turn(
        items=[
            {
                "type": "reasoning",
                "id": "rs1",
                "summary": ["Check the tests.", "Then fix."],
                "content": [],
            },
            {"type": "reasoning", "id": "rs2", "summary": [], "content": []},
        ]
    )
    events = turns_to_events([turn], "sess-1")
    assert len(events) == 1
    event = events[0]
    assert event.kind == EventKind.AGENT_OUTPUT
    assert event.text == "Check the tests.\n\nThen fix."
    assert event.metadata["item_kind"] == "reasoning"
    assert event.metadata["item_id"] == "rs1"


def test_exited_review_mode_imports_the_review() -> None:
    turn = _turn(
        items=[
            {"type": "enteredReviewMode", "id": "r1", "review": "current changes"},
            {"type": "exitedReviewMode", "id": "r2", "review": "Looks good."},
        ]
    )
    events = turns_to_events([turn], "sess-1")
    assert [(event.kind, event.text) for event in events] == [
        (EventKind.SYSTEM_NOTE, "Review started: current changes"),
        (EventKind.AGENT_OUTPUT, "Looks good."),
    ]


def test_image_generation_imports_with_an_attachment_blob() -> None:
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nrest").decode()
    turn = _turn(
        items=[
            {
                "type": "imageGeneration",
                "id": "ig1",
                "status": "completed",
                "result": encoded,
                "revisedPrompt": "A red circle",
            }
        ]
    )
    events = turns_to_events([turn], "sess-1")
    assert [event.kind for event in events] == [
        EventKind.TOOL_CALL,
        EventKind.TOOL_RESULT,
    ]
    call, result = events
    assert call.text == "A red circle"
    assert result.metadata["capture_inline_blobs"] == [
        {"filename": "ig1.png", "base64": encoded, "mime": "image/png"}
    ]
    assert result.metadata["is_error"] is False
    assert call.metadata["payload"]["item"]["result"] == ""
    assert result.metadata["payload"]["item"]["result"] == ""
