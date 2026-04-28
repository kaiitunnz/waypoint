import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from waypoint.claude_cli import (
    DEFAULT_TIMEOUT_SECONDS,
    ClaudeCliAdapter,
    ClaudeSessionState,
)
from waypoint.schemas import EventKind, SessionStatus


class FakeStream:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.lines = list(lines or [])
        self.writes: list[bytes] = []
        self.closed = False

    async def readline(self) -> bytes:
        if not self.lines:
            return b""
        return self.lines.pop(0)

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStream()
        self.stdout = FakeStream()
        self.stderr = FakeStream()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.signals: list[int] = []

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


def _make_adapter(
    emitted: list[tuple[str, EventKind, str, dict[str, Any], SessionStatus]],
) -> ClaudeCliAdapter:
    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    return ClaudeCliAdapter(
        emit,
        hook_settings_path=Path("/tmp/waypoint-test-settings.json"),
        hook_secret="test-secret",
        hook_url="http://127.0.0.1:8787",
    )


def _attach_state(
    adapter: ClaudeCliAdapter, session_id: str = "sess"
) -> tuple[ClaudeSessionState, FakeProcess]:
    process = FakeProcess()
    state = ClaudeSessionState(
        session_id=session_id,
        cwd="/tmp",
        process=process,  # type: ignore[arg-type]
        claude_session_id="claude-uuid",
        stdout_task=asyncio.create_task(asyncio.sleep(0)),
        stderr_task=asyncio.create_task(asyncio.sleep(0)),
    )
    adapter._sessions[session_id] = state
    return state, process


@pytest.mark.asyncio
async def test_send_input_writes_user_message_envelope() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    await adapter.send_input("sess", "hello world")
    payloads = [
        json.loads(line.decode("utf-8").strip()) for line in process.stdin.writes
    ]
    assert payloads == [
        {"type": "user", "message": {"role": "user", "content": "hello world"}}
    ]


@pytest.mark.asyncio
async def test_dispatch_assistant_emits_text_and_tool_use_events() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "content": [
                {"type": "text", "text": "Working on it"},
                {
                    "type": "tool_use",
                    "id": "toolu_xyz",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ],
        },
    }
    await adapter._dispatch(state, event)
    kinds = [item[1] for item in emitted]
    texts = [item[2] for item in emitted]
    assert kinds == [EventKind.AGENT_OUTPUT, EventKind.TOOL_CALL]
    assert texts[0] == "Working on it"
    assert "Bash" in texts[1] and "ls" in texts[1]
    # text block carries assistant message id; tool_use carries its own tool_use_id as item_id
    assert emitted[0][3]["item_id"] == "msg_1"
    assert emitted[1][3]["tool_use_id"] == "toolu_xyz"


@pytest.mark.asyncio
async def test_dispatch_user_tool_result_emits_event() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    event = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_xyz",
                    "content": "ok",
                    "is_error": False,
                }
            ]
        },
    }
    await adapter._dispatch(state, event)
    assert emitted[0][1] == EventKind.TOOL_RESULT
    assert emitted[0][2] == "ok"
    assert emitted[0][3]["tool_use_id"] == "toolu_xyz"


@pytest.mark.asyncio
async def test_await_approval_resolves_via_respond() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    _attach_state(adapter)
    payload = {
        "waypoint_session_id": "sess",
        "tool_use_id": "toolu_1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }

    async def resolver() -> None:
        # Wait until adapter has registered the pending approval.
        for _ in range(50):
            if adapter.has_pending_approval("sess"):
                break
            await asyncio.sleep(0.01)
        await adapter.respond_to_approval("sess", "approve")

    decision_task = asyncio.create_task(adapter.await_approval(payload))
    await resolver()
    decision = await decision_task
    assert decision["permissionDecision"] == "allow"
    # Pending was emitted as APPROVAL_REQUEST
    assert emitted[0][1] == EventKind.APPROVAL_REQUEST


@pytest.mark.asyncio
async def test_await_approval_returns_ask_for_unknown_session() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    decision = await adapter.await_approval(
        {"waypoint_session_id": "nope", "tool_use_id": "x"}
    )
    assert decision["permissionDecision"] == "ask"


@pytest.mark.asyncio
async def test_terminate_session_closes_stdin_and_resolves_pending(monkeypatch) -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    # Set up a pending approval that would otherwise block forever.
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    from waypoint.claude_cli import ClaudePendingApproval

    state.pending["toolu_1"] = ClaudePendingApproval(
        tool_use_id="toolu_1", payload={}, future=future
    )
    handled = await adapter.terminate_session("sess")
    assert handled is True
    assert "sess" not in adapter._sessions
    assert process.terminated is True
    assert process.stdin.closed is True
    assert future.done()
    assert future.result()["permissionDecision"] == "deny"


def test_map_decision_table() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    assert adapter._map_decision("approve") == "allow"
    assert adapter._map_decision("yes") == "allow"
    assert adapter._map_decision("acceptForSession") == "allow"
    assert adapter._map_decision("decline") == "deny"
    assert adapter._map_decision("anything-else") == "deny"


def test_default_timeout_is_finite() -> None:
    assert 0 < DEFAULT_TIMEOUT_SECONDS < 24 * 3600
