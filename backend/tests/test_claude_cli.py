import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.claude_code.adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    ClaudeCliAdapter,
    ClaudeSessionState,
    claude_cli_mode_for,
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
        wait_task=asyncio.create_task(asyncio.sleep(0)),
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
    from waypoint.backends.claude_code.adapter import ClaudePendingApproval

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
async def test_await_approval_auto_allows_in_auto_mode() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.permission_mode = "auto"

    decision = await adapter.await_approval(
        {
            "waypoint_session_id": "sess",
            "tool_use_id": "tu-1",
            "tool_name": "Bash",
            "tool_input": {"command": "rm -rf /"},
        }
    )

    assert decision == {
        "permissionDecision": "allow",
        "permissionDecisionReason": "auto-approved by mode=auto",
    }
    # Auto-approved hooks should never surface an approval card.
    assert not any(item[1] == EventKind.APPROVAL_REQUEST for item in emitted)
    assert not state.pending


@pytest.mark.asyncio
async def test_await_approval_accept_edits_only_auto_allows_edit_tools() -> None:
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.permission_mode = "acceptEdits"

    edit_decision = await adapter.await_approval(
        {
            "waypoint_session_id": "sess",
            "tool_use_id": "tu-edit",
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/x.py"},
        }
    )
    assert edit_decision["permissionDecision"] == "allow"

    # Bash still surfaces the approval card so the user can intervene.
    bash_task = asyncio.create_task(
        adapter.await_approval(
            {
                "waypoint_session_id": "sess",
                "tool_use_id": "tu-bash",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            }
        )
    )
    await asyncio.sleep(0)
    assert "tu-bash" in state.pending
    state.pending["tu-bash"].future.set_result(
        {"permissionDecision": "deny", "permissionDecisionReason": "user said no"}
    )
    bash_decision = await bash_task
    assert bash_decision["permissionDecision"] == "deny"


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
async def test_await_approval_for_ask_user_question_skips_approval_card() -> None:
    """AskUserQuestion goes through the PreToolUse hook so the binary blocks
    waiting for our verdict, but the question UI is already rendered via
    the tool_call event — emitting an APPROVAL_REQUEST too would surface
    the same prompt twice."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    _attach_state(adapter)

    payload = {
        "waypoint_session_id": "sess",
        "tool_use_id": "toolu_ask",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [{"question": "ok?", "options": []}]},
    }
    decision_task = asyncio.create_task(adapter.await_approval(payload))
    for _ in range(50):
        if adapter.has_pending_ask_question("sess"):
            break
        await asyncio.sleep(0.01)
    assert adapter.has_pending_ask_question("sess")
    assert not any(item[1] == EventKind.APPROVAL_REQUEST for item in emitted)
    handled = await adapter.respond_to_ask_question(
        "sess", "**Plan target**: Trivial wrapper-test plan", "toolu_ask"
    )
    assert handled is True
    decision = await decision_task
    assert decision == {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
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
        "waypoint_session_id": "sess",
        "tool_use_id": "toolu_ask",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": []},
    }
    task = asyncio.create_task(adapter.await_approval(payload))
    for _ in range(50):
        if adapter.has_pending_ask_question("sess"):
            break
        await asyncio.sleep(0.01)
    assert adapter.has_pending_ask_question("sess")
    await adapter.respond_to_ask_question("sess", "answer", "toolu_ask")
    await task


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
    state, _ = _attach_state(adapter)
    state.permission_mode = "plan"

    decision = await adapter.await_approval(
        {
            "waypoint_session_id": "sess",
            "tool_use_id": "toolu_write",
            "tool_name": "Write",
            "tool_input": {
                "file_path": "/Users/me/.claude/plans/my-plan.md",
                "content": "# plan",
            },
        }
    )
    assert decision["permissionDecision"] == "allow"
    assert state.last_plan_path == "/Users/me/.claude/plans/my-plan.md"
    # Approval card must NOT have been emitted for the meta-write.
    assert not any(item[1] == EventKind.APPROVAL_REQUEST for item in emitted)


@pytest.mark.asyncio
async def test_plan_mode_does_not_auto_approve_non_plan_writes() -> None:
    """Writes outside the ~/.claude/plans/ tree should still surface the
    approval card; only the binary's own plan-file path is implicit."""
    emitted: list = []
    adapter = _make_adapter(emitted)
    state, _ = _attach_state(adapter)
    state.permission_mode = "plan"

    payload = {
        "waypoint_session_id": "sess",
        "tool_use_id": "toolu_write_src",
        "tool_name": "Write",
        "tool_input": {"file_path": "/repo/src/main.py", "content": "x"},
    }
    task = asyncio.create_task(adapter.await_approval(payload))
    for _ in range(50):
        if adapter.has_pending_approval("sess"):
            break
        await asyncio.sleep(0.01)
    assert adapter.has_pending_approval("sess")
    assert state.last_plan_path is None
    await adapter.respond_to_approval("sess", "decline")
    await task


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
    state, _ = _attach_state(adapter)
    state.permission_mode = "plan"
    state.last_plan_path = "/Users/me/.claude/plans/my-plan.md"

    mode_calls: list[str] = []

    async def fake_set_mode(session_id: str, mode: str) -> None:
        mode_calls.append(mode)
        state.permission_mode = mode

    monkeypatch.setattr(adapter, "set_permission_mode", fake_set_mode)

    plan_body = "## Plan\n1. Read files\n2. Apply edits"
    payload = {
        "waypoint_session_id": "sess",
        "tool_use_id": "toolu_plan",
        "tool_name": "ExitPlanMode",
        "tool_input": {"plan": plan_body},
    }
    decision_task = asyncio.create_task(adapter.await_approval(payload))
    for _ in range(50):
        if adapter.has_pending_approval("sess"):
            break
        await asyncio.sleep(0.01)
    await adapter.respond_to_approval("sess", "approve")
    decision = await decision_task

    reason = decision["permissionDecisionReason"]
    assert decision["permissionDecision"] == "deny"
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
        "waypoint_session_id": "sess",
        "tool_use_id": "toolu_plan",
        "tool_name": "ExitPlanMode",
        "tool_input": {"plan": "## Plan"},
    }
    task = asyncio.create_task(adapter.await_approval(payload))
    for _ in range(50):
        if adapter.has_pending_approval("sess"):
            break
        await asyncio.sleep(0.01)
    await adapter.respond_to_approval("sess", "approve")
    await task

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
    state, _ = _attach_state(adapter)
    state.permission_mode = "plan"

    payload = {
        "waypoint_session_id": "sess",
        "tool_use_id": "toolu_plan",
        "tool_name": "ExitPlanMode",
        "tool_input": {"plan": "## Plan\n- step"},
    }
    decision_task = asyncio.create_task(adapter.await_approval(payload))
    for _ in range(50):
        if adapter.has_pending_approval("sess"):
            break
        await asyncio.sleep(0.01)
    await adapter.respond_to_approval("sess", "decline")
    decision = await decision_task

    assert decision["permissionDecision"] == "deny"
    assert "declined your plan" in decision["permissionDecisionReason"]
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
