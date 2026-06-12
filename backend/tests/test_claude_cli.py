import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.claude_code.adapter import (
    ClaudeCliAdapter,
    ClaudeSessionState,
    claude_cli_mode_for,
)
from waypoint.backends.claude_code.models import (
    DEFAULT_CLAUDE_MODELS,
    claude_default_model_id,
)
from waypoint.backends.claude_code.plugin import ClaudeCodePluginConfig
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
    session_updates: list[tuple[str, dict[str, Any], bool]] | None = None,
) -> ClaudeCliAdapter:
    async def emit(session_id, kind, text, metadata, status):
        emitted.append((session_id, kind, text, metadata, status))

    async def update(session_id: str, updates: dict[str, Any], publish: bool) -> Any:
        if session_updates is not None:
            session_updates.append((session_id, updates, publish))
        return updates

    return ClaudeCliAdapter(
        emit,
        on_session_update=update if session_updates is not None else None,
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
        wait_task=asyncio.create_task(asyncio.sleep(0)),
    )
    adapter._sessions[session_id] = state
    return state, process


def _can_use_tool_event(payload: dict[str, Any], request_id: str = "req-1") -> dict:
    """Build a `can_use_tool` control_request from a hook-style payload."""
    request: dict[str, Any] = {
        "subtype": "can_use_tool",
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "input": payload.get("tool_input"),
    }
    if payload.get("permission_suggestions") is not None:
        request["permission_suggestions"] = payload["permission_suggestions"]
    return {"type": "control_request", "request_id": request_id, "request": request}


def _permission_results(process: Any) -> list[dict[str, Any]]:
    """PermissionResults from every control_response written to the binary."""
    results: list[dict[str, Any]] = []
    for line in process.stdin.writes:
        obj = json.loads(line.decode("utf-8").strip())
        if obj.get("type") == "control_response":
            result = obj["response"].get("response")
            if isinstance(result, dict):
                results.append(result)
    return results


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
async def test_can_use_tool_resolves_via_respond() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    payload = {
        "tool_use_id": "toolu_1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    # Pending was registered and surfaced as APPROVAL_REQUEST; no response yet.
    assert adapter.has_pending_approval("sess")
    assert emitted[0][1] == EventKind.APPROVAL_REQUEST
    assert not _permission_results(process)

    assert await adapter.respond_to_approval("sess", "approve")
    results = _permission_results(process)
    assert results[-1] == {"behavior": "allow", "updatedInput": {"command": "ls"}}
    assert not adapter.has_pending_approval("sess")


@pytest.mark.asyncio
async def test_can_use_tool_generates_edit_diff_preview() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    payload = {
        "tool_use_id": "toolu_1",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "/tmp/app.py",
            "old_string": "old",
            "new_string": "new",
        },
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    await adapter.respond_to_approval("sess", "decline")

    # The adapter synthesizes the diff from the tool input (no file read), then
    # surfaces it both as a TOOL_RESULT preview and on the approval card.
    assert emitted[0][1] == EventKind.TOOL_RESULT
    assert emitted[0][3]["diff_preview"]["files"][0]["path"] == "/tmp/app.py"
    assert emitted[1][1] == EventKind.APPROVAL_REQUEST
    assert emitted[1][3]["diff_preview"]["files"][0]["path"] == "/tmp/app.py"
    assert _permission_results(process)[-1]["behavior"] == "deny"


@pytest.mark.asyncio
async def test_user_tool_result_inherits_generated_diff_preview() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.permission_mode = "acceptEdits"
    payload = {
        "tool_use_id": "toolu_1",
        "tool_name": "Edit",
        "tool_input": {
            "file_path": "/tmp/app.py",
            "old_string": "old",
            "new_string": "new",
        },
    }

    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    await adapter._dispatch(
        state,
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "updated successfully",
                    }
                ]
            },
        },
    )

    assert _permission_results(process)[-1]["behavior"] == "allow"
    assert emitted[-1][1] == EventKind.TOOL_RESULT
    assert emitted[-1][3]["tool_name"] == "Edit"
    assert emitted[-1][3]["tool_input"]["file_path"] == "/tmp/app.py"
    assert emitted[-1][3]["diff_preview"]["files"][0]["path"] == "/tmp/app.py"


def test_diff_preview_from_input_reads_local_file(tmp_path: Path) -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")

    preview = adapter._diff_preview_from_input(
        "Edit",
        {"file_path": "app.py", "old_string": "old\n", "new_string": "new\n"},
        str(tmp_path),
    )

    assert preview is not None
    assert preview["phase"] == "proposed"
    assert preview["files"][0]["path"] == "app.py"
    assert preview["files"][0]["additions"] == 1
    assert preview["files"][0]["deletions"] == 1


@pytest.mark.asyncio
async def test_can_use_tool_denies_when_identifiers_missing() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    # A can_use_tool request with no tool_use_id can't be tracked; deny it.
    await adapter._handle_can_use_tool(
        state,
        {
            "type": "control_request",
            "request_id": "req-1",
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {}},
        },
    )
    assert _permission_results(process)[-1]["behavior"] == "deny"
    assert not adapter.has_pending_approval("sess")


@pytest.mark.asyncio
async def test_terminate_session_closes_stdin_and_resolves_pending(monkeypatch) -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    # A pending approval should be denied (control_response) on teardown so the
    # binary unblocks the parked tool call.
    from waypoint.backends.claude_code.adapter import ClaudePendingApproval

    state.pending["toolu_1"] = ClaudePendingApproval(
        tool_use_id="toolu_1", payload={}, request_id="req-1"
    )
    handled = await adapter.terminate_session("sess")
    assert handled is True
    assert "sess" not in adapter._sessions
    assert process.terminated is True
    assert _permission_results(process)[-1]["behavior"] == "deny"
    assert process.stdin.closed is True


def test_map_decision_table() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    assert adapter._map_decision("approve") == "allow"
    assert adapter._map_decision("yes") == "allow"
    assert adapter._map_decision("acceptForSession") == "allow"
    assert adapter._map_decision("decline") == "deny"
    assert adapter._map_decision("anything-else") == "deny"


def test_hook_timeout_default_is_finite() -> None:
    config = ClaudeCodePluginConfig()
    assert 0 < config.hook_timeout_seconds < 24 * 3600


def test_claude_default_model_id_comes_from_catalog() -> None:
    default_option = next(opt for opt in DEFAULT_CLAUDE_MODELS if opt.is_default)
    assert claude_default_model_id() == default_option.id
    assert ClaudeCodePluginConfig().default_model_id == default_option.id


@pytest.mark.asyncio
async def test_send_input_reports_dead_process_with_stderr_tail() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.stderr_tail.append("env: claude: No such file or directory")
    process.returncode = 127
    from waypoint.backends.claude_code.adapter import ClaudeCliError

    with pytest.raises(ClaudeCliError) as info:
        await adapter.send_input("sess", "hi")
    message = str(info.value)
    assert "rc=127" in message
    assert "env: claude: No such file or directory" in message


@pytest.mark.asyncio
async def test_dispatch_system_init_records_runtime_slash_commands() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(
        state,
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-sonnet-4-5",
            "slash_commands": ["clear", "compact", "usage"],
            "session_id": "claude-uuid",
        },
    )

    assert adapter.session_slash_commands("sess") == ("clear", "compact", "usage")
    assert state.model == "sonnet"


@pytest.mark.asyncio
async def test_dispatch_system_init_preserves_1m_default_model() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.model = "opus[1m]"

    await adapter._dispatch(
        state,
        {
            "type": "system",
            "subtype": "init",
            "model": "opus",
            "session_id": "claude-uuid",
        },
    )

    assert state.model == "opus[1m]"


@pytest.mark.asyncio
async def test_dispatch_system_init_refreshes_context_window_on_family_change() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.model = "sonnet"

    calls: list[str] = []

    async def fake_refresh(target_state: ClaudeSessionState) -> None:
        calls.append(target_state.session_id)

    adapter._refresh_context_usage = fake_refresh  # type: ignore[assignment]

    await adapter._dispatch(
        state,
        {
            "type": "system",
            "subtype": "init",
            "model": "opus",
            "session_id": "claude-uuid",
        },
    )

    assert state.model == "opus"
    assert calls == ["sess"]


@pytest.mark.asyncio
async def test_dispatch_assistant_emits_context_usage_snapshot() -> None:
    emitted: list = []
    session_updates: list[tuple[str, dict[str, Any], bool]] = []
    adapter = _make_adapter(emitted, session_updates=session_updates)
    state, _ = _attach_state(adapter)
    state.model = "opus[1m]"

    await adapter._dispatch(
        state,
        {
            "type": "assistant",
            "message": {
                "id": "msg_1",
                "usage": {
                    "input_tokens": 11,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": 7,
                },
                "content": [{"type": "text", "text": "Working on it"}],
            },
        },
    )

    assert emitted[0][1] == EventKind.AGENT_OUTPUT
    assert len(session_updates) == 1
    session_id, payload, publish = session_updates[0]
    assert session_id == "sess"
    assert publish is False
    context_usage = payload["context_usage"]
    assert context_usage["used_tokens"] == 19
    assert context_usage["context_window_tokens"] == 1_000_000
    assert context_usage["source"] == "claude_code"
    assert context_usage["breakdown"] == {
        "input_tokens": 11,
        "cache_read_tokens": 3,
        "cache_creation_tokens": 5,
        "output_tokens": 7,
    }
    assert isinstance(context_usage["updated_at"], str)


@pytest.mark.asyncio
async def test_dispatch_assistant_dedupes_context_usage_snapshot() -> None:
    emitted: list = []
    session_updates: list[tuple[str, dict[str, Any], bool]] = []
    adapter = _make_adapter(emitted, session_updates=session_updates)
    state, _ = _attach_state(adapter)
    state.model = "sonnet"

    event = {
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "usage": {
                "input_tokens": 8,
                "cache_read_input_tokens": 2,
                "cache_creation_input_tokens": 1,
                "output_tokens": 6,
            },
            "content": [{"type": "text", "text": "Still going"}],
        },
    }

    await adapter._dispatch(state, event)
    await adapter._dispatch(state, event)

    assert len(session_updates) == 1
    assert state.context_usage_signature == (11, 200_000)


@pytest.mark.asyncio
async def test_set_model_refreshes_context_window_immediately() -> None:
    emitted: list = []
    session_updates: list[tuple[str, dict[str, Any], bool]] = []
    adapter = _make_adapter(emitted, session_updates=session_updates)
    state, _ = _attach_state(adapter)
    state.model = "opus"

    await adapter._dispatch(
        state,
        {
            "type": "assistant",
            "message": {
                "id": "msg_1",
                "usage": {
                    "input_tokens": 11,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": 7,
                },
                "content": [{"type": "text", "text": "Working on it"}],
            },
        },
    )
    assert session_updates[-1][1]["context_usage"]["context_window_tokens"] == 200_000

    captured: list[dict[str, Any]] = []

    async def fake_send(session_id: str, request_id: str, request: dict) -> dict:
        captured.append(request)
        return {"subtype": "ack"}

    object.__setattr__(adapter, "_send_control_request", fake_send)
    await adapter.set_model("sess", "opus[1m]")

    assert captured[-1] == {"subtype": "set_model", "model": "opus[1m]"}
    assert state.model == "opus[1m]"
    assert len(session_updates) == 2
    assert session_updates[-1][1]["context_usage"]["context_window_tokens"] == 1_000_000


@pytest.mark.asyncio
async def test_dispatch_system_status_compacting_emits_running_note() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(
        state,
        {
            "type": "system",
            "subtype": "status",
            "status": "compacting",
            "session_id": "claude-uuid",
        },
    )
    assert emitted, "expected an event"
    item = emitted[-1]
    assert item[1] == EventKind.SYSTEM_NOTE
    assert "Compacting" in item[2]
    assert item[4] == SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_dispatch_system_status_compact_result_emits_idle_note() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(
        state,
        {
            "type": "system",
            "subtype": "status",
            "status": None,
            "compact_result": "success",
            "session_id": "claude-uuid",
        },
    )
    assert emitted[-1][1] == EventKind.SYSTEM_NOTE
    assert "compaction success" in emitted[-1][2].lower()
    assert emitted[-1][4] == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_dispatch_compact_boundary_renders_token_summary() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(
        state,
        {
            "type": "system",
            "subtype": "compact_boundary",
            "session_id": "claude-uuid",
            "compact_metadata": {
                "trigger": "manual",
                "pre_tokens": 27000,
                "post_tokens": 1200,
                "duration_ms": 23000,
            },
        },
    )
    assert emitted[-1][1] == EventKind.SYSTEM_NOTE
    assert "27000 → 1200" in emitted[-1][2]
    assert "manual" in emitted[-1][2]


@pytest.mark.asyncio
async def test_watch_process_emits_error_event_with_stderr_tail() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.stderr_tail.append("env: claude: No such file or directory")
    process.returncode = 127
    await adapter._watch_process(state)
    assert any(
        item[1] == EventKind.SYSTEM_NOTE
        and "rc=127" in item[2]
        and "env: claude" in item[2]
        for item in emitted
    )


@pytest.mark.asyncio
async def test_can_use_tool_auto_allows_in_auto_mode() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.permission_mode = "auto"

    await adapter._handle_can_use_tool(
        state,
        _can_use_tool_event(
            {
                "tool_use_id": "tu-1",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            }
        ),
    )

    assert _permission_results(process)[-1] == {
        "behavior": "allow",
        "updatedInput": {"command": "rm -rf /"},
    }
    # Auto-approved tools should never surface an approval card.
    assert not any(item[1] == EventKind.APPROVAL_REQUEST for item in emitted)
    assert not state.pending


@pytest.mark.asyncio
async def test_can_use_tool_accept_edits_only_auto_allows_edit_tools() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.permission_mode = "acceptEdits"

    await adapter._handle_can_use_tool(
        state,
        _can_use_tool_event(
            {
                "tool_use_id": "tu-edit",
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/x.py"},
            }
        ),
    )
    assert _permission_results(process)[-1]["behavior"] == "allow"

    # Bash still surfaces the approval card so the user can intervene.
    await adapter._handle_can_use_tool(
        state,
        _can_use_tool_event(
            {
                "tool_use_id": "tu-bash",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            },
            request_id="req-bash",
        ),
    )
    assert "tu-bash" in state.pending
    await adapter.respond_to_approval("sess", "decline", approval_id="tu-bash")
    assert _permission_results(process)[-1]["behavior"] == "deny"


@pytest.mark.asyncio
async def test_dispatch_assistant_skips_exit_plan_mode_tool_call() -> None:
    """Plan text already renders as agent_output; ExitPlanMode tool_call would
    duplicate it and the approval card represents the gate."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    plan_text = "## Plan\n\n1. Read files\n2. Apply edits"
    event = {
        "type": "assistant",
        "message": {
            "id": "msg_plan",
            "content": [
                {"type": "text", "text": plan_text},
                {
                    "type": "tool_use",
                    "id": "toolu_plan",
                    "name": "ExitPlanMode",
                    "input": {"plan": plan_text},
                },
            ],
        },
    }
    await adapter._dispatch(state, event)
    kinds = [item[1] for item in emitted]
    assert kinds == [EventKind.AGENT_OUTPUT]
    assert emitted[0][2] == plan_text


@pytest.mark.asyncio
async def test_exit_plan_mode_outcome_tool_result_is_suppressed() -> None:
    """The ExitPlanMode tool_call is suppressed, so the binary's echoed verdict
    tool_result must be too — otherwise it renders as an orphan 0-call tool run
    (the plan card + the agent's next message already convey the outcome)."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(
        state,
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_plan",
                        "name": "ExitPlanMode",
                        "input": {"plan": "## P"},
                    }
                ],
            },
        },
    )
    await adapter._dispatch(
        state,
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_plan",
                        "content": "User has approved your plan.",
                    }
                ]
            },
        },
    )
    assert not any(item[1] == EventKind.TOOL_RESULT for item in emitted)
    # A subsequent real tool's result is unaffected.
    await adapter._dispatch(
        state,
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_other",
                        "content": "ok",
                    }
                ]
            },
        },
    )
    assert any(item[1] == EventKind.TOOL_RESULT for item in emitted)


def test_format_approval_text_omits_plan_body() -> None:
    """Approval card stays compact; the plan is rendered above as agent_output."""
    from waypoint.backends.claude_code.normalize import format_approval_text

    text = format_approval_text(
        {
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": "## Plan\n\n1. Step one"},
        }
    )
    assert text == "Approve plan and exit plan mode"
    assert "Step one" not in text


def test_claude_cli_mode_for_maps_waypoint_to_cli_values() -> None:
    """Native Claude modes pass through; auto/dontAsk are Waypoint-only and
    fall back to default so the binary launches in a recognizable state."""
    assert claude_cli_mode_for("default") == "default"
    assert claude_cli_mode_for("plan") == "plan"
    assert claude_cli_mode_for("acceptEdits") == "acceptEdits"
    assert claude_cli_mode_for("bypassPermissions") == "bypassPermissions"
    assert claude_cli_mode_for("auto") == "default"
    assert claude_cli_mode_for("dontAsk") == "default"
    assert claude_cli_mode_for("garbage") == "default"


@pytest.mark.asyncio
async def test_ask_user_question_skips_approval_card() -> None:
    """AskUserQuestion goes through can_use_tool so the binary blocks waiting
    for our verdict, but the question UI is already rendered via the tool_call
    event — emitting an APPROVAL_REQUEST too would surface it twice."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)

    payload = {
        "tool_use_id": "toolu_ask",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "ok?", "options": []}]},
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    assert adapter.has_pending_ask_question("sess")
    assert not any(item[1] == EventKind.APPROVAL_REQUEST for item in emitted)
    assert not _permission_results(process)

    handled = await adapter.respond_to_ask_question(
        "sess", "**Plan target**: Trivial wrapper-test plan", "toolu_ask"
    )
    assert handled is True
    assert _permission_results(process)[-1] == {
        "behavior": "deny",
        "message": (
            "User has answered your questions: "
            "**Plan target**: Trivial wrapper-test plan. "
            "You can now continue with the user's answers in mind."
        ),
    }


@pytest.mark.asyncio
async def test_respond_to_ask_question_returns_false_without_pending() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    _attach_state(adapter)
    handled = await adapter.respond_to_ask_question("sess", "anything")
    assert handled is False


@pytest.mark.asyncio
async def test_ask_user_question_never_auto_approves_in_auto_mode() -> None:
    """Even auto/bypassPermissions modes must surface AskUserQuestion to
    the user — auto-approving lets the binary's defer path auto-decline."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.permission_mode = "auto"

    payload = {
        "tool_use_id": "toolu_ask",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": []},
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    assert adapter.has_pending_ask_question("sess")
    await adapter.respond_to_ask_question("sess", "answer", "toolu_ask")


@pytest.mark.asyncio
async def test_interrupt_uses_control_request_not_signal(monkeypatch) -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)

    captured: list[dict] = []

    async def fake_send(session_id, request_id, request):
        captured.append(request)
        return {"subtype": "ack"}

    monkeypatch.setattr(adapter, "_send_control_request", fake_send)
    await adapter.interrupt("sess")
    assert captured == [{"subtype": "interrupt"}]
    assert process.signals == []  # SIGINT not used when control_request succeeds


@pytest.mark.asyncio
async def test_plan_mode_auto_approves_plan_file_write_and_captures_path() -> None:
    """In plan mode the binary writes the plan to ~/.claude/plans/<slug>.md
    before calling ExitPlanMode. That meta-write must auto-approve, and the
    path must be captured so ExitPlanMode can echo it back."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.permission_mode = "plan"

    await adapter._handle_can_use_tool(
        state,
        _can_use_tool_event(
            {
                "tool_use_id": "toolu_write",
                "tool_name": "Write",
                "tool_input": {
                    "file_path": "/Users/me/.claude/plans/my-plan.md",
                    "content": "# plan",
                },
            }
        ),
    )
    assert _permission_results(process)[-1]["behavior"] == "allow"
    assert state.last_plan_path == "/Users/me/.claude/plans/my-plan.md"
    # Approval card must NOT have been emitted for the meta-write.
    assert not any(item[1] == EventKind.APPROVAL_REQUEST for item in emitted)


@pytest.mark.asyncio
async def test_plan_mode_does_not_auto_approve_non_plan_writes() -> None:
    """Writes outside the ~/.claude/plans/ tree should still surface the
    approval card; only the binary's own plan-file path is implicit."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.permission_mode = "plan"

    payload = {
        "tool_use_id": "toolu_write_src",
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/src/main.py", "content": "x"},
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    assert adapter.has_pending_approval("sess")
    assert state.last_plan_path is None
    await adapter.respond_to_approval("sess", "decline")
    assert _permission_results(process)[-1]["behavior"] == "deny"


@pytest.mark.asyncio
async def test_exit_plan_mode_approval_blocks_tool_and_switches_mode(
    monkeypatch,
) -> None:
    """When the user approves an ExitPlanMode plan, the hook must deny the
    tool (so Claude doesn't read the binary's "Exit plan mode?" echo as a
    dismissal), the adapter must flip the binary out of plan mode, and the
    deny reason must echo the saved-plan path and the plan body so the
    model sees the same context the native tool_result would carry."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.permission_mode = "plan"
    state.last_plan_path = "/Users/me/.claude/plans/my-plan.md"

    mode_calls: list[str] = []

    async def fake_set_mode(session_id: str, mode: str) -> None:
        mode_calls.append(mode)
        state.permission_mode = mode

    monkeypatch.setattr(adapter, "set_permission_mode", fake_set_mode)

    plan_body = "## Plan\n1. Read files\n2. Apply edits"
    payload = {
        "tool_use_id": "toolu_plan",
        "tool_name": "ExitPlanMode",
        "tool_input": {"plan": plan_body},
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    assert adapter.has_pending_approval("sess")
    await adapter.respond_to_approval("sess", "approve")

    result = _permission_results(process)[-1]
    assert result["behavior"] == "deny"
    reason = result["message"]
    assert "approved your plan" in reason
    assert "start coding" in reason
    assert "/Users/me/.claude/plans/my-plan.md" in reason
    assert "## Approved Plan:" in reason
    assert plan_body in reason
    assert mode_calls == ["default"]
    # last_plan_path is consumed once approval lands.
    assert state.last_plan_path is None


@pytest.mark.asyncio
async def test_exit_plan_mode_restores_pre_plan_mode(monkeypatch) -> None:
    """If the user was in (e.g.) acceptEdits before toggling plan mode,
    approving the plan should drop them back into acceptEdits — not the
    fallback default."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.permission_mode = "plan"
    state.pre_plan_mode = "acceptEdits"

    mode_calls: list[str] = []

    async def fake_set_mode(session_id: str, mode: str) -> None:
        mode_calls.append(mode)
        state.permission_mode = mode

    monkeypatch.setattr(adapter, "set_permission_mode", fake_set_mode)

    payload = {
        "tool_use_id": "toolu_plan",
        "tool_name": "ExitPlanMode",
        "tool_input": {"plan": "## Plan"},
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    await adapter.respond_to_approval("sess", "approve")

    assert mode_calls == ["acceptEdits"]
    # pre_plan_mode is consumed; subsequent plan toggles will record fresh.
    assert state.pre_plan_mode is None


@pytest.mark.asyncio
async def test_set_permission_mode_records_pre_plan_mode() -> None:
    """Switching from non-plan to plan must capture the outgoing mode so
    ExitPlanMode approval can restore it."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.permission_mode = "acceptEdits"

    captured: list[dict] = []

    async def fake_send(session_id: str, request_id: str, request: dict) -> dict:
        captured.append(request)
        return {"subtype": "ack"}

    # Bypass the real control_request round-trip.
    object.__setattr__(adapter, "_send_control_request", fake_send)

    await adapter.set_permission_mode("sess", "plan")
    assert state.pre_plan_mode == "acceptEdits"
    assert state.permission_mode == "plan"

    # Toggling plan -> plan must not overwrite the recorded prior mode.
    await adapter.set_permission_mode("sess", "plan")
    assert state.pre_plan_mode == "acceptEdits"


@pytest.mark.asyncio
async def test_set_model_sends_control_request_and_mirrors_state() -> None:
    """set_model must round-trip a control_request and update state.model."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)

    captured: list[dict] = []

    async def fake_send(session_id: str, request_id: str, request: dict) -> dict:
        captured.append(request)
        return {"subtype": "ack"}

    object.__setattr__(adapter, "_send_control_request", fake_send)

    await adapter.set_model("sess", "opus")
    assert captured[-1] == {"subtype": "set_model", "model": "opus"}
    assert state.model == "opus"

    # Reverting to default omits the model field, mirroring how the CLI's
    # /model command issues `model: undefined` to drop back to the session
    # default.
    await adapter.set_model("sess", None)
    assert captured[-1] == {"subtype": "set_model"}
    assert state.model is None


@pytest.mark.asyncio
async def test_exit_plan_mode_decline_keeps_plan_mode() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, process = _attach_state(adapter)
    state.permission_mode = "plan"

    payload = {
        "tool_use_id": "toolu_plan",
        "tool_name": "ExitPlanMode",
        "tool_input": {"plan": "## Plan\n- step"},
    }
    await adapter._handle_can_use_tool(state, _can_use_tool_event(payload))
    assert adapter.has_pending_approval("sess")
    await adapter.respond_to_approval("sess", "decline")

    result = _permission_results(process)[-1]
    assert result["behavior"] == "deny"
    assert "declined your plan" in result["message"]
    assert state.permission_mode == "plan"


def test_build_local_launch_spec_uses_session_cli_mode(monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.adapter.shutil.which",
        lambda _: "/usr/bin/claude",
    )
    adapter = _make_adapter([])
    spec = adapter._build_local_launch_spec(
        "sess",
        "/tmp",
        "claude-uuid",
        resume=False,
        cli_mode="plan",
    )
    args = spec.args
    assert "--permission-mode" in args
    idx = args.index("--permission-mode")
    assert args[idx + 1] == "plan"
    assert "--model" not in args
    # Tool approval rides the stdio control protocol, not the PreToolUse hook.
    assert "--permission-prompt-tool" in args
    assert args[args.index("--permission-prompt-tool") + 1] == "stdio"
    assert "--settings" not in args
    assert spec.env is not None and spec.env.get("CLAUDE_CODE_WORKFLOWS") == "1"
    # The session id is exported so an agent (and its waypoint CLI) can inherit
    # this session's posture into children it spawns.
    assert spec.env.get("WAYPOINT_SESSION_ID") == "sess"


def test_build_local_launch_spec_emits_model_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.adapter.shutil.which",
        lambda _: "/usr/bin/claude",
    )
    adapter = _make_adapter([])
    spec = adapter._build_local_launch_spec(
        "sess",
        "/tmp",
        "claude-uuid",
        resume=False,
        cli_mode="default",
        model="opus",
    )
    args = spec.args
    assert "--model" in args
    assert args[args.index("--model") + 1] == "opus"


# ─── Task tools (Claude Code >= v2.1.142) ───────────────────────────────────


def _task_create_event(
    tool_use_id: str, subject: str, description: str | None = None
) -> dict[str, Any]:
    tool_input: dict[str, Any] = {"subject": subject}
    if description is not None:
        tool_input["description"] = description
    return {
        "type": "assistant",
        "message": {
            "id": "m_create",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "TaskCreate",
                    "input": tool_input,
                }
            ],
        },
    }


def _task_create_result_event(
    tool_use_id: str, task_id: str, *, shape: str = "string"
) -> dict[str, Any]:
    # "string" mirrors what real Claude Code emits (verified against persisted
    # production events): a plain "Task #N created successfully: ..." line. The
    # JSON shapes cover the Agent SDK doc's structured payload for forward-compat.
    content: Any
    if shape == "json":
        content = [{"task": {"id": task_id, "subject": "ignored"}}]
    elif shape == "json_text":
        content = json.dumps({"task": {"id": task_id, "subject": "ignored"}})
    else:
        content = f"Task #{task_id} created successfully: ignored"
    return {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
            ]
        },
    }


def _task_update_event(task_id: str, **patch: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": "m_update",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"toolu_update_{task_id}",
                    "name": "TaskUpdate",
                    "input": {"taskId": task_id, **patch},
                }
            ],
        },
    }


def _todo_snapshots(emitted: list) -> list[list[dict[str, Any]]]:
    return [
        item[3]["payload"]["input"]["todos"]
        for item in emitted
        if item[3].get("item_type") == "todo_list"
    ]


@pytest.mark.asyncio
async def test_task_create_folds_into_todo_snapshot() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    # The raw TaskCreate tool_call must not render; only the result emits a card.
    await adapter._dispatch(state, _task_create_event("toolu_a", "Write the parser"))
    assert emitted == []
    await adapter._dispatch(state, _task_create_result_event("toolu_a", "1"))
    snapshots = _todo_snapshots(emitted)
    assert snapshots == [
        [
            {
                "content": "Write the parser",
                "status": "pending",
                "activeForm": None,
                "description": None,
            }
        ]
    ]
    assert emitted[0][1] == EventKind.TOOL_RESULT
    assert state.task_card_item_id is not None


@pytest.mark.asyncio
async def test_task_create_reads_id_from_json_text_result() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(state, _task_create_event("toolu_a", "Task A"))
    await adapter._dispatch(
        state, _task_create_result_event("toolu_a", "t1", shape="json_text")
    )
    # Updating by the id parsed out of the JSON-text result must patch in place,
    # not create a duplicate stub.
    await adapter._dispatch(state, _task_update_event("t1", status="completed"))
    snapshots = _todo_snapshots(emitted)
    assert snapshots[-1] == [
        {
            "content": "Task A",
            "status": "completed",
            "activeForm": None,
            "description": None,
        }
    ]


@pytest.mark.asyncio
async def test_task_updates_patch_one_item_keyed_by_id() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(state, _task_create_event("toolu_a", "First"))
    await adapter._dispatch(state, _task_create_result_event("toolu_a", "1"))
    await adapter._dispatch(state, _task_create_event("toolu_b", "Second"))
    await adapter._dispatch(state, _task_create_result_event("toolu_b", "2"))
    await adapter._dispatch(
        state,
        _task_update_event("1", status="in_progress", activeForm="Doing first"),
    )
    snapshots = _todo_snapshots(emitted)
    assert snapshots[-1] == [
        {
            "content": "First",
            "status": "in_progress",
            "activeForm": "Doing first",
            "description": None,
        },
        {
            "content": "Second",
            "status": "pending",
            "activeForm": None,
            "description": None,
        },
    ]
    # Every snapshot merges under one stable card so the transcript shows a
    # single evolving todo list rather than one card per Task call.
    item_ids = {
        item[3]["item_id"]
        for item in emitted
        if item[3].get("item_type") == "todo_list"
    }
    assert len(item_ids) == 1


@pytest.mark.asyncio
async def test_task_update_deleted_removes_item() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(state, _task_create_event("toolu_a", "Throwaway"))
    await adapter._dispatch(state, _task_create_result_event("toolu_a", "1"))
    await adapter._dispatch(state, _task_update_event("1", status="deleted"))
    assert _todo_snapshots(emitted)[-1] == []
    assert state.task_tracker.is_empty


@pytest.mark.asyncio
async def test_task_update_for_unknown_id_materialises_stub() -> None:
    """A resumed session may patch a task whose create predates this process."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(
        state, _task_update_event("task-ghost", status="completed", subject="Recovered")
    )
    assert _todo_snapshots(emitted)[-1] == [
        {
            "content": "Recovered",
            "status": "completed",
            "activeForm": None,
            "description": None,
        }
    ]


@pytest.mark.asyncio
async def test_task_description_flows_into_snapshot_and_is_patchable() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    await adapter._dispatch(
        state, _task_create_event("toolu_a", "Write parser", "Create parser.py")
    )
    await adapter._dispatch(state, _task_create_result_event("toolu_a", "1"))
    assert _todo_snapshots(emitted)[-1] == [
        {
            "content": "Write parser",
            "status": "pending",
            "activeForm": None,
            "description": "Create parser.py",
        }
    ]
    # TaskUpdate can revise the description in place.
    await adapter._dispatch(
        state,
        _task_update_event(
            "1", status="in_progress", description="Create parser.py + wire it"
        ),
    )
    final = _todo_snapshots(emitted)[-1][0]
    assert final["status"] == "in_progress"
    assert final["description"] == "Create parser.py + wire it"


@pytest.mark.asyncio
async def test_respawn_restores_task_tracker_after_terminate() -> None:
    """The respawn paths (set_effort, reattach) terminate before the
    resume-spawn, so terminate_session must stash the folded todo state and the
    resume-spawn must restore it — otherwise post-respawn TaskUpdate deltas stub
    blank items for tasks created before the respawn."""
    adapter = _make_adapter([])
    state, _ = _attach_state(adapter, "sess")
    state.task_tracker.create("1", content="First", status="completed")
    state.task_card_item_id = "card-abc"
    await adapter.terminate_session("sess")
    assert "sess" in adapter._carried_task_state
    # The resume-spawn consumes the stash onto its fresh state.
    fresh, _ = _attach_state(adapter, "sess")
    adapter._restore_carried_task_state("sess", fresh)
    assert "sess" not in adapter._carried_task_state
    assert fresh.task_card_item_id == "card-abc"
    # A status-only update for the carried id patches in place, not a stub.
    fresh.task_tracker.update("1", status="in_progress")
    assert fresh.task_tracker.snapshot() == [
        {
            "content": "First",
            "status": "in_progress",
            "activeForm": None,
            "description": None,
        }
    ]


@pytest.mark.asyncio
async def test_terminate_does_not_stash_empty_tracker() -> None:
    adapter = _make_adapter([])
    _attach_state(adapter, "sess")
    await adapter.terminate_session("sess")
    assert "sess" not in adapter._carried_task_state


@pytest.mark.asyncio
async def test_restore_carried_task_state_tolerates_missing() -> None:
    adapter = _make_adapter([])
    fresh, _ = _attach_state(adapter)
    adapter._restore_carried_task_state("sess", fresh)
    assert fresh.task_tracker.is_empty


@pytest.mark.asyncio
async def test_discard_session_clears_carried_task_state() -> None:
    """A permanent delete drops the stash so it doesn't linger for a respawn
    that will never come."""
    adapter = _make_adapter([])
    state, _ = _attach_state(adapter, "sess")
    state.task_tracker.create("1", content="First")
    await adapter.terminate_session("sess")
    assert "sess" in adapter._carried_task_state
    adapter.discard_session("sess")
    assert "sess" not in adapter._carried_task_state


@pytest.mark.asyncio
async def test_task_get_and_list_results_are_suppressed() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    for tool in ("TaskGet", "TaskList"):
        tool_use_id = f"toolu_{tool}"
        await adapter._dispatch(
            state,
            {
                "type": "assistant",
                "message": {
                    "id": "m",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": tool,
                            "input": {},
                        }
                    ],
                },
            },
        )
        await adapter._dispatch(
            state,
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": "[]",
                        }
                    ]
                },
            },
        )
    assert emitted == []


@pytest.mark.asyncio
async def test_replays_real_task_stream_from_production_fixture() -> None:
    """Replay a real Claude Code session's Task stream (captured from persisted
    production events) and assert the fold reconstructs the full list. The
    fixture has 31 creates whose ids come only from the "Task #N created"
    result strings; if the string-id path failed and fell back to tool_use_id,
    the TaskUpdates would stub empty duplicates and inflate the count past 31."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    fixture = Path(__file__).parent / "fixtures" / "claude_task_stream.json"
    for event in json.loads(fixture.read_text()):
        await adapter._dispatch(state, event)
    final = _todo_snapshots(emitted)[-1]
    assert len(final) == 31
    assert all(todo["content"] for todo in final)
    assert all(todo["status"] == "completed" for todo in final)
    assert final[0]["content"] == "Write waypoint-subagents skill"
    assert final[-1]["content"] == "Commit 7: hydration fix"
