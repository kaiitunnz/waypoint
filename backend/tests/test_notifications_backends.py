"""Each supported backend emits the normalized ``interaction`` envelope on the
same event that makes a session actionable, alongside its existing transcript
metadata."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from waypoint.backends.claude_code.adapter import ClaudeCliAdapter, ClaudeSessionState
from waypoint.backends.events import INTERACTION_METADATA_KEY
from waypoint.backends.opencode.normalize import map_event
from waypoint.notifications.render import intent_from_event
from waypoint.schemas import EventKind, EventRecord


def _adapter() -> tuple[ClaudeCliAdapter, list[tuple[Any, ...]]]:
    emitted: list[tuple[Any, ...]] = []

    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    return ClaudeCliAdapter(emit), emitted


def _state(adapter: ClaudeCliAdapter) -> ClaudeSessionState:
    state = ClaudeSessionState(
        session_id="sess",
        cwd="/tmp",
        process=None,  # type: ignore[arg-type]
        claude_session_id="claude-uuid",
        stdout_task=asyncio.create_task(asyncio.sleep(0)),
        stderr_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
    )
    adapter._sessions["sess"] = state
    return state


def _can_use_tool(tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> dict:
    return {
        "type": "control_request",
        "request_id": "req-1",
        "request": {
            "subtype": "can_use_tool",
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "input": tool_input,
        },
    }


def _interaction(emitted: list[tuple[Any, ...]], kind: EventKind) -> dict[str, Any]:
    for _, event_kind, _text, metadata, _status in emitted:
        if event_kind == kind and INTERACTION_METADATA_KEY in metadata:
            return metadata[INTERACTION_METADATA_KEY]
    raise AssertionError(f"no interaction envelope on a {kind} event")


async def test_claude_tool_approval_envelope() -> None:
    adapter, emitted = _adapter()
    state = _state(adapter)
    await adapter._handle_can_use_tool(
        state, _can_use_tool("Bash", {"command": "pytest"}, "toolu_a")
    )
    interaction = _interaction(emitted, EventKind.APPROVAL_REQUEST)
    assert interaction["kind"] == "approval"
    assert interaction["request_id"] == "toolu_a"
    # It maps cleanly to a notification intent.
    event = EventRecord(
        session_id="sess",
        ts=datetime.now(UTC),
        kind=EventKind.APPROVAL_REQUEST,
        text="x",
        metadata={INTERACTION_METADATA_KEY: interaction},
        sequence=1,
    )
    intent = intent_from_event(event, session_title="Sess")
    assert intent is not None and intent.kind == "approval"


async def test_claude_plan_approval_envelope() -> None:
    adapter, emitted = _adapter()
    state = _state(adapter)
    state.last_plan_path = "/tmp/plan.md"
    state.last_plan_content = "Step 1\nStep 2"
    await adapter._handle_can_use_tool(
        state, _can_use_tool("ExitPlanMode", {}, "toolu_plan")
    )
    interaction = _interaction(emitted, EventKind.APPROVAL_REQUEST)
    assert interaction["kind"] == "plan_approval"
    assert interaction["plan_item_id"] == "toolu_plan"
    assert interaction["body"] == "Step 1\nStep 2"


async def test_claude_ask_user_question_envelope() -> None:
    adapter, emitted = _adapter()
    state = _state(adapter)
    event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_q",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [
                            {
                                "question": "Which database?",
                                "options": [
                                    {"label": "postgres", "description": "default"},
                                    {"label": "sqlite"},
                                ],
                            }
                        ]
                    },
                }
            ],
        },
    }
    await adapter._dispatch(state, event)
    interaction = _interaction(emitted, EventKind.TOOL_CALL)
    assert interaction["kind"] == "question"
    assert interaction["request_id"] == "toolu_q"
    labels = [choice["label"] for choice in interaction["choices"]]
    assert labels == ["postgres", "sqlite"]


def test_opencode_permission_envelope() -> None:
    _kind, _text, metadata = map_event(
        "permission.asked",
        {"id": "perm-1", "type": "bash", "permission": "run command"},
    )
    assert metadata[INTERACTION_METADATA_KEY]["kind"] == "approval"
    assert metadata[INTERACTION_METADATA_KEY]["request_id"] == "perm-1"


def test_opencode_question_envelope() -> None:
    _kind, _text, metadata = map_event(
        "question.asked",
        {
            "id": "q-1",
            "questions": [
                {"question": "Pick one", "options": [{"label": "a"}, {"label": "b"}]}
            ],
        },
    )
    interaction = metadata[INTERACTION_METADATA_KEY]
    assert interaction["kind"] == "question"
    assert [c["label"] for c in interaction["choices"]] == ["a", "b"]
