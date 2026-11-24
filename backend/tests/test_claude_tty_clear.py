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
from waypoint.backends.claude_tty import plugin as plugin_mod
from waypoint.backends.claude_tty.byte_source import (
    LocalTranscriptByteSource,
    RemoteClaudeTranscriptByteSource,
)
from waypoint.backends.claude_tty.normalize import TranscriptNormalizer
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
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
        cwd="/repo/app",
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


def _write(path: Path, records: list[dict[str, Any]], age: float = 0.0) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


# ── header scan (shared by the local source and the remote op) ───────────────


def test_find_derived_matches_clear_successor(tmp_path: Path) -> None:
    _write(tmp_path / f"{PARENT}.jsonl", [{"type": "user", "message": {}}])
    _write(tmp_path / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    assert script.find_derived_transcripts(
        str(tmp_path), "*.jsonl", PARENT, "clear", 60
    ) == [CHILD]


@pytest.mark.parametrize(
    ("records", "age"),
    [
        (_header(CHILD, PARENT, via="fork"), 0.0),
        (_header(CHILD, OTHER), 0.0),
        (_header(CHILD, PARENT), 600.0),
        ([{"type": "user", "message": {}}, *_header(CHILD, PARENT)], 0.0),
    ],
    ids=["other-via", "other-parent", "stale", "after-conversation-record"],
)
def test_find_derived_ignores_non_matches(
    tmp_path: Path, records: list[dict[str, Any]], age: float
) -> None:
    _write(tmp_path / f"{CHILD}.jsonl", records, age=age)
    assert (
        script.find_derived_transcripts(str(tmp_path), "*.jsonl", PARENT, "clear", 60)
        == []
    )


def test_find_derived_tolerates_garbage_and_missing_root(tmp_path: Path) -> None:
    (tmp_path / f"{CHILD}.jsonl").write_bytes(b"\x00not json\n[1, 2]\n")
    assert (
        script.find_derived_transcripts(str(tmp_path), "*.jsonl", PARENT, "clear", 60)
        == []
    )
    assert (
        script.find_derived_transcripts(
            str(tmp_path / "missing"), "*.jsonl", PARENT, "clear", 60
        )
        == []
    )


def test_find_derived_op_emits_ids_and_validates_age(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "projects" / "-repo-app"
    project.mkdir(parents=True)
    _write(project / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    script.op_find_derived(str(tmp_path), "projects/*/*.jsonl", PARENT, "clear", "60")
    script.op_find_derived(str(tmp_path), "projects/*/*.jsonl", PARENT, "clear", "x")
    first, second = capsys.readouterr().out.splitlines()
    assert json.loads(first.removeprefix(script.SENTINEL)) == {"ids": [CHILD]}
    assert "error" in json.loads(second.removeprefix(script.SENTINEL))


def test_remote_fs_find_derived_passes_args_and_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fs = RemoteTranscriptFilesystem(
        SshLaunchTargetConfig(id="r", name="R", ssh_destination="h", ssh_bin="/bin/sh")
    )
    calls: list[tuple[str, ...]] = []
    replies: list[dict[str, Any]] = [{"ids": [CHILD, 7]}, {"error": "boom"}]

    def fake_run(op: str, *args: str) -> dict[str, Any]:
        calls.append((op, *args))
        return replies.pop(0)

    monkeypatch.setattr(fs, "_run", fake_run)
    assert fs.find_derived("~/.claude", "projects/*/*.jsonl", PARENT, "clear", 60) == [
        CHILD
    ]
    assert fs.find_derived("~/.claude", "projects/*/*.jsonl", PARENT, "clear", 60) == []
    assert calls[0] == (
        "find_derived",
        "~/.claude",
        "projects/*/*.jsonl",
        PARENT,
        "clear",
        "60",
    )


# ── byte sources ─────────────────────────────────────────────────────────────


def _local_source(path: Path) -> LocalTranscriptByteSource:
    src = LocalTranscriptByteSource.__new__(LocalTranscriptByteSource)
    src._path = path
    return src


def test_local_successor_found_without_parent_file(tmp_path: Path) -> None:
    # A /clear before the first turn links to a parent that was never written.
    _write(tmp_path / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    src = _local_source(tmp_path / f"{PARENT}.jsonl")
    assert src.find_clear_successor(PARENT, 60) == CHILD


def test_local_successor_ambiguous_or_missing(tmp_path: Path) -> None:
    src = _local_source(tmp_path / "missing-dir" / f"{PARENT}.jsonl")
    assert src.find_clear_successor(PARENT, 60) is None
    _write(tmp_path / f"{CHILD}.jsonl", _header(CHILD, PARENT))
    _write(tmp_path / f"{OTHER}.jsonl", _header(OTHER, PARENT))
    assert (
        _local_source(tmp_path / f"{PARENT}.jsonl").find_clear_successor(PARENT, 60)
        is None
    )


def test_remote_successor_searches_config_root() -> None:
    runtime = MagicMock()
    runtime.storage.get_session.return_value = _session()
    src = RemoteClaudeTranscriptByteSource(
        runtime,
        "s1",
        MagicMock(),
        SshLaunchTargetConfig(id="r", name="R", ssh_destination="h", ssh_bin="/bin/sh"),
        "/home/u/.claude-work",
    )
    fs = MagicMock()
    fs.find_derived.return_value = [CHILD]
    src._fs = fs
    assert src.find_clear_successor(PARENT, 60) == CHILD
    fs.find_derived.assert_called_once_with(
        "/home/u/.claude-work", "projects/*/*.jsonl", PARENT, "clear", 60
    )
    fs.find_derived.return_value = [CHILD, OTHER]
    assert src.find_clear_successor(PARENT, 60) is None


# ── normalizer note ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "expected"),
    [("clear", "Context cleared"), ("status", "Command complete")],
)
def test_local_command_note(command: str, expected: str) -> None:
    events = TranscriptNormalizer(None).process_record(
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


def _runtime(session: SessionRecord) -> MagicMock:
    runtime = MagicMock()
    store = _Store(session)
    runtime.storage = store
    runtime._find_launch_target.return_value = None

    async def update_session_fields(session_id: str, **updates: Any) -> SessionRecord:
        store.session = store.session.model_copy(update=updates)
        return store.session

    runtime.update_session_fields = AsyncMock(side_effect=update_session_fields)
    return runtime


def _plugin_with_source(
    monkeypatch: pytest.MonkeyPatch, answers: list[str | None]
) -> tuple[ClaudeTtyPlugin, MagicMock]:
    plugin = ClaudeTtyPlugin()
    source = MagicMock()
    source.find_clear_successor.side_effect = answers
    monkeypatch.setattr(plugin, "_byte_source", lambda *args: source)
    monkeypatch.setattr(plugin_mod, "_CLEAR_LOOKUP_INTERVAL_SECONDS", 0.0)
    return plugin, source


async def test_follow_clear_rebinds_thread_and_restarts_tailer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, source = _plugin_with_source(monkeypatch, [None, CHILD])
    runtime = _runtime(_session())
    old_tailer = asyncio.create_task(asyncio.sleep(3600))
    plugin._tailer_tasks["s1"] = old_tailer
    started: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        plugin, "_start_tailer", lambda *args, **kwargs: started.append((args, kwargs))
    )

    assert await plugin.follow_clear(runtime, "s1") is True

    assert old_tailer.cancelled()
    assert "s1" not in plugin._tailer_tasks
    session = runtime.storage.session
    assert session.transport_state == {"thread_id": CHILD, "tmux_pane": "%0"}
    assert session.context_usage is None
    [(args, kwargs)] = started
    assert args[1:3] == ("s1", CHILD)
    assert kwargs.get("start_at_end", False) is False
    assert source.find_clear_successor.call_count == 2


async def test_follow_clear_gives_up_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, source = _plugin_with_source(monkeypatch, [None] * 1000)
    monkeypatch.setattr(plugin_mod, "_CLEAR_LOOKUP_DEADLINE_SECONDS", 0.0)
    runtime = _runtime(_session())
    tailer = MagicMock()
    plugin._tailer_tasks["s1"] = tailer

    assert await plugin.follow_clear(runtime, "s1") is False
    assert plugin._tailer_tasks["s1"] is tailer
    runtime.update_session_fields.assert_not_called()


async def test_follow_clear_yields_to_a_concurrent_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin, _ = _plugin_with_source(monkeypatch, [CHILD])
    runtime = _runtime(_session())
    tailer = MagicMock()
    plugin._tailer_tasks["s1"] = tailer
    original = plugin._rebind_thread

    async def relaunch_first(*args: Any) -> bool:
        runtime.storage.session = _session(thread_id=OTHER)
        return await original(*args)

    monkeypatch.setattr(plugin, "_rebind_thread", relaunch_first)

    assert await plugin.follow_clear(runtime, "s1") is False
    assert plugin._tailer_tasks["s1"] is tailer
    runtime.update_session_fields.assert_not_called()


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
