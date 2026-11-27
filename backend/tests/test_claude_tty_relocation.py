"""claude_tty when Claude moves a session's transcript (a worktree, ``/cd``).

Covers the tailer streaming across the move, the session's recorded folder
following it, and the relaunch paths afterwards.
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.backends.claude_tty import plugin as plugin_mod
from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    transcript_path,
)
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.schemas import EventKind, SessionRecord, SessionSource, SessionStatus

TID = "11111111-1111-1111-1111-111111111111"
NEW_TID = "22222222-2222-2222-2222-222222222222"


def _session(cwd: str, **updates: Any) -> SessionRecord:
    now = datetime.now(UTC)
    record = SessionRecord(
        id="s1",
        backend="claude_code",
        source=SessionSource.MANAGED,
        transport="claude_tty",
        title="t",
        cwd=cwd,
        repo_name="proj",
        branch="main",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        transport_state={"thread_id": TID, "tmux_session": "s1", "tmux_pane": "%0"},
    )
    return record.model_copy(update=updates)


class _Store:
    def __init__(self, session: SessionRecord) -> None:
        self.session = session

    def get_session(self, session_id: str) -> SessionRecord | None:
        return self.session

    def update_session(self, session_id: str, **fields: Any) -> SessionRecord:
        self.session = self.session.model_copy(update=fields)
        return self.session


def _runtime(session: SessionRecord) -> MagicMock:
    runtime = MagicMock()
    store = _Store(session)
    runtime.storage = store
    runtime._find_launch_target.return_value = None
    runtime._record_system_event = AsyncMock()
    runtime._emit_adapter_event = AsyncMock()
    runtime.publish_token_usage_record = AsyncMock()

    async def update_session_fields(session_id: str, **updates: Any) -> SessionRecord:
        return store.update_session(session_id, **updates)

    runtime.update_session_fields = AsyncMock(side_effect=update_session_fields)
    target = MagicMock(session="s-new", window="0", pane="%9", pane_pid=1)
    runtime.tmux.kill_session = AsyncMock()
    runtime.tmux.start_managed_session = AsyncMock(return_value=target)
    runtime.tmux.pipe_output = AsyncMock()
    runtime.tmux.resize_window = AsyncMock()
    runtime._command_for_backend.return_value = ["claude"]
    return runtime


def _git(cwd: Path, *args: str) -> None:
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(
        ["git", "-C", str(cwd), *identity, "-c", "commit.gpgsign=false", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repository and a Claude-style worktree inside it."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "commit", "-q", "--allow-empty", "-m", "init")
    worktree = root / ".claude" / "worktrees" / "w"
    _git(root, "worktree", "add", "-q", str(worktree), "-b", "worktree-w")
    return root, worktree


# ── tailing across a move ─────────────────────────────────────────────────────


def _assistant(message_id: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "model": "claude-haiku-4-5",
            "content": [{"type": "text", "text": message_id}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    }


def _append(path: Path, *records: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _tailer(
    runtime: MagicMock, config_dir: Path, *, start_at_end: bool = False
) -> TranscriptTailer:
    plugin = MagicMock()
    plugin.follow_relocation = AsyncMock()
    return TranscriptTailer(
        session_id="s1",
        source=LocalTranscriptByteSource("/repo/app", TID, str(config_dir)),
        runtime=runtime,
        plugin=plugin,
        start_at_end=start_at_end,
    )


def _outputs(runtime: MagicMock) -> list[str]:
    return [
        call.args[2]
        for call in runtime._emit_adapter_event.call_args_list
        if call.args[1] is EventKind.AGENT_OUTPUT
    ]


async def test_tailer_emits_each_record_once_across_moves(tmp_path: Path) -> None:
    runtime = _runtime(_session("/repo/app"))
    tailer = _tailer(runtime, tmp_path)
    home = transcript_path("/repo/app", TID, str(tmp_path))
    away = transcript_path("/repo/app/.claude/worktrees/w", TID, str(tmp_path))

    _append(home, _assistant("before"))
    await tailer._drain()
    away.parent.mkdir(parents=True)
    home.rename(away)
    # Part of a record, then the rest, in the new folder.
    line = json.dumps(_assistant("inside")) + "\n"
    with away.open("a") as fh:
        fh.write(line[:20])
    await tailer._drain()
    with away.open("a") as fh:
        fh.write(line[20:])
    await tailer._drain()
    assert _outputs(runtime) == ["before", "inside"]
    away.rename(home)
    _append(home, _assistant("after"))
    await tailer._drain()
    await tailer._drain()

    assert _outputs(runtime) == ["before", "inside", "after"]
    runtime._record_system_event.assert_not_called()


async def test_tailer_restarted_after_a_move_reads_only_new_records(
    tmp_path: Path,
) -> None:
    away = transcript_path("/repo/app/.claude/worktrees/w", TID, str(tmp_path))
    _append(away, _assistant("old"))
    runtime = _runtime(_session("/repo/app"))
    tailer = _tailer(runtime, tmp_path, start_at_end=True)

    await tailer._drain()
    _append(away, _assistant("new"))
    await tailer._drain()

    assert _outputs(runtime) == ["new"]


async def test_tailer_started_at_the_end_follows_an_earlier_move(
    tmp_path: Path,
) -> None:
    # A restart or reattach after a move the previous tailer never read.
    away = transcript_path("/repo/app/.claude/worktrees/w", TID, str(tmp_path))
    _append(
        away,
        {"type": "relocated", "relocatedCwd": "/repo/app"},
        _assistant("old"),
        {"type": "relocated", "relocatedCwd": "/repo/app/.claude/worktrees/w"},
        {**_assistant("later"), "cwd": "/repo/app/sub"},
    )
    runtime = _runtime(_session("/repo/app"))
    tailer = _tailer(runtime, tmp_path, start_at_end=True)

    await tailer._drain()

    tailer._plugin.follow_relocation.assert_awaited_once_with(  # type: ignore[attr-defined]
        runtime, "s1", "/repo/app/.claude/worktrees/w"
    )
    assert _outputs(runtime) == []


async def test_tailer_follows_the_last_relocation_after_the_records(
    tmp_path: Path,
) -> None:
    runtime = _runtime(_session("/repo/app"))
    tailer = _tailer(runtime, tmp_path)
    emitted_at_follow: list[int] = []

    async def follow(*args: Any) -> None:
        emitted_at_follow.append(len(_outputs(runtime)))

    follow_mock = AsyncMock(side_effect=follow)
    tailer._plugin.follow_relocation = follow_mock  # type: ignore[method-assign]
    _append(
        transcript_path("/repo/app", TID, str(tmp_path)),
        {"type": "relocated", "relocatedCwd": "/repo/first"},
        _assistant("one"),
        {"type": "relocated", "relocatedCwd": "/repo/last"},
        _assistant("two"),
    )
    await tailer._drain()

    follow_mock.assert_awaited_once_with(runtime, "s1", "/repo/last")
    assert emitted_at_follow == [2]


async def test_tailer_ignores_a_records_own_cwd(tmp_path: Path) -> None:
    # The records' cwd follows a shell ``cd``, which moves nothing.
    runtime = _runtime(_session("/repo/app"))
    tailer = _tailer(runtime, tmp_path)
    _append(
        transcript_path("/repo/app", TID, str(tmp_path)),
        {**_assistant("one"), "cwd": "/repo/app/sub"},
    )
    await tailer._drain()
    tailer._plugin.follow_relocation.assert_not_called()  # type: ignore[attr-defined]


# ── the session's recorded folder ─────────────────────────────────────────────


async def test_follow_moves_the_folder_and_back(repo: tuple[Path, Path]) -> None:
    root, worktree = repo
    runtime = _runtime(_session(str(root)))
    plugin = ClaudeTtyPlugin()

    await plugin.follow_relocation(runtime, "s1", str(worktree))
    moved = runtime.storage.session
    assert moved.cwd == str(worktree)
    # One repository: its worktree is not a separate repo.
    assert (moved.repo_name, moved.branch) == ("proj", "worktree-w")
    assert moved.transport_state["launch_cwd"] == str(root)
    assert moved.transport_state["thread_id"] == TID

    await plugin.follow_relocation(runtime, "s1", str(root))
    back = runtime.storage.session
    assert (back.cwd, back.repo_name, back.branch) == (str(root), "proj", "main")
    assert back.transport_state["launch_cwd"] == str(root)


async def test_follow_takes_the_name_of_another_folder(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = repo
    other = tmp_path / "other"
    other.mkdir()
    runtime = _runtime(_session(str(root)))

    await ClaudeTtyPlugin().follow_relocation(runtime, "s1", str(other))

    session = runtime.storage.session
    assert (session.repo_name, session.branch) == ("other", None)


@pytest.mark.parametrize("case", ["same", "missing", "assistant"])
async def test_follow_leaves_the_session_alone(tmp_path: Path, case: str) -> None:
    session = _session(str(tmp_path))
    folder = tmp_path / "w"
    folder.mkdir()
    if case == "same":
        folder = tmp_path
    elif case == "missing":
        folder = tmp_path / "gone"
    else:
        session.source = SessionSource.ASSISTANT
    runtime = _runtime(session)

    await ClaudeTtyPlugin().follow_relocation(runtime, "s1", str(folder))

    runtime.update_session_fields.assert_not_called()


async def test_follow_never_raises(tmp_path: Path) -> None:
    runtime = _runtime(_session("/repo/app"))
    runtime.update_session_fields.side_effect = KeyError("s1")
    await ClaudeTtyPlugin().follow_relocation(runtime, "s1", str(tmp_path))


async def test_follow_on_ssh_runs_no_local_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(_session("~/proj", launch_target_id="remote-1"))
    plugin = ClaudeTtyPlugin()
    monkeypatch.setattr(plugin, "_folder_exists", AsyncMock(return_value=True))
    local_git = AsyncMock()
    monkeypatch.setattr(plugin_mod, "resolve_git_meta", local_git)

    await plugin.follow_relocation(runtime, "s1", "/home/u/proj/.claude/worktrees/w")

    session = runtime.storage.session
    assert session.cwd == "/home/u/proj/.claude/worktrees/w"
    assert (session.repo_name, session.branch) == ("proj", None)
    assert session.transport_state["launch_cwd"] == "~/proj"
    local_git.assert_not_called()


# ── relaunching after a move ──────────────────────────────────────────────────


def _removed_worktree_session(launch: Path, status: SessionStatus) -> SessionRecord:
    return _session(
        str(launch / "removed"),
        status=status,
        branch="worktree-removed",
        transport_state={
            "thread_id": TID,
            "tmux_session": "s1",
            "launch_cwd": str(launch),
        },
    )


def _stub(plugin: ClaudeTtyPlugin, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    start_tailer = MagicMock()
    monkeypatch.setattr(plugin, "_start_tailer", start_tailer)
    monkeypatch.setattr(plugin, "_spawn_rate_limit_watcher", MagicMock())
    monkeypatch.setattr(plugin, "_conversation_exists", AsyncMock(return_value=True))
    return start_tailer


@pytest.mark.parametrize("path", ["reattach", "restart"])
async def test_relaunch_leaves_a_removed_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    status = SessionStatus.EXITED if path == "reattach" else SessionStatus.IDLE
    session = _removed_worktree_session(tmp_path, status)
    session.raw_log_path = str(tmp_path / "raw.log")
    runtime = _runtime(session)
    plugin = ClaudeTtyPlugin()
    start_tailer = _stub(plugin, monkeypatch)

    if path == "reattach":
        await plugin.restore_session(runtime, session)
    else:
        await plugin._restart_with_args(runtime, session, model="sonnet")

    launch = str(tmp_path)
    assert runtime._command_for_backend.call_args.args[3] == launch
    assert runtime.tmux.start_managed_session.call_args.args[1] == launch
    assert start_tailer.call_args.args[3] == launch
    assert runtime.storage.session.cwd == launch
    assert runtime.storage.session.transport_state["launch_cwd"] == launch
    note = runtime._record_system_event.call_args_list[0]
    assert f"Folder {tmp_path / 'removed'} no longer exists" in note.args[1]
    # The Summary view shows it.
    assert note.kwargs["metadata"] == {"visibility": "important"}


async def test_reattach_keeps_a_moved_folder_that_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "w"
    worktree.mkdir()
    # The caller's snapshot predates the move the stored record carries.
    stale = _session(str(tmp_path), status=SessionStatus.EXITED)
    stale.raw_log_path = str(tmp_path / "raw.log")
    moved = stale.model_copy(
        update={
            "cwd": str(worktree),
            "transport_state": {"thread_id": TID, "launch_cwd": str(tmp_path)},
        }
    )
    runtime = _runtime(moved)
    plugin = ClaudeTtyPlugin()
    _stub(plugin, monkeypatch)

    await plugin.restore_session(runtime, stale)

    assert runtime.tmux.start_managed_session.call_args.args[1] == str(worktree)
    state = runtime.storage.session.transport_state
    assert state["launch_cwd"] == str(tmp_path)


async def test_fork_copies_a_moved_transcript_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "cfg"
    moved = transcript_path("/elsewhere", TID, str(config_dir))
    _append(moved, _assistant("one"))
    session = _session(str(tmp_path), launch_env={})

    assert ClaudeTtyPlugin()._copy_transcript(
        session, TID, NEW_TID, str(config_dir), None
    )

    assert (moved.parent / f"{NEW_TID}.jsonl").read_text() == moved.read_text()


async def test_fork_of_a_removed_folder_records_where_it_launched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _removed_worktree_session(tmp_path, SessionStatus.IDLE)
    runtime = _runtime(source)
    created: list[SessionRecord] = []
    runtime.storage.create_session = created.append
    plugin = ClaudeTtyPlugin()
    start_tailer = _stub(plugin, monkeypatch)
    monkeypatch.setattr(
        plugin, "_materialize_forked_transcript", AsyncMock(return_value=False)
    )

    await plugin.fork_session(
        runtime,
        source,
        "fork-1",
        "forked",
        tmp_path / "raw.log",
        tmp_path / "events.jsonl",
    )

    assert created[0].cwd == str(tmp_path)
    assert created[0].transport_state["launch_cwd"] == str(tmp_path)
    assert runtime.tmux.start_managed_session.call_args.args[1] == str(tmp_path)
    assert start_tailer.call_args.args[3] == str(tmp_path)
    # The source keeps its record.
    assert source.cwd == str(tmp_path / "removed")


def test_clear_successor_is_found_in_another_project_folder(tmp_path: Path) -> None:
    successor = transcript_path("/elsewhere", NEW_TID, str(tmp_path))
    _append(
        successor,
        {"type": "derived-from", "sessions": [{"id": TID, "via": "clear"}]},
    )
    source = LocalTranscriptByteSource("/repo/app", TID, str(tmp_path))
    assert source.find_clear_successor(TID, 60) == NEW_TID
