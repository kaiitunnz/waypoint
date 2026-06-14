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

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from waypoint.backends.claude_tty._state import PendingTtyApproval
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
) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="claude_tty",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="test",
        cwd="/tmp",
        status=SessionStatus.RUNNING,
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


async def test_auto_mode_never_captures_pane() -> None:
    for mode in ("auto", "bypassPermissions", "dontAsk"):
        plugin = ClaudeTtyPlugin()
        session = _make_session(permission_mode=mode)
        runtime = _make_runtime(session, _load("approval_write.txt"))
        tailer = _make_tailer(plugin, runtime)

        await tailer._poll_dialog()

        runtime.tmux.capture_snapshot.assert_not_called()
        runtime._emit_adapter_event.assert_not_called()


async def test_non_approval_screen_does_not_emit() -> None:
    for fixture in ("ready.txt", "trust_dialog.txt", "model_selector.txt"):
        plugin = ClaudeTtyPlugin()
        session = _make_session()
        runtime = _make_runtime(session, _load(fixture))
        tailer = _make_tailer(plugin, runtime)

        await tailer._poll_dialog()
        await tailer._poll_dialog()

        runtime._emit_adapter_event.assert_not_called()


# ── transport: respond_to_approval ───────────────────────────────────────────


def _pending(
    approval_id: str = "aid-1",
    tool_name: str = "Write",
    target: str = "probe_out.txt",
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
