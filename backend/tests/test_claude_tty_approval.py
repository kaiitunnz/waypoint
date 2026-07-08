"""Unit tests for claude_tty live approval wiring.

Uses captured pane fixtures and a fake runtime/tmux so no live TUI is needed.
Verifies:
  - stable dialog (>=2 ticks) emits exactly ONE APPROVAL_REQUEST
  - single-tick dialog emits nothing (debounce)
  - respond_to_approval('decline') sends the No-option digit + Enter
  - respond_to_approval('approve') sends '1' + Enter
  - respond_to_approval with no pending returns False
  - wrong approval_id returns False without clearing pending
  - auto-mode session never calls capture_snapshot
  - vanished dialog clears pending
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from waypoint.backends.claude_tty._state import PendingTtyApproval, PendingTtyQuestion
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.claude_tty.transport import ClaudeTtyTransport
from waypoint.schemas import EventKind, SessionRecord, SessionSource, SessionStatus

FIXTURES = Path(__file__).parent / "fixtures" / "claude_tty_pane"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def _make_session(
    session_id: str = "sess-1",
    permission_mode: str | None = None,
    pane: str = "%0",
    status: SessionStatus = SessionStatus.RUNNING,
) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="claude_tty",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="test",
        cwd="/tmp",
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/structured.log",
        transport_state={
            "tmux_session": session_id,
            "tmux_window": "0",
            "tmux_pane": pane,
            "thread_id": "thread-1",
        },
        permission_mode=permission_mode,
    )


def _make_tailer(
    plugin: ClaudeTtyPlugin,
    runtime: MagicMock,
    session_id: str = "sess-1",
) -> TranscriptTailer:
    return TranscriptTailer(
        session_id=session_id,
        session_uuid="thread-1",
        cwd="/nonexistent",
        runtime=runtime,
        plugin=plugin,
    )


def _make_runtime(session: SessionRecord, snapshot: str) -> MagicMock:
    tmux = MagicMock()
    tmux.capture_snapshot = AsyncMock(return_value=snapshot)
    tmux.send_input = AsyncMock()
    tmux.send_bytes = AsyncMock()
    runtime = MagicMock()
    runtime.storage.get_session.return_value = session
    runtime.tmux = tmux
    runtime._emit_adapter_event = AsyncMock()
    return runtime


# ── tailer: dialog detection ──────────────────────────────────────────────────


async def test_single_tick_emits_nothing() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("approval_write.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()

    runtime._emit_adapter_event.assert_not_called()
    assert "sess-1" not in plugin._pending_approvals


async def test_stable_write_dialog_emits_approval_request() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("approval_write.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()  # tick 1: debounce
    runtime._emit_adapter_event.assert_not_called()

    await tailer._poll_dialog()  # tick 2: stable → emit
    runtime._emit_adapter_event.assert_called_once()

    call = runtime._emit_adapter_event.call_args
    assert call.args[1] is EventKind.APPROVAL_REQUEST
    meta = call.args[3]
    assert meta["tool_name"] == "Write"
    assert meta["tool_input"] == {"file_path": "probe_out.txt"}
    assert meta["method"] == "tty_permission"
    assert meta["status"] is SessionStatus.WAITING_INPUT
    assert "approval_id" in meta

    assert "sess-1" in plugin._pending_approvals
    pending = plugin._pending_approvals["sess-1"]
    assert pending.tool_name == "Write"
    assert pending.approve_number == 1
    assert pending.decline_number == 3


async def test_stable_plan_dialog_emits_exit_plan_approval() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    runtime = _make_runtime(session, _load("plan_approval.txt"))
    tailer = _make_tailer(plugin, runtime)
    # The plan-file Write the normalizer would have captured before the dialog.
    tailer._normalizer.last_plan_content = "# Plan\n\nAdd hello.py"
    tailer._normalizer.last_plan_path = "/home/u/.claude/plans/slug.md"

    await tailer._poll_dialog()  # tick 1: debounce
    runtime._emit_adapter_event.assert_not_called()

    await tailer._poll_dialog()  # tick 2: stable → emit
    runtime._emit_adapter_event.assert_called_once()

    call = runtime._emit_adapter_event.call_args
    assert call.args[1] is EventKind.APPROVAL_REQUEST
    assert call.args[2] == "Approve plan and exit plan mode"
    meta = call.args[3]
    assert meta["tool_name"] == "ExitPlanMode"
    assert meta["tool_input"]["plan"] == "# Plan\n\nAdd hello.py"
    assert meta["tool_input"]["planFilePath"] == "/home/u/.claude/plans/slug.md"
    assert meta["method"] == "tty_permission"
    assert meta["status"] is SessionStatus.WAITING_INPUT

    pending = plugin._pending_approvals["sess-1"]
    assert pending.tool_name == "ExitPlanMode"
    # No recorded pre-plan mode (launched in plan) → manual option → default.
    assert pending.approve_number == 2  # "Yes, manually approve edits"
    assert pending.restore_mode == "default"
    assert pending.decline_number is None  # decline → Esc keeps plan mode
    assert pending.is_plan is True


async def test_plan_dialog_restores_auto_mode_via_auto_option() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    session.transport_state["pre_plan_mode"] = "auto"
    runtime = _make_runtime(session, _load("plan_approval.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()
    await tailer._poll_dialog()

    pending = plugin._pending_approvals["sess-1"]
    assert pending.approve_number == 1  # "Yes, and use auto mode"
    assert pending.restore_mode == "auto"


async def test_plan_dialog_auto_pre_mode_without_auto_option_falls_back() -> None:
    # A subscription whose plan dialog omits the "auto mode" option: a pre-plan
    # auto session must fall back to manual → default, never widen.
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    session.transport_state["pre_plan_mode"] = "auto"
    screen = "\n".join(
        [
            "Claude is ready to execute. Would you like to proceed?",
            "  ❯ 1. Yes, manually approve edits",
            "    2. No, keep planning",
            "       shift+tab to approve with this feedback",
        ]
    )
    runtime = _make_runtime(session, screen)
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()
    await tailer._poll_dialog()

    pending = plugin._pending_approvals["sess-1"]
    assert pending.approve_number == 1  # the manual option (no auto option present)
    assert pending.restore_mode == "default"


async def test_plan_dialog_non_auto_pre_mode_falls_back_to_default() -> None:
    # acceptEdits has no plan-exit option, so it must not be approximated by the
    # broader "auto" option — fall back to manual → default (never widen).
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    session.transport_state["pre_plan_mode"] = "acceptEdits"
    runtime = _make_runtime(session, _load("plan_approval.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()
    await tailer._poll_dialog()

    pending = plugin._pending_approvals["sess-1"]
    assert pending.approve_number == 2
    assert pending.restore_mode == "default"


async def test_plan_dialog_surfaces_card_even_without_captured_body() -> None:
    # If the dialog is seen before the plan-file Write is normalized, the card
    # still surfaces (empty body) rather than hanging the session.
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    runtime = _make_runtime(session, _load("plan_approval.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()
    await tailer._poll_dialog()

    runtime._emit_adapter_event.assert_called_once()
    meta = runtime._emit_adapter_event.call_args.args[3]
    assert meta["tool_name"] == "ExitPlanMode"
    assert meta["tool_input"]["plan"] == ""
    # Falls back to the path named in the dialog footer.
    assert meta["tool_input"]["planFilePath"].endswith(
        "make-a-plan-to-linear-hennessy.md"
    )


async def test_stable_bash_dialog_emits_approval_request() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("approval_bash.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()
    await tailer._poll_dialog()

    runtime._emit_adapter_event.assert_called_once()
    meta = runtime._emit_adapter_event.call_args.args[3]
    assert meta["tool_name"] == "Bash"
    assert meta["tool_input"] == {"command": "mkdir /tmp/cc-tty-probe/probe_subdir"}


async def test_stable_dialog_emits_only_once() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("approval_write.txt"))
    tailer = _make_tailer(plugin, runtime)

    for _ in range(5):
        await tailer._poll_dialog()

    assert runtime._emit_adapter_event.call_count == 1


async def test_vanished_dialog_clears_pending() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    write_screen = _load("approval_write.txt")
    ready_screen = _load("ready.txt")

    call_count: list[int] = [0]

    async def _side_effect(pane: str) -> str:
        call_count[0] += 1
        return write_screen if call_count[0] <= 3 else ready_screen

    runtime = _make_runtime(session, write_screen)
    runtime.tmux.capture_snapshot = AsyncMock(side_effect=_side_effect)
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()  # tick 1
    await tailer._poll_dialog()  # tick 2 → emit + pending
    assert "sess-1" in plugin._pending_approvals

    await tailer._poll_dialog()  # tick 3: still same screen, already pending
    assert "sess-1" in plugin._pending_approvals

    await tailer._poll_dialog()  # tick 4: dialog gone → clear
    assert "sess-1" not in plugin._pending_approvals


async def test_no_reemit_after_response_while_dialog_lingers() -> None:
    # After respond_to_approval clears pending, the dialog can still be on the
    # pane for up to one poll before it redraws. The tailer must NOT re-emit a
    # duplicate approval for that same dialog.
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("approval_write.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()  # tick 1: debounce
    await tailer._poll_dialog()  # tick 2: emit + pending
    assert runtime._emit_adapter_event.call_count == 1

    # Simulate the transport answering the approval.
    del plugin._pending_approvals["sess-1"]

    await tailer._poll_dialog()  # tick 3: same dialog still rendered
    assert runtime._emit_adapter_event.call_count == 1  # no duplicate
    assert "sess-1" not in plugin._pending_approvals


async def test_reemits_distinct_dialog_after_screen_clears() -> None:
    # Once the pane redraws away (dialog gone) the surfaced-signature guard
    # resets, so a genuinely new dialog later is surfaced again.
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    write_screen = _load("approval_write.txt")
    ready_screen = _load("ready.txt")
    screens = [write_screen, write_screen, ready_screen, write_screen, write_screen]
    idx = [0]

    async def _side_effect(pane: str) -> str:
        screen = screens[min(idx[0], len(screens) - 1)]
        idx[0] += 1
        return screen

    runtime = _make_runtime(session, write_screen)
    runtime.tmux.capture_snapshot = AsyncMock(side_effect=_side_effect)
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()  # emit (debounce tick 1)
    await tailer._poll_dialog()  # emit (tick 2) → 1
    del plugin._pending_approvals["sess-1"]  # responded
    await tailer._poll_dialog()  # ready: screen cleared, guard resets
    await tailer._poll_dialog()  # write again, debounce
    await tailer._poll_dialog()  # write stable → re-emit → 2

    assert runtime._emit_adapter_event.call_count == 2


async def test_auto_mode_dialog_still_surfaced() -> None:
    # claude_tty's stored permission_mode is launch-fixed, but the TUI posture
    # can drift (shift+tab) into a prompting mode. Detection must not gate on
    # the stored mode: a dialog present on an "auto" session's pane is still
    # surfaced rather than silently missed (which would hang the session).
    for mode in ("auto", "bypassPermissions", "dontAsk"):
        plugin = ClaudeTtyPlugin()
        session = _make_session(permission_mode=mode)
        runtime = _make_runtime(session, _load("approval_write.txt"))
        tailer = _make_tailer(plugin, runtime)

        await tailer._poll_dialog()  # tick 1: debounce
        await tailer._poll_dialog()  # tick 2: stable → emit

        runtime.tmux.capture_snapshot.assert_called()
        runtime._emit_adapter_event.assert_called_once()
        assert session.id in plugin._pending_approvals


async def test_trust_prompt_is_accepted() -> None:
    # A fresh-cwd session opens at the workspace-trust prompt; the tailer must
    # accept it (bare Enter) so the session — including autonomous ones — does
    # not hang, without emitting any approval.
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("trust_dialog.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()

    runtime.tmux.send_input.assert_called_once_with("%0", "", submit=True)
    runtime._emit_adapter_event.assert_not_called()
    assert "sess-1" not in plugin._pending_approvals


async def test_non_approval_screen_does_not_emit() -> None:
    for fixture in ("ready.txt", "trust_dialog.txt", "model_selector.txt"):
        plugin = ClaudeTtyPlugin()
        session = _make_session()
        runtime = _make_runtime(session, _load(fixture))
        tailer = _make_tailer(plugin, runtime)

        await tailer._poll_dialog()
        await tailer._poll_dialog()

        runtime._emit_adapter_event.assert_not_called()


# ── tailer: AskUserQuestion dialog ───────────────────────────────────────────


async def test_question_dialog_dismissed_when_stable() -> None:
    # The popup withholds its questions from the transcript until resolved, so
    # the tailer Escs it (which flushes the record) and arms the normalizer to
    # surface it. No approval is emitted for it.
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("question_dialog.txt"))
    tailer = _make_tailer(plugin, runtime)

    await tailer._poll_dialog()  # tick 1: debounce
    runtime.tmux.send_bytes.assert_not_called()

    await tailer._poll_dialog()  # tick 2: stable → Esc + arm
    runtime.tmux.send_bytes.assert_called_once_with("%0", b"\x1b")
    assert tailer._normalizer._expect_dismissed_question is True
    assert tailer._question_dismissed is True
    runtime._emit_adapter_event.assert_not_called()


async def test_question_dialog_dismissed_only_once() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("question_dialog.txt"))
    tailer = _make_tailer(plugin, runtime)

    for _ in range(5):
        await tailer._poll_dialog()

    runtime.tmux.send_bytes.assert_called_once()


async def test_question_drain_registers_pending() -> None:
    # Once the armed normalizer surfaces the flushed tool_use as a WAITING_INPUT
    # card, the tailer registers the pending question so an answer can route.
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    runtime = _make_runtime(session, _load("ready.txt"))
    tailer = _make_tailer(plugin, runtime)
    tailer._normalizer.arm_question_dismissal()

    record = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "auq1",
                    "name": "AskUserQuestion",
                    "input": {"questions": [{"question": "Q", "options": []}]},
                }
            ],
        },
    }
    data = (json.dumps(record) + "\n").encode()
    tailer._read_new_bytes = lambda: data  # type: ignore[method-assign]

    await tailer._drain()

    assert "sess-1" in plugin._pending_questions
    assert plugin._pending_questions["sess-1"].tool_use_id == "auq1"


# ── plugin: answer_question ───────────────────────────────────────────────────


def _make_answer_runtime(session: SessionRecord, transport: MagicMock) -> MagicMock:
    runtime = MagicMock()
    runtime.transport_for.return_value = transport
    runtime._emit_adapter_event = AsyncMock()
    runtime._record_user_event = AsyncMock()
    runtime.storage.update_session = MagicMock(return_value=session)
    return runtime


async def test_answer_question_delivers_message_and_resolves_card() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_questions["sess-1"] = PendingTtyQuestion(
        approval_id="aid", tool_use_id="auq1"
    )
    transport = MagicMock()
    transport.send_input = AsyncMock()
    runtime = _make_answer_runtime(session, transport)
    answers = [{"question": "Tabs or spaces?", "answer": "Spaces"}]

    await plugin.answer_question(
        runtime, session, '"Tabs or spaces?"="Spaces"', "auq1", answers
    )

    # Answer is delivered to the pane as a normal user turn.
    transport.send_input.assert_awaited_once()
    sent = transport.send_input.call_args.args
    assert sent[0] is session
    assert "User has answered your questions" in sent[1]
    # A synthetic tool_result flips the surfaced card to answered.
    runtime._emit_adapter_event.assert_awaited_once()
    res = runtime._emit_adapter_event.call_args.args
    assert res[1] is EventKind.TOOL_RESULT
    assert res[3]["tool_use_id"] == "auq1"
    # The styled answers card carries the structured answers.
    extra = runtime._record_user_event.call_args.kwargs["extra_metadata"]
    assert extra["kind"] == "ask_user_question_answer"
    assert extra["answers"] == answers
    assert extra["tool_use_id"] == "auq1"
    assert "sess-1" not in plugin._pending_questions


async def test_answer_question_no_pending_raises() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    transport = MagicMock()
    transport.send_input = AsyncMock()
    runtime = _make_answer_runtime(session, transport)

    with pytest.raises(HTTPException) as exc:
        await plugin.answer_question(runtime, session, "x", None, None)
    assert exc.value.status_code == 400
    transport.send_input.assert_not_called()


async def test_answer_question_mismatched_tool_use_id_keeps_pending() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_questions["sess-1"] = PendingTtyQuestion(
        approval_id="aid", tool_use_id="auq1"
    )
    transport = MagicMock()
    transport.send_input = AsyncMock()
    runtime = _make_answer_runtime(session, transport)

    with pytest.raises(HTTPException):
        await plugin.answer_question(runtime, session, "x", "stale-id", None)
    transport.send_input.assert_not_called()
    assert "sess-1" in plugin._pending_questions


async def test_interrupt_clears_pending_question() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_questions["sess-1"] = PendingTtyQuestion(
        approval_id="aid", tool_use_id="auq1"
    )

    transport, tmux = _make_transport(plugin)
    await transport.interrupt(session)

    assert "sess-1" not in plugin._pending_questions
    tmux.send_bytes.assert_called_once_with("%0", b"\x1b")


# ── plugin: reconnect resume must not replay the transcript ──────────────────


async def _run_exited_reconnect(resumes: bool) -> bool:
    """Run restore_session on an EXITED session and report the tailer's
    start_at_end. resumes=True → an existing thread is reattached."""
    plugin = ClaudeTtyPlugin()
    session = _make_session(status=SessionStatus.EXITED)

    target = MagicMock(session="s", window="0", pane="%9", pane_pid=123)
    runtime = MagicMock()
    runtime.tmux.kill_session = AsyncMock()
    runtime.tmux.start_managed_session = AsyncMock(return_value=target)
    runtime.tmux.pipe_output = AsyncMock()
    runtime.tmux.resize_window = AsyncMock()
    runtime._find_launch_target.return_value = None
    runtime._command_for_backend.return_value = ["claude", "--resume", "thread-1"]
    runtime._record_system_event = AsyncMock()
    runtime.storage.update_session = MagicMock()

    plugin._conversation_exists = AsyncMock(return_value=resumes)  # type: ignore[method-assign]
    plugin._spawn_rate_limit_watcher = MagicMock()  # type: ignore[method-assign]

    captured: dict[str, bool] = {}

    def _fake_start_tailer(
        runtime: object,
        session_id: str,
        thread_id: str,
        cwd: str,
        *,
        start_at_end: bool = False,
        config_dir: str | None = None,
    ) -> None:
        captured["start_at_end"] = start_at_end

    plugin._start_tailer = _fake_start_tailer  # type: ignore[method-assign]

    await plugin.restore_session(runtime, session)
    return captured["start_at_end"]


async def test_reconnect_resume_tails_from_end_not_replay() -> None:
    # Resuming an existing thread reopens its populated transcript (already in
    # the event DB); the tailer must start at the end so it is not replayed.
    assert await _run_exited_reconnect(resumes=True) is True


async def test_reconnect_new_thread_tails_from_start() -> None:
    # A fresh thread starts an empty transcript, so reading from 0 is correct.
    assert await _run_exited_reconnect(resumes=False) is False


# ── transport: respond_to_approval ───────────────────────────────────────────


def _pending(
    approval_id: str = "aid-1",
    tool_name: str = "Write",
    target: str | None = "probe_out.txt",
    approve_number: int = 1,
    decline_number: int | None = 3,
) -> PendingTtyApproval:
    return PendingTtyApproval(
        approval_id=approval_id,
        tool_name=tool_name,
        target=target,
        approve_number=approve_number,
        decline_number=decline_number,
        signature=f"{tool_name}:{target}:Do you want to create {target}?",
    )


def _make_transport(plugin: ClaudeTtyPlugin) -> tuple[ClaudeTtyTransport, MagicMock]:
    tmux = MagicMock()
    tmux.send_input = AsyncMock()
    tmux.send_bytes = AsyncMock()
    runtime = MagicMock()
    runtime.tmux = tmux
    transport = ClaudeTtyTransport(runtime, plugin)
    return transport, tmux


async def test_respond_approve_sends_digit_1_enter() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_approvals["sess-1"] = _pending(approve_number=1)

    transport, tmux = _make_transport(plugin)
    result = await transport.respond_to_approval(session, "approve", None)

    assert result is True
    tmux.send_input.assert_called_once_with("%0", "1", submit=True)
    assert "sess-1" not in plugin._pending_approvals


async def test_respond_accept_sends_approve_digit_not_decline() -> None:
    # The frontend ApprovalCard sends decision="accept" for the primary approve;
    # it must drive the Yes digit, not fall through to the decline branch.
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_approvals["sess-1"] = _pending(approve_number=1, decline_number=3)

    transport, tmux = _make_transport(plugin)
    result = await transport.respond_to_approval(session, "accept", None)

    assert result is True
    tmux.send_input.assert_called_once_with("%0", "1", submit=True)
    assert "sess-1" not in plugin._pending_approvals


async def test_respond_decline_sends_no_digit_enter() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_approvals["sess-1"] = _pending(decline_number=3)

    transport, tmux = _make_transport(plugin)
    result = await transport.respond_to_approval(session, "decline", None)

    assert result is True
    tmux.send_input.assert_called_once_with("%0", "3", submit=True)
    assert "sess-1" not in plugin._pending_approvals


async def test_respond_decline_no_option_sends_esc() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_approvals["sess-1"] = _pending(decline_number=None)

    transport, tmux = _make_transport(plugin)
    result = await transport.respond_to_approval(session, "decline", None)

    assert result is True
    tmux.send_input.assert_not_called()
    tmux.send_bytes.assert_called_once_with("%0", b"\x1b")
    assert "sess-1" not in plugin._pending_approvals


def _make_plan_transport(
    plugin: ClaudeTtyPlugin,
) -> tuple[ClaudeTtyTransport, MagicMock]:
    runtime = MagicMock()
    runtime.tmux = MagicMock()
    runtime.tmux.send_input = AsyncMock()
    runtime.tmux.send_bytes = AsyncMock()
    runtime.update_session_fields = AsyncMock()
    return ClaudeTtyTransport(runtime, plugin), runtime


async def test_respond_approve_plan_presses_manual_digit_and_exits_plan_mode() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    pending = _pending(
        tool_name="ExitPlanMode", target=None, approve_number=2, decline_number=None
    )
    pending.is_plan = True
    pending.restore_mode = "default"
    plugin._pending_approvals["sess-1"] = pending

    transport, runtime = _make_plan_transport(plugin)
    result = await transport.respond_to_approval(session, "approve", None)

    assert result is True
    runtime.tmux.send_input.assert_called_once_with("%0", "2", submit=True)
    runtime.update_session_fields.assert_awaited_once_with(
        "sess-1", permission_mode="default"
    )


async def test_respond_approve_plan_restores_auto_mode() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    pending = _pending(
        tool_name="ExitPlanMode", target=None, approve_number=1, decline_number=None
    )
    pending.is_plan = True
    pending.restore_mode = "auto"
    plugin._pending_approvals["sess-1"] = pending

    transport, runtime = _make_plan_transport(plugin)
    result = await transport.respond_to_approval(session, "approve", None)

    assert result is True
    runtime.tmux.send_input.assert_called_once_with("%0", "1", submit=True)
    runtime.update_session_fields.assert_awaited_once_with(
        "sess-1", permission_mode="auto"
    )


async def test_respond_decline_plan_sends_esc_and_keeps_plan_mode() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session(permission_mode="plan")
    pending = _pending(
        tool_name="ExitPlanMode", target=None, approve_number=2, decline_number=None
    )
    pending.is_plan = True
    plugin._pending_approvals["sess-1"] = pending

    transport, runtime = _make_plan_transport(plugin)
    result = await transport.respond_to_approval(session, "decline", None)

    assert result is True
    runtime.tmux.send_bytes.assert_called_once_with("%0", b"\x1b")
    runtime.tmux.send_input.assert_not_called()
    runtime.update_session_fields.assert_not_awaited()


async def test_respond_no_pending_returns_false() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()

    transport, tmux = _make_transport(plugin)
    result = await transport.respond_to_approval(session, "approve", None)

    assert result is False
    tmux.send_input.assert_not_called()


async def test_respond_wrong_approval_id_returns_false() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_approvals["sess-1"] = _pending(approval_id="correct-id")

    transport, _ = _make_transport(plugin)
    result = await transport.respond_to_approval(
        session, "approve", None, approval_id="wrong-id"
    )

    assert result is False
    assert "sess-1" in plugin._pending_approvals  # not cleared


async def test_interrupt_clears_pending_approval_and_sends_esc() -> None:
    # Esc declines an open dialog; dropping the pending entry up front closes
    # the window where a racing approve would fire a digit at the ready prompt.
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_approvals["sess-1"] = _pending()

    transport, tmux = _make_transport(plugin)
    await transport.interrupt(session)

    assert "sess-1" not in plugin._pending_approvals
    tmux.send_bytes.assert_called_once_with("%0", b"\x1b")
    assert not transport.has_pending_approval(session)


async def test_respond_correct_approval_id_resolves() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    plugin._pending_approvals["sess-1"] = _pending(approval_id="correct-id")

    transport, tmux = _make_transport(plugin)
    result = await transport.respond_to_approval(
        session, "approve", None, approval_id="correct-id"
    )

    assert result is True
    tmux.send_input.assert_called_once()
    assert "sess-1" not in plugin._pending_approvals


def test_has_pending_approval_reflects_plugin_state() -> None:
    plugin = ClaudeTtyPlugin()
    session = _make_session()
    transport, _ = _make_transport(plugin)

    assert not transport.has_pending_approval(session)

    plugin._pending_approvals["sess-1"] = _pending()
    assert transport.has_pending_approval(session)

    del plugin._pending_approvals["sess-1"]
    assert not transport.has_pending_approval(session)
