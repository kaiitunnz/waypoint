"""The ``summary`` a tool call carries: the agent's own description of it, taken
from the field each agent writes for that purpose and absent otherwise."""

import asyncio
from typing import Any

import pytest

from waypoint.backends.claude_code.adapter import ClaudeCliAdapter, ClaudeSessionState
from waypoint.backends.claude_code.history import convert_transcript_records
from waypoint.backends.claude_code.normalize import call_summary
from waypoint.backends.claude_tty.normalize import TranscriptNormalizer
from waypoint.backends.events import set_summary
from waypoint.schemas import EventKind

BASH_INPUT = {"command": "git status --short", "description": "Check for changes"}


def _tool_use(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """An assistant record as Claude Code writes it, with synthetic ids."""
    return {
        "type": "assistant",
        "uuid": "record-1",
        "timestamp": "2026-04-29T15:47:10.000Z",
        "message": {
            "id": "message-1",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu-1",
                    "name": name,
                    "input": tool_input,
                    "caller": {"type": "direct"},
                }
            ],
            "stop_reason": "tool_use",
        },
    }


@pytest.mark.parametrize("tool", ["Bash", "Agent", "Task", "Monitor", "PowerShell"])
def test_claude_tools_with_a_description_of_the_call(tool: str) -> None:
    assert call_summary(tool, {"description": "Do the thing"}) == "Do the thing"


@pytest.mark.parametrize(
    "tool",
    ["Write", "Artifact", "Workflow", "TaskCreate", "mcp__calendar__create_event"],
)
def test_a_description_argument_of_another_tool_is_not_a_summary(tool: str) -> None:
    assert call_summary(tool, {"description": "Quarterly review with finance"}) is None


@pytest.mark.parametrize(
    "tool_input", [{}, {"description": ""}, {"description": 3}, None, "ls"]
)
def test_claude_without_a_description_gives_none(tool_input: Any) -> None:
    metadata = set_summary({}, call_summary("Bash", tool_input))
    assert "summary" not in metadata


def test_summary_is_trimmed_and_absent_when_blank() -> None:
    assert set_summary({}, "  Run tests \n") == {"summary": "Run tests"}
    assert set_summary({}, "   ") == {}
    assert set_summary({}, None) == {}


def _claude_emitted() -> tuple[ClaudeCliAdapter, list[tuple[EventKind, dict]]]:
    emitted: list[tuple[EventKind, dict]] = []

    async def emit(session_id, kind, text, metadata, status):
        emitted.append((kind, metadata))

    return ClaudeCliAdapter(emit), emitted


def _claude_state(adapter: ClaudeCliAdapter) -> ClaudeSessionState:
    state = ClaudeSessionState(
        session_id="sess",
        cwd="/tmp",
        process=None,  # type: ignore[arg-type]
        claude_session_id="claude-session",
        stdout_task=asyncio.create_task(asyncio.sleep(0)),
        stderr_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
    )
    adapter._sessions["sess"] = state
    return state


async def test_structured_claude_call_and_approval_carry_the_summary() -> None:
    adapter, emitted = _claude_emitted()
    state = _claude_state(adapter)

    await adapter._handle_assistant(state, _tool_use("Bash", BASH_INPUT))
    await adapter._handle_can_use_tool(
        state,
        {
            "type": "control_request",
            "request_id": "request-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "tool_use_id": "toolu-1",
                "input": BASH_INPUT,
            },
        },
    )

    kinds = {kind: metadata for kind, metadata in emitted}
    assert kinds[EventKind.TOOL_CALL]["summary"] == "Check for changes"
    assert kinds[EventKind.APPROVAL_REQUEST]["summary"] == "Check for changes"


async def test_structured_claude_mcp_call_has_no_summary() -> None:
    adapter, emitted = _claude_emitted()
    state = _claude_state(adapter)
    event = {"title": "Standup", "description": "Daily sync with the team"}

    await adapter._handle_assistant(
        state, _tool_use("mcp__calendar__create_event", event)
    )
    await adapter._handle_can_use_tool(
        state,
        {
            "type": "control_request",
            "request_id": "request-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "mcp__calendar__create_event",
                "tool_use_id": "toolu-1",
                "input": event,
            },
        },
    )

    assert [kind for kind, _ in emitted] == [
        EventKind.TOOL_CALL,
        EventKind.APPROVAL_REQUEST,
    ]
    assert all("summary" not in metadata for _, metadata in emitted)


@pytest.mark.parametrize(
    ("tool_input", "expected"),
    [(BASH_INPUT, "Check for changes"), ({"command": "ls"}, None)],
)
def test_claude_tty_and_history_calls(
    tool_input: dict[str, Any], expected: str | None
) -> None:
    record = _tool_use("Bash", tool_input)

    [tty] = [
        event
        for event in TranscriptNormalizer().process_record(record)
        if event.kind is EventKind.TOOL_CALL
    ]
    [imported] = [
        event
        for event in convert_transcript_records("sess", [record])
        if event.kind is EventKind.TOOL_CALL
    ]

    assert tty.metadata.get("summary") == expected
    assert imported.metadata.get("summary") == expected
    assert ("summary" in tty.metadata) is (expected is not None)


def test_claude_agent_call_is_summarized() -> None:
    record = _tool_use(
        "Agent",
        {"description": "Find callers", "prompt": "List them", "subagent_type": "x"},
    )

    [event] = [
        event
        for event in TranscriptNormalizer().process_record(record)
        if event.kind is EventKind.TOOL_CALL
    ]

    assert event.metadata["summary"] == "Find callers"
