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


def test_unknown_item_type_becomes_preserved_passthrough_event() -> None:
    # A thread item whose type the pinned SDK does not model (a newer CLI's item
    # ahead of the SDK union) must import as a generic passthrough note carrying
    # the raw item, not be dropped and not crash the whole import.
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
    assert len(events) == 1
    event = events[0]
    assert event.kind == EventKind.SYSTEM_NOTE
    assert "waypointFutureItem" in event.text
    assert event.metadata["item_type"] == "waypointFutureItem"
    item = event.metadata["payload"]["item"]
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
