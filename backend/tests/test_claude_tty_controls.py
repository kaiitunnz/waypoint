"""Unit tests for claude_tty control swaps, custom args, and thread import.

claude_tty has no in-process knobs: model/effort/permission-mode swaps all
relaunch the pane with ``--resume <thread>`` and a rebuilt flag set. These
tests cover the arg scrubber, the restart helper (idle guard, no-op
short-circuit, flag rebuild), the effort return contract, custom-arg
validation, thread-discovery dedup, and the resume-import path — all against
fakes so no live TUI/tmux is needed.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from waypoint.backends.claude_code.schemas import ClaudeThreadImportRequest
from waypoint.backends.claude_code.threads import ClaudeThreadInfo
from waypoint.backends.claude_tty import plugin as plugin_mod
from waypoint.backends.claude_tty.plugin import (
    ClaudeTtyPlugin,
    _scrub_session_args,
    _validate_custom_args,
)
from waypoint.schemas import (
    SessionCreateRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)


def _make_session(
    *,
    session_id: str = "sess-1",
    status: SessionStatus = SessionStatus.IDLE,
    model: str | None = None,
    effort: str | None = None,
    permission_mode: str | None = None,
    launch_args: list[str] | None = None,
    thread_id: str | None = "thread-1",
) -> SessionRecord:
    now = datetime.now(UTC)
    state: dict[str, object] = {
        "tmux_session": session_id,
        "tmux_window": "0",
        "tmux_pane": "%0",
        "launch_args": (
            launch_args if launch_args is not None else ["--session-id", "thread-1"]
        ),
    }
    if thread_id is not None:
        state["thread_id"] = thread_id
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
        transport_state=state,
        model=model,
        effort=effort,
        permission_mode=permission_mode,
    )


def _restart_runtime() -> tuple[MagicMock, MagicMock]:
    target = MagicMock(session="s-new", window="0", pane="%9", pane_pid=123)
    runtime = MagicMock()
    runtime.tmux.kill_session = AsyncMock()
    runtime.tmux.start_managed_session = AsyncMock(return_value=target)
    runtime.tmux.pipe_output = AsyncMock()
    runtime.tmux.resize_window = AsyncMock()
    runtime._find_launch_target.return_value = None
    runtime._command_for_backend.return_value = ["claude", "--resume", "thread-1"]
    runtime._record_system_event = AsyncMock()
    runtime.storage.update_session = MagicMock()
    return runtime, target


def _stub_lifecycle(plugin: ClaudeTtyPlugin) -> dict[str, object]:
    captured: dict[str, object] = {}

    def _fake_start_tailer(
        runtime: object,
        session_id: str,
        thread_id: str,
        cwd: str,
        *,
        start_at_end: bool = False,
    ) -> None:
        captured["tailer_thread_id"] = thread_id
        captured["start_at_end"] = start_at_end

    plugin._start_tailer = _fake_start_tailer  # type: ignore[method-assign]
    plugin._spawn_rate_limit_watcher = MagicMock()  # type: ignore[method-assign]
    return captured


# ── _scrub_session_args ───────────────────────────────────────────────────────


def test_scrub_strips_managed_flags_keeps_custom() -> None:
    args = [
        "--session-id",
        "uuid-1",
        "--model",
        "opus",
        "--effort",
        "high",
        "--permission-mode",
        "plan",
        "--fork-session",
        "--verbose",
        "--add-dir",
        "/work",
    ]
    assert _scrub_session_args(args) == ["--verbose", "--add-dir", "/work"]


def test_scrub_strips_resume() -> None:
    assert _scrub_session_args(["--resume", "uuid-1", "--keep"]) == ["--keep"]


# ── _restart_with_args ────────────────────────────────────────────────────────


async def test_restart_idle_guard_raises_when_running() -> None:
    plugin = ClaudeTtyPlugin()
    _stub_lifecycle(plugin)
    session = _make_session(status=SessionStatus.RUNNING, model="opus")
    runtime, _ = _restart_runtime()

    with pytest.raises(HTTPException) as exc:
        await plugin._restart_with_args(runtime, session, model="sonnet")

    assert exc.value.status_code == 400
    assert "model" in exc.value.detail
    runtime.tmux.start_managed_session.assert_not_called()


async def test_restart_unchanged_value_returns_false_without_relaunch() -> None:
    plugin = ClaudeTtyPlugin()
    _stub_lifecycle(plugin)
    session = _make_session(model="opus")
    runtime, _ = _restart_runtime()

    result = await plugin._restart_with_args(runtime, session, model="opus")

    assert result is False
    runtime.tmux.kill_session.assert_not_called()
    runtime.tmux.start_managed_session.assert_not_called()
    runtime.storage.update_session.assert_not_called()


async def test_restart_rebuilds_resume_flags_preserving_custom_args() -> None:
    plugin = ClaudeTtyPlugin()
    captured = _stub_lifecycle(plugin)
    session = _make_session(
        model="opus",
        effort="high",
        launch_args=["--session-id", "thread-1", "--model", "opus", "--verbose"],
    )
    runtime, _ = _restart_runtime()

    result = await plugin._restart_with_args(runtime, session, model="sonnet")

    assert result is True
    # New value plus the untouched effort, custom arg survives, identity flag dropped.
    built_args = runtime._command_for_backend.call_args.args[1]
    assert built_args == [
        "--resume",
        "thread-1",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--verbose",
    ]
    runtime.tmux.kill_session.assert_awaited_once()
    # Stored state carries the rebuilt args and the new tmux ids.
    update_kwargs = runtime.storage.update_session.call_args.kwargs
    assert update_kwargs["status"] is SessionStatus.STARTING
    assert update_kwargs["transport_state"]["launch_args"] == built_args
    assert update_kwargs["transport_state"]["thread_id"] == "thread-1"
    # Resumed transcript is already populated → tail from the end.
    assert captured["start_at_end"] is True


async def test_restart_clears_pending_approval_and_tailer() -> None:
    plugin = ClaudeTtyPlugin()
    _stub_lifecycle(plugin)
    plugin._pending_approvals["sess-1"] = MagicMock()
    session = _make_session(model="opus")
    runtime, _ = _restart_runtime()

    await plugin._restart_with_args(runtime, session, model="sonnet")

    assert "sess-1" not in plugin._pending_approvals


# ── apply_effort return contract ──────────────────────────────────────────────


async def test_apply_effort_returns_true_on_change() -> None:
    plugin = ClaudeTtyPlugin()
    _stub_lifecycle(plugin)
    session = _make_session(effort="low")
    runtime, _ = _restart_runtime()

    assert await plugin.apply_effort(runtime, session, "high") is True


async def test_apply_effort_returns_false_when_unchanged() -> None:
    plugin = ClaudeTtyPlugin()
    _stub_lifecycle(plugin)
    session = _make_session(effort="high")
    runtime, _ = _restart_runtime()

    assert await plugin.apply_effort(runtime, session, "high") is False
    runtime.tmux.start_managed_session.assert_not_called()


# ── custom CLI args validation ────────────────────────────────────────────────


def test_validate_custom_args_rejects_reserved() -> None:
    for arg in ("--model=opus", "--resume", "--session-id", "--permission-mode"):
        with pytest.raises(HTTPException) as exc:
            _validate_custom_args([arg])
        assert exc.value.status_code == 400


def test_validate_custom_args_allows_passthrough() -> None:
    _validate_custom_args(["--verbose", "--add-dir", "/work"])


async def test_create_session_rejects_reserved_flags() -> None:
    plugin = ClaudeTtyPlugin()
    runtime = MagicMock()
    request = SessionCreateRequest(
        backend="claude_tty", cwd="/tmp", args=["--model", "opus"]
    )

    with pytest.raises(HTTPException) as exc:
        await plugin.create_session(
            runtime,
            request,
            session_id="sess-1",
            launch_target=None,
            title="t",
            raw_log=Path("/tmp/raw.log"),
            structured_log=Path("/tmp/events.jsonl"),
            git_meta=MagicMock(repo_name=None, branch=None),
            permission_mode=None,
            resolved_model=None,
            resolved_effort=None,
        )

    assert exc.value.status_code == 400
    # Bailed before touching tmux.
    runtime.tmux.start_managed_session.assert_not_called()


# ── list_threads dedup ────────────────────────────────────────────────────────


def _thread_info(thread_id: str) -> ClaudeThreadInfo:
    now = datetime.now(UTC)
    return ClaudeThreadInfo(
        id=thread_id,
        cwd="/work",
        title=f"thread {thread_id}",
        branch=None,
        repo_name="work",
        preview="hello",
        created_at=now,
        updated_at=now,
    )


async def test_list_threads_dedups_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        plugin_mod,
        "list_local_claude_threads",
        lambda: [_thread_info("t-a"), _thread_info("t-b")],
    )
    plugin = ClaudeTtyPlugin()
    runtime = MagicMock()
    runtime.storage.list_sessions.return_value = [
        _make_session(session_id="s1", thread_id="t-a"),
    ]

    summaries = await plugin.list_threads(runtime)

    assert [s.id for s in summaries] == ["t-b"]


async def test_list_threads_remote_returns_empty() -> None:
    plugin = ClaudeTtyPlugin()
    runtime = MagicMock()

    assert await plugin.list_threads(runtime, launch_target_id="remote") == []


# ── import_thread ─────────────────────────────────────────────────────────────


async def test_import_thread_resumes_and_starts_tailer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info = _thread_info("imp-1")
    info.cwd = str(tmp_path)
    monkeypatch.setattr(plugin_mod, "find_local_claude_thread", lambda thread_id: info)
    plugin = ClaudeTtyPlugin()
    captured = _stub_lifecycle(plugin)

    target = MagicMock(session="s", window="0", pane="%1", pane_pid=7)
    runtime = MagicMock()
    runtime._generate_session_id.return_value = "claude_tty-abcd"
    runtime._session_dir.return_value = tmp_path
    runtime._command_for_backend.return_value = ["claude", "--resume", "imp-1"]
    runtime.tmux.start_managed_session = AsyncMock(return_value=target)
    runtime.tmux.pipe_output = AsyncMock()
    runtime.tmux.resize_window = AsyncMock()
    runtime.storage.list_sessions.return_value = []
    runtime.storage.create_session = MagicMock()
    runtime._record_system_event = AsyncMock()
    created_holder: dict[str, SessionRecord] = {}

    def _create(session: SessionRecord) -> None:
        created_holder["session"] = session

    runtime.storage.create_session.side_effect = _create
    runtime.get_session.side_effect = lambda sid: created_holder["session"]

    request = ClaudeThreadImportRequest(thread_id="imp-1")
    result = await plugin.import_thread(runtime, request)

    assert result.transport_state["thread_id"] == "imp-1"
    assert result.transport_state["launch_args"] == ["--resume", "imp-1"]
    assert result.backend == "claude_tty"
    # Resume tails from the end of the already-populated transcript.
    assert captured["tailer_thread_id"] == "imp-1"
    assert captured["start_at_end"] is True
    # Resumed thread import recorded with provenance metadata.
    meta = runtime._record_system_event.call_args.kwargs["metadata"]
    assert meta["imported_thread_id"] == "imp-1"


async def test_import_thread_missing_raises_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin_mod, "find_local_claude_thread", lambda thread_id: None)
    plugin = ClaudeTtyPlugin()
    runtime = MagicMock()

    with pytest.raises(HTTPException) as exc:
        await plugin.import_thread(runtime, ClaudeThreadImportRequest(thread_id="gone"))

    assert exc.value.status_code == 404


async def test_import_thread_already_imported_raises_400(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    info = _thread_info("dup-1")
    info.cwd = str(tmp_path)
    monkeypatch.setattr(plugin_mod, "find_local_claude_thread", lambda thread_id: info)
    plugin = ClaudeTtyPlugin()
    runtime = MagicMock()
    runtime.storage.list_sessions.return_value = [
        _make_session(session_id="s1", thread_id="dup-1"),
    ]

    with pytest.raises(HTTPException) as exc:
        await plugin.import_thread(
            runtime, ClaudeThreadImportRequest(thread_id="dup-1")
        )

    assert exc.value.status_code == 400


async def test_import_thread_remote_not_supported() -> None:
    plugin = ClaudeTtyPlugin()
    runtime = MagicMock()

    with pytest.raises(HTTPException) as exc:
        await plugin.import_thread(
            runtime,
            ClaudeThreadImportRequest(thread_id="x", launch_target_id="remote"),
        )

    assert exc.value.status_code == 400
