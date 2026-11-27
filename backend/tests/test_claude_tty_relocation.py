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
from fastapi import HTTPException

from waypoint.backends.claude_code import session_folder
from waypoint.backends.claude_code.session_folder import (
    follow_relocation,
    relaunch_folder,
)
from waypoint.backends.claude_tty import tailer as tailer_mod
from waypoint.backends.claude_tty.byte_source import (
    READ_LIMIT,
    LocalTranscriptByteSource,
    RemoteClaudeTranscriptByteSource,
    transcript_path,
)
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.transcript_fs_remote import (
    RemoteRead,
    RemoteTranscriptFilesystem,
)
from waypoint.runtime import SessionRuntime
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
    runtime._require_launch_folder = SessionRuntime._require_launch_folder
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
    return TranscriptTailer(
        session_id="s1",
        source=LocalTranscriptByteSource("/repo/app", TID, str(config_dir)),
        runtime=runtime,
        plugin=MagicMock(),
        start_at_end=start_at_end,
    )


@pytest.fixture
def followed(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """The tailer's folder follow."""
    follow = AsyncMock()
    monkeypatch.setattr(tailer_mod, "follow_relocation", follow)
    return follow


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
    tmp_path: Path, followed: AsyncMock
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

    followed.assert_awaited_once_with(runtime, "s1", "/repo/app/.claude/worktrees/w")
    assert _outputs(runtime) == []


async def test_remote_tailer_reads_the_tail_for_an_earlier_move(
    followed: AsyncMock,
) -> None:
    relocated = {"type": "relocated", "relocatedCwd": "/srv/w"}
    content = b"x" * READ_LIMIT + b"\n" + json.dumps(relocated).encode() + b"\n"
    offsets: list[int] = []

    def read_range(path: str, offset: int, limit: int) -> RemoteRead:
        offsets.append(offset)
        data = content[offset : offset + limit]
        return RemoteRead(data=data, size=len(content), device=1, inode=2)

    runtime = _runtime(_session("/srv/app", launch_target_id="remote-1"))
    source = RemoteClaudeTranscriptByteSource(
        runtime, "s1", TID, MagicMock(id="remote-1"), None
    )
    source._fs = MagicMock(read_range=read_range)
    source._fs.glob.return_value = [f"/p/{TID}.jsonl"]
    tailer = TranscriptTailer(
        session_id="s1",
        source=source,
        runtime=runtime,
        plugin=MagicMock(),
        start_at_end=True,
    )

    await tailer._drain()

    # The priming read, then the tail, inside the source's poll interval.
    assert offsets == [0, len(content) - READ_LIMIT]
    followed.assert_awaited_once_with(runtime, "s1", "/srv/w")


async def test_tailer_follows_the_last_relocation_after_the_records(
    tmp_path: Path, followed: AsyncMock
) -> None:
    runtime = _runtime(_session("/repo/app"))
    tailer = _tailer(runtime, tmp_path)
    emitted_at_follow: list[int] = []

    async def follow(*args: Any) -> None:
        emitted_at_follow.append(len(_outputs(runtime)))

    followed.side_effect = follow
    _append(
        transcript_path("/repo/app", TID, str(tmp_path)),
        {"type": "relocated", "relocatedCwd": "/repo/first"},
        _assistant("one"),
        {"type": "relocated", "relocatedCwd": "/repo/last"},
        _assistant("two"),
    )
    await tailer._drain()

    followed.assert_awaited_once_with(runtime, "s1", "/repo/last")
    assert emitted_at_follow == [2]


async def test_tailer_ignores_a_records_own_cwd(
    tmp_path: Path, followed: AsyncMock
) -> None:
    # The records' cwd follows a shell ``cd``, which moves nothing.
    runtime = _runtime(_session("/repo/app"))
    tailer = _tailer(runtime, tmp_path)
    _append(
        transcript_path("/repo/app", TID, str(tmp_path)),
        {**_assistant("one"), "cwd": "/repo/app/sub"},
    )
    await tailer._drain()
    assert _outputs(runtime) == ["one"]
    followed.assert_not_called()


# ── the session's recorded folder ─────────────────────────────────────────────


async def test_follow_moves_the_folder_and_back(repo: tuple[Path, Path]) -> None:
    root, worktree = repo
    runtime = _runtime(_session(str(root)))

    await follow_relocation(runtime, "s1", str(worktree))
    moved = runtime.storage.session
    assert moved.cwd == str(worktree)
    # One repository: its worktree is not a separate repo.
    assert (moved.repo_name, moved.branch) == ("proj", "worktree-w")
    assert moved.transport_state["launch_cwd"] == str(root)
    assert moved.transport_state["thread_id"] == TID

    await follow_relocation(runtime, "s1", str(root))
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

    await follow_relocation(runtime, "s1", str(other))

    session = runtime.storage.session
    assert (session.repo_name, session.branch) == ("other", None)


async def test_follow_names_a_worktree_after_its_repository(
    repo: tuple[Path, Path], tmp_path: Path
) -> None:
    _, worktree = repo
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    runtime = _runtime(_session(str(elsewhere), repo_name="other", branch=None))

    await follow_relocation(runtime, "s1", str(worktree))

    session = runtime.storage.session
    assert (session.repo_name, session.branch) == ("proj", "worktree-w")


async def test_follow_keeps_state_written_during_the_git_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "w"
    folder.mkdir()
    runtime = _runtime(_session(str(tmp_path)))
    resolve = session_folder.resolve_git_meta

    async def writes_meanwhile(cwd: str) -> Any:
        store = runtime.storage
        state = {**store.session.transport_state, "thread_id": NEW_TID}
        store.update_session("s1", transport_state=state)
        return await resolve(cwd)

    monkeypatch.setattr(session_folder, "resolve_git_meta", writes_meanwhile)

    await follow_relocation(runtime, "s1", str(folder))

    assert runtime.storage.session.transport_state["thread_id"] == NEW_TID


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

    await follow_relocation(runtime, "s1", str(folder))

    runtime.update_session_fields.assert_not_called()


async def test_follow_never_raises(tmp_path: Path) -> None:
    runtime = _runtime(_session("/repo/app"))
    runtime.update_session_fields.side_effect = KeyError("s1")
    await follow_relocation(runtime, "s1", str(tmp_path))
    runtime.update_session_fields.assert_awaited_once()


async def test_follow_on_ssh_runs_no_local_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(_session("~/proj", launch_target_id="remote-1"))
    runtime._find_launch_target.return_value = MagicMock()
    probed: list[str] = []

    def probe_dir(self: RemoteTranscriptFilesystem, path: str) -> bool:
        probed.append(path)
        return True

    monkeypatch.setattr(RemoteTranscriptFilesystem, "probe_dir", probe_dir)
    local_git = AsyncMock()
    monkeypatch.setattr(session_folder, "resolve_git_meta", local_git)

    await follow_relocation(runtime, "s1", "/home/u/proj/.claude/worktrees/w")

    assert probed == ["/home/u/proj/.claude/worktrees/w"]

    session = runtime.storage.session
    assert session.cwd == "/home/u/proj/.claude/worktrees/w"
    assert (session.repo_name, session.branch) == ("proj", None)
    assert session.transport_state["launch_cwd"] == "~/proj"
    local_git.assert_not_called()


# ── relaunching after a move ──────────────────────────────────────────────────


def _assert_removed_folder_note(
    runtime: MagicMock, session_id: str, launch: Path
) -> None:
    text = f"Folder {launch / 'removed'} no longer exists; starting in {launch}"
    note = next(
        call
        for call in runtime._record_system_event.call_args_list
        if call.args[1] == text
    )
    assert note.args[0] == session_id
    # The Summary view shows it.
    assert note.kwargs["metadata"] == {"visibility": "important"}


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
    assert runtime.storage.session.repo_name == tmp_path.name
    assert runtime.tmux.start_managed_session.call_args.args[1] == launch
    assert start_tailer.call_args.args[3] == launch
    assert runtime.storage.session.cwd == launch
    assert runtime.storage.session.transport_state["launch_cwd"] == launch
    _assert_removed_folder_note(runtime, "s1", tmp_path)


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


async def test_restart_keeps_the_pane_when_no_folder_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(_session(str(tmp_path / "gone")))
    plugin = ClaudeTtyPlugin()
    start_tailer = _stub(plugin, monkeypatch)

    with pytest.raises(HTTPException, match="working folder does not exist"):
        await plugin._restart_with_args(
            runtime, runtime.storage.session, model="sonnet"
        )

    runtime.tmux.kill_session.assert_not_awaited()
    start_tailer.assert_not_called()


async def test_restart_uses_the_stored_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = tmp_path / "w"
    worktree.mkdir()
    # A follow landed after the caller read the record.
    stale = _session(str(tmp_path), raw_log_path=str(tmp_path / "raw.log"))
    moved = stale.model_copy(
        update={
            "cwd": str(worktree),
            "transport_state": {**stale.transport_state, "launch_cwd": str(tmp_path)},
        }
    )
    runtime = _runtime(moved)
    plugin = ClaudeTtyPlugin()
    _stub(plugin, monkeypatch)

    await plugin._restart_with_args(runtime, stale, model="sonnet")

    assert runtime.tmux.start_managed_session.call_args.args[1] == str(worktree)
    state = runtime.storage.session.transport_state
    assert state["launch_cwd"] == str(tmp_path)


async def test_a_failed_ssh_probe_is_not_a_removed_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(
        "/srv/w",
        launch_target_id="remote-1",
        transport_state={"launch_cwd": "/srv/app"},
    )
    runtime = _runtime(session)
    runtime._find_launch_target.return_value = MagicMock()
    answers = {"/srv/w": None, "/srv/app": True}
    monkeypatch.setattr(
        RemoteTranscriptFilesystem, "probe_dir", lambda self, path: answers[path]
    )

    assert await relaunch_folder(runtime, session) == "/srv/w"
    answers["/srv/w"] = False
    assert await relaunch_folder(runtime, session) == "/srv/app"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"is_dir": True}, True),
        ({"is_dir": False}, False),
        ({"error": "transport failure"}, None),
    ],
)
def test_probe_dir_tells_unreachable_from_missing(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any], expected: bool | None
) -> None:
    fs = RemoteTranscriptFilesystem(MagicMock())
    monkeypatch.setattr(fs, "_run", lambda *args: result)
    assert fs.probe_dir("/srv/w") is expected


async def test_promoted_side_question_leaves_a_removed_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _removed_worktree_session(tmp_path, SessionStatus.IDLE)
    new_session = parent.model_copy(
        update={"id": "aside-1", "raw_log_path": str(tmp_path / "raw.log")}
    )
    runtime = _runtime(new_session)
    plugin = ClaudeTtyPlugin()
    start_tailer = _stub(plugin, monkeypatch)

    await plugin._launch_resumed_pane(runtime, parent, new_session, NEW_TID)

    assert runtime.tmux.start_managed_session.call_args.args[1] == str(tmp_path)
    assert start_tailer.call_args.args[3] == str(tmp_path)
    record = runtime.storage.session
    assert (record.cwd, record.repo_name, record.branch) == (
        str(tmp_path),
        tmp_path.name,
        None,
    )
    assert record.transport_state["launch_cwd"] == str(tmp_path)
    _assert_removed_folder_note(runtime, "aside-1", tmp_path)


def test_fork_copy_refuses_an_ambiguous_transcript(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    for folder in ("/one", "/two"):
        _append(transcript_path(folder, TID, str(config_dir)), _assistant(folder))
    session = _session("/elsewhere", launch_env={})

    assert not ClaudeTtyPlugin()._copy_transcript(
        session, TID, NEW_TID, str(config_dir), None
    )


async def test_fork_copies_a_moved_transcript_beside_it(tmp_path: Path) -> None:
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
    assert (created[0].repo_name, created[0].branch) == (tmp_path.name, None)
    assert created[0].transport_state["launch_cwd"] == str(tmp_path)
    assert runtime.tmux.start_managed_session.call_args.args[1] == str(tmp_path)
    assert start_tailer.call_args.args[3] == str(tmp_path)
    _assert_removed_folder_note(runtime, "fork-1", tmp_path)
    # The source keeps its record.
    assert runtime.storage.session.cwd == str(tmp_path / "removed")
