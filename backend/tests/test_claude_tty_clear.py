"""claude_tty following a composer ``/clear`` onto the conversation it starts."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.backends import transcript_fs_remote_script as script
from waypoint.backends.claude_code.threads import encode_project_dir
from waypoint.backends.claude_tty import plugin as plugin_mod
from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    RemoteClaudeTranscriptByteSource,
    TranscriptRead,
)
from waypoint.backends.claude_tty.normalize import TranscriptNormalizer
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.claude_tty.transport import ClaudeTtyTransport
from waypoint.backends.tmux.transport import TmuxTransport
from waypoint.backends.transcript_fs_remote import RemoteTranscriptFilesystem
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import (
    EventKind,
    SessionContextUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)

PARENT = "11111111-1111-1111-1111-111111111111"
CHILD = "22222222-2222-2222-2222-222222222222"
OTHER = "33333333-3333-3333-3333-333333333333"
CWD = "/repo/app"


def _session(
    status: SessionStatus = SessionStatus.IDLE, thread_id: str = PARENT
) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id="s1",
        backend="claude_code",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="t",
        cwd=CWD,
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/r",
        structured_log_path="/e",
        transport_state={"thread_id": thread_id, "tmux_pane": "%0"},
        context_usage=SessionContextUsage(
            used_tokens=10,
            context_window_tokens=100,
            updated_at=now,
            source="claude_code",
        ),
    )


def _launch_target() -> SshLaunchTargetConfig:
    return SshLaunchTargetConfig(
        id="r", name="R", ssh_destination="h", ssh_bin="/bin/sh"
    )


def _header(session_id: str, parent: str, via: str = "clear") -> list[dict[str, Any]]:
    return [
        {"type": "mode", "mode": "normal", "sessionId": session_id},
        {"type": "session-owner", "sessionId": session_id},
        {
            "type": "derived-from",
            "v": 1,
            "sessionId": session_id,
            "sessions": [{"type": "claude_code_session", "id": parent, "via": via}],
        },
    ]


def _write(path: Path, records: list[dict[str, Any]], age: float = 0.0) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))


# ── header scan (shared by the local source and the remote op) ───────────────


def test_scan_matches_clear_successor(tmp_path: Path) -> None:
    _write(tmp_path / f"{PARENT}.jsonl", [{"type": "user", "message": {}}])
    _write(tmp_path / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    assert script.find_clear_successors(str(tmp_path), "*.jsonl", PARENT, 60) == [CHILD]


@pytest.mark.parametrize(
    ("records", "age"),
    [
        (_header(CHILD, PARENT, via="fork"), 0.0),
        (_header(CHILD, OTHER), 0.0),
        (_header(CHILD, PARENT), 600.0),
    ],
    ids=["other-via", "other-parent", "stale"],
)
def test_scan_ignores_non_matches(
    tmp_path: Path, records: list[dict[str, Any]], age: float
) -> None:
    _write(tmp_path / f"{CHILD}.jsonl", records, age=age)
    assert script.find_clear_successors(str(tmp_path), "*.jsonl", PARENT, 60) == []


def test_scan_tolerates_garbage_and_missing_root(tmp_path: Path) -> None:
    (tmp_path / f"{CHILD}.jsonl").write_bytes(b"\x00not json\n[1, 2]\n")
    assert script.find_clear_successors(str(tmp_path), "*.jsonl", PARENT, 60) == []
    missing = str(tmp_path / "missing")
    assert script.find_clear_successors(missing, "*.jsonl", PARENT, 60) == []


def test_op_emits_ids_and_validates_age(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "projects" / "-repo-app"
    project.mkdir(parents=True)
    _write(project / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    script.op_find_clear_successors(str(tmp_path), "projects/*/*.jsonl", PARENT, "60")
    script.op_find_clear_successors(str(tmp_path), "projects/*/*.jsonl", PARENT, "x")
    first, second = capsys.readouterr().out.splitlines()
    assert json.loads(first.removeprefix(script.SENTINEL)) == {"ids": [CHILD]}
    assert "error" in json.loads(second.removeprefix(script.SENTINEL))


def test_remote_fs_passes_args_and_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    fs = RemoteTranscriptFilesystem(_launch_target())
    calls: list[tuple[str, ...]] = []
    replies: list[dict[str, Any]] = [{"ids": [CHILD, 7]}, {"error": "boom"}]

    def fake_run(op: str, *args: str) -> dict[str, Any]:
        calls.append((op, *args))
        return replies.pop(0)

    monkeypatch.setattr(fs, "_run", fake_run)
    pattern = "projects/*/*.jsonl"
    assert fs.find_clear_successors("~/.claude", pattern, PARENT, 60) == [CHILD]
    assert fs.find_clear_successors("~/.claude", pattern, PARENT, 60) == []
    assert calls[0] == ("find_clear_successors", "~/.claude", pattern, PARENT, "60")


# ── byte sources ─────────────────────────────────────────────────────────────


def _project_dir(config_dir: Path) -> Path:
    project = config_dir / "projects" / encode_project_dir(CWD)
    project.mkdir(parents=True, exist_ok=True)
    return project


def test_local_successor_found_without_parent_file(tmp_path: Path) -> None:
    # A /clear before the first turn names a parent that was never written.
    _write(_project_dir(tmp_path) / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    src = LocalTranscriptByteSource(CWD, PARENT, str(tmp_path))
    assert src.find_clear_successor(PARENT, 60) == CHILD


def test_local_successor_missing_or_ambiguous(tmp_path: Path) -> None:
    src = LocalTranscriptByteSource(CWD, PARENT, str(tmp_path))
    assert src.find_clear_successor(PARENT, 60) is None
    project = _project_dir(tmp_path)
    _write(project / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    _write(project / f"{OTHER}.jsonl", _header(OTHER, PARENT))
    assert src.find_clear_successor(PARENT, 60) is None


def test_local_successor_found_in_another_project_folder(tmp_path: Path) -> None:
    # The session moved (a worktree) after the source's folder was derived.
    moved = tmp_path / "projects" / encode_project_dir(f"{CWD}/.claude/worktrees/w")
    moved.mkdir(parents=True)
    _write(moved / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    src = LocalTranscriptByteSource(CWD, PARENT, str(tmp_path))
    assert src.find_clear_successor(PARENT, 60) == CHILD


def test_remote_successor_searches_config_root() -> None:
    runtime = MagicMock()
    runtime.storage.get_session.return_value = _session()
    src = RemoteClaudeTranscriptByteSource(
        runtime, "s1", PARENT, _launch_target(), "/home/u/.claude-work"
    )
    fs = MagicMock()
    fs.find_clear_successors.return_value = [CHILD]
    src._fs = fs
    assert src.find_clear_successor(PARENT, 60) == CHILD
    fs.find_clear_successors.assert_called_once_with(
        "/home/u/.claude-work", "projects/*/*.jsonl", PARENT, 60
    )
    fs.find_clear_successors.return_value = [CHILD, OTHER]
    assert src.find_clear_successor(PARENT, 60) is None


# ── normalizer note ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "expected"),
    [("clear", "Context cleared"), ("status", "Command complete")],
)
def test_local_command_note(command: str, expected: str) -> None:
    events = TranscriptNormalizer().process_record(
        {
            "type": "system",
            "subtype": "local_command",
            "content": "<local-command-stdout></local-command-stdout>",
            "commandRun": {"command": command, "args": ""},
        }
    )
    assert [(e.kind, e.text, e.status) for e in events] == [
        (EventKind.SYSTEM_NOTE, expected, SessionStatus.IDLE)
    ]


# ── plugin rebind ────────────────────────────────────────────────────────────


class _Store:
    def __init__(self, session: SessionRecord) -> None:
        self.session = session

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self.session

    def set_state(self, **updates: str) -> None:
        state = {**self.session.transport_state, **updates}
        self.session = self.session.model_copy(update={"transport_state": state})


def _runtime(session: SessionRecord) -> MagicMock:
    runtime = MagicMock()
    store = _Store(session)
    runtime.storage = store
    runtime._find_launch_target.return_value = None

    async def update_session_fields(session_id: str, **updates: Any) -> SessionRecord:
        store.session = store.session.model_copy(update=updates)
        return store.session

    runtime.update_session_fields = AsyncMock(side_effect=update_session_fields)
    runtime.questions.close_open = AsyncMock()
    return runtime


class _FakeTailer:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.ran = False

    async def run(self) -> None:
        self.ran = True


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch) -> ClaudeTtyPlugin:
    monkeypatch.setattr(plugin_mod, "_CLEAR_LOOKUP_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(plugin_mod, "TranscriptTailer", _FakeTailer)
    return ClaudeTtyPlugin()


def _lookup(
    plugin: ClaudeTtyPlugin, monkeypatch: pytest.MonkeyPatch, side_effect: Any
) -> list[tuple[Any, ...]]:
    source = MagicMock()
    source.find_clear_successor.side_effect = side_effect
    built: list[tuple[Any, ...]] = []

    def byte_source(*args: Any) -> MagicMock:
        built.append(args)
        return source

    monkeypatch.setattr(plugin, "_byte_source", byte_source)
    return built


def _register_old_tailer(
    plugin: ClaudeTtyPlugin,
) -> tuple[asyncio.Task[None], MagicMock]:
    tailer = MagicMock(spec=TranscriptTailer)
    task = asyncio.create_task(asyncio.sleep(3600))
    plugin._tailers["s1"] = plugin_mod._TailerRun(tailer, task)
    return task, tailer


async def test_follow_clear_rebinds_after_the_old_tailer_drains(
    plugin: ClaudeTtyPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _lookup(plugin, monkeypatch, [None, CHILD])
    runtime = _runtime(_session())
    old_task, old_tailer = _register_old_tailer(plugin)

    assert await plugin.follow_clear(runtime, "s1") is True

    old_tailer.stop.assert_called_once_with()
    session = runtime.storage.session
    assert session.transport_state == {"thread_id": CHILD, "tmux_pane": "%0"}
    assert session.context_usage is None
    # The old conversation's questions cannot be answered in the new one.
    runtime.questions.close_open.assert_awaited_once()
    assert [args[2] for args in built] == [PARENT, CHILD]
    new = plugin._tailers["s1"]
    assert isinstance(new.tailer, _FakeTailer)
    assert new.tailer.kwargs["start_at_end"] is False
    await asyncio.sleep(0)
    assert not new.tailer.ran
    old_task.cancel()
    await new.task
    assert new.tailer.ran


async def test_follow_clear_deadline_bounds_a_hung_lookup(
    plugin: ClaudeTtyPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lookup(plugin, monkeypatch, lambda *args: time.sleep(0.3))
    monkeypatch.setattr(plugin_mod, "_CLEAR_LOOKUP_DEADLINE_SECONDS", 0.05)
    runtime = _runtime(_session())
    old_task, _ = _register_old_tailer(plugin)
    started = time.monotonic()

    assert await plugin.follow_clear(runtime, "s1") is False

    assert time.monotonic() - started < 0.25
    assert plugin._tailers["s1"].task is old_task
    runtime.update_session_fields.assert_not_called()
    old_task.cancel()


@pytest.mark.parametrize(
    "relaunch",
    [
        {"thread_id": OTHER},
        # A settings-change relaunch resumes the same thread in a new pane.
        {"tmux_pane": "%9"},
        # A relaunch or terminate in flight has popped the tailer.
        None,
    ],
    ids=["new-thread", "same-thread-new-pane", "tailer-popped"],
)
async def test_follow_clear_yields_to_a_concurrent_relaunch(
    plugin: ClaudeTtyPlugin,
    monkeypatch: pytest.MonkeyPatch,
    relaunch: dict[str, str] | None,
) -> None:
    runtime = _runtime(_session())
    old_task, old_tailer = _register_old_tailer(plugin)

    def relaunch_during_lookup(*args: Any) -> str:
        if relaunch is None:
            del plugin._tailers["s1"]
        else:
            runtime.storage.set_state(**relaunch)
        return CHILD

    _lookup(plugin, monkeypatch, relaunch_during_lookup)

    assert await plugin.follow_clear(runtime, "s1") is False

    old_tailer.stop.assert_not_called()
    runtime.update_session_fields.assert_not_called()
    old_task.cancel()


async def test_stopped_tailer_drains_once_and_keeps_dialog_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = MagicMock()
    runtime.storage.get_session.return_value = _session()
    source = MagicMock()
    source.read_from.return_value = TranscriptRead(observed=False)
    tailer = TranscriptTailer("s1", source, runtime, MagicMock())
    close = AsyncMock()
    monkeypatch.setattr(tailer, "_close_dialog_state", close)
    tailer.stop()

    await tailer.run()

    source.read_from.assert_called_once_with(0, metadata_only=False, force=True)
    close.assert_not_called()


# ── transport interception ───────────────────────────────────────────────────


def _transport(
    monkeypatch: pytest.MonkeyPatch, followed: bool
) -> tuple[ClaudeTtyTransport, MagicMock, AsyncMock]:
    monkeypatch.setattr(TmuxTransport, "send_input", AsyncMock())
    runtime = MagicMock()
    runtime._record_system_event = AsyncMock()
    plugin = MagicMock()
    plugin.follow_clear = AsyncMock(return_value=followed)
    return ClaudeTtyTransport(runtime, plugin), runtime, plugin.follow_clear


@pytest.mark.parametrize("text", ["hello", "/clearer", "say /clear", "/compact"])
async def test_send_input_ignores_other_text(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    transport, runtime, follow = _transport(monkeypatch, followed=True)
    await transport.send_input(_session(), text)
    follow.assert_not_called()
    runtime._record_system_event.assert_not_called()


@pytest.mark.parametrize("text", ["/clear", "  /clear  ", "/clear now"])
async def test_send_input_follows_clear(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    transport, runtime, follow = _transport(monkeypatch, followed=True)
    await transport.send_input(_session(), text)
    follow.assert_awaited_once_with(runtime, "s1")
    runtime._record_system_event.assert_not_called()


@pytest.mark.parametrize(
    ("prior", "expected"),
    [
        (SessionStatus.IDLE, SessionStatus.IDLE),
        (SessionStatus.RUNNING, None),
        (SessionStatus.WAITING_INPUT, None),
    ],
)
async def test_unfollowed_clear_notes_without_ending_a_live_turn(
    monkeypatch: pytest.MonkeyPatch,
    prior: SessionStatus,
    expected: SessionStatus | None,
) -> None:
    transport, runtime, _ = _transport(monkeypatch, followed=False)
    await transport.send_input(_session(status=prior), "/clear")
    [call] = runtime._record_system_event.await_args_list
    assert call.args[0] == "s1"
    assert "/clear" in call.args[1]
    assert call.kwargs["status"] is expected
