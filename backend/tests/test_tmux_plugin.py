"""Unit tests for TmuxPlugin's terminate/resume helpers.

The end-to-end lifecycle is covered by the integration paths in
``test_runtime.py``; these tests pin down the pure-function pieces that
decide how reconnect rebuilds an inner CLI's command line and how the
Codex rollout-file watcher extracts a thread id from a filename.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus


@pytest.fixture
def plugin() -> TmuxPlugin:
    return TmuxPlugin()


def test_resume_args_claude_swaps_session_id_for_resume(plugin: TmuxPlugin) -> None:
    # ``create_session`` prepends ``--session-id <uuid>`` for claude
    # launches. On reconnect we want ``--resume <uuid>`` instead — the
    # two flags are mutually exclusive — with the rest of the user's
    # original args preserved in order.
    args = plugin._resume_args(
        "claude_code",
        "abc-123",
        ["--session-id", "abc-123", "--model", "sonnet", "extra"],
    )
    assert args == ["--resume", "abc-123", "--model", "sonnet", "extra"]


def test_resume_args_claude_without_thread_id_is_verbatim(plugin: TmuxPlugin) -> None:
    # No thread captured → fall through to a fresh launch with the
    # stored args. (Shouldn't happen for claude in practice since
    # create_session always generates one, but the contract is robust.)
    args = plugin._resume_args("claude_code", None, ["--model", "sonnet"])
    assert args == ["--model", "sonnet"]


def test_resume_args_codex_prepends_resume_subcommand(plugin: TmuxPlugin) -> None:
    # Codex's interactive CLI exposes resume only via the ``resume``
    # subcommand; arguments after the id are preserved.
    args = plugin._resume_args("codex", "abc-123", ["--model", "o3"])
    assert args == ["resume", "abc-123", "--model", "o3"]


def test_resume_args_codex_without_thread_id_is_verbatim(plugin: TmuxPlugin) -> None:
    # The watcher only captures an id once Codex has written its
    # rollout file. If the user disconnected before sending any input,
    # there's no conversation to resume — replay the launch verbatim.
    args = plugin._resume_args("codex", None, ["--model", "o3"])
    assert args == ["--model", "o3"]


def test_resume_args_unknown_backend_is_verbatim(plugin: TmuxPlugin) -> None:
    args = plugin._resume_args("opencode", "abc-123", ["--foo"])
    assert args == ["--foo"]


@pytest.mark.asyncio
async def test_conversation_exists_claude_code_local(
    plugin: TmuxPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Claude stores conversations at
    # ~/.claude/projects/<cwd-with-/-replaced-by-->/<uuid>.jsonl.
    # Pivot HOME so the lookup hits our fixture tree.
    monkeypatch.setenv("HOME", str(tmp_path))
    cwd = "/Users/me/proj"
    project_dir = tmp_path / ".claude" / "projects" / cwd.replace("/", "-")
    project_dir.mkdir(parents=True)
    uuid_str = "00000000-0000-0000-0000-000000000001"
    (project_dir / f"{uuid_str}.jsonl").write_text("")
    assert await plugin._conversation_exists("claude_code", uuid_str, cwd, None) is True
    # Missing uuid (e.g., user terminated before first message) →
    # False, so restore falls back to a verbatim launch.
    assert (
        await plugin._conversation_exists(
            "claude_code", "ffffffff-ffff-ffff-ffff-ffffffffffff", cwd, None
        )
        is False
    )


@pytest.mark.asyncio
async def test_conversation_exists_codex_local(
    plugin: TmuxPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    sessions_dir = tmp_path / "codex" / "sessions" / "2026" / "05" / "16"
    sessions_dir.mkdir(parents=True)
    uuid_str = "00000000-0000-0000-0000-000000000042"
    (sessions_dir / f"rollout-2026-05-16T10-00-00-{uuid_str}.jsonl").write_text("")
    assert (
        await plugin._conversation_exists("codex", uuid_str, "/anywhere", None) is True
    )
    assert (
        await plugin._conversation_exists(
            "codex", "ffffffff-ffff-ffff-ffff-ffffffffffff", "/anywhere", None
        )
        is False
    )


@pytest.mark.asyncio
async def test_conversation_exists_routes_through_ssh_when_launch_target_set(
    plugin: TmuxPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Verify the launch_target branch by stubbing the SSH helpers; we
    # don't actually want to spawn ssh in a unit test. Claude → _ssh_test;
    # codex → _ssh_capture.
    calls: list[tuple[str, str]] = []

    async def fake_ssh_test(target: object, remote_path: str) -> bool:
        calls.append(("test", remote_path))
        return remote_path.endswith("00000000-0000-0000-0000-000000000001.jsonl")

    async def fake_ssh_capture(target: object, remote_cmd: str) -> str:
        calls.append(("capture", remote_cmd))
        if "found" in remote_cmd:
            return "/remote/.codex/sessions/2026/05/16/rollout-2026-05-16T10-00-00-found.jsonl\n"
        return ""

    monkeypatch.setattr(TmuxPlugin, "_ssh_test", staticmethod(fake_ssh_test))
    monkeypatch.setattr(TmuxPlugin, "_ssh_capture", staticmethod(fake_ssh_capture))

    # ``_ssh_test`` is stubbed, so the actual SshLaunchTargetConfig
    # is never touched — passing a sentinel via cast keeps mypy happy
    # without forcing us to spin up a real launch-target fixture.
    target = cast(SshLaunchTargetConfig, object())
    cwd = "/remote/proj"
    assert (
        await plugin._conversation_exists(
            "claude_code",
            "00000000-0000-0000-0000-000000000001",
            cwd,
            target,
        )
        is True
    )
    assert (
        await plugin._conversation_exists(
            "claude_code",
            "ffffffff-ffff-ffff-ffff-ffffffffffff",
            cwd,
            target,
        )
        is False
    )
    # The SSH path was exercised for both calls.
    assert [kind for kind, _ in calls] == ["test", "test"]


def test_codex_rollout_matches_cwd_top_level(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    # SessionMeta with cwd at the top level of the JSONL header.
    log = tmp_path / "rollout-2026-05-16T12-00-00-abc.jsonl"
    log.write_text(
        json.dumps({"id": "abc", "cwd": "/Users/me/proj", "timestamp": "..."}) + "\n"
    )
    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/proj") is True
    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/other") is False


def test_codex_rollout_matches_cwd_nested_payload(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    # Newer rollout schemas wrap SessionMeta under "payload".
    log = tmp_path / "rollout-2026-05-16T12-00-00-abc.jsonl"
    log.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "abc", "cwd": "/Users/me/proj"},
            }
        )
        + "\n"
    )
    assert plugin._codex_rollout_matches_cwd(log, "/Users/me/proj") is True


def test_codex_rollout_matches_cwd_missing_or_bad_json(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    log = tmp_path / "garbage.jsonl"
    log.write_text("not json\n")
    assert plugin._codex_rollout_matches_cwd(log, "/anywhere") is False
    missing = tmp_path / "nope.jsonl"
    assert plugin._codex_rollout_matches_cwd(missing, "/anywhere") is False


@pytest.mark.asyncio
async def test_capture_codex_thread_id_pulls_uuid_from_filename(
    plugin: TmuxPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drop a rollout file in the mocked CODEX_HOME and confirm the
    watcher captures the UUID into transport_state via the runtime's
    storage."""
    # Stub a minimal runtime with just the storage surface the watcher
    # touches. The real SessionRuntime is too heavy for a pure unit
    # test.
    captured: dict[str, object] = {}

    class _Session:
        transport_state: dict[str, object] = {}

    class _Storage:
        def get_session(self, sid: str) -> _Session:
            return _Session()

        def update_session(self, sid: str, **fields: object) -> None:
            captured[sid] = fields

    class _Runtime:
        storage = _Storage()
        _tmux_thread_id_watchers: dict[str, object] = {}

    cwd = "/Users/me/proj"
    codex_home = tmp_path / "codex"
    sessions_dir = codex_home / "sessions" / "2026" / "05" / "16"
    sessions_dir.mkdir(parents=True)
    uuid_str = "01234567-89ab-cdef-0123-456789abcdef"
    rollout = sessions_dir / f"rollout-2026-05-16T12-00-00-{uuid_str}.jsonl"
    rollout.write_text(json.dumps({"id": uuid_str, "cwd": cwd}) + "\n")

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    # Tight loop so the test doesn't pay the 2 s default poll interval.
    monkeypatch.setattr(
        "waypoint.backends.tmux.plugin.asyncio.sleep",
        _fake_sleep,
    )
    await plugin._capture_codex_thread_id(
        _Runtime(),  # type: ignore[arg-type]
        "sess-1",
        cwd,
        datetime.now(UTC),
        None,
    )
    assert captured["sess-1"] == {
        "transport_state": {"thread_id": uuid_str},
    }


@pytest.mark.asyncio
async def test_conversation_exists_ssh_claude_command_leaves_home_unquoted(
    plugin: TmuxPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: ``shlex.quote`` over the whole remote path single-quoted
    # ``$HOME`` and made the remote shell look for a literal ``$HOME``
    # directory. The command we send must keep ``$HOME`` outside any
    # single-quoted span so the remote shell expands it.
    captured: list[str] = []

    async def fake_ssh_test(target: object, remote_cmd: str) -> bool:
        captured.append(remote_cmd)
        return True

    monkeypatch.setattr(TmuxPlugin, "_ssh_test", staticmethod(fake_ssh_test))
    target = cast(SshLaunchTargetConfig, object())
    await plugin._conversation_exists(
        "claude_code",
        "00000000-0000-0000-0000-000000000001",
        "/Users/me/proj",
        target,
    )
    assert len(captured) == 1
    cmd = captured[0]
    assert cmd.startswith("test -f ")
    # ``$HOME`` must not be inside a single-quoted span, otherwise the
    # remote shell sees the four literal characters instead of the
    # home-dir path.
    assert "'$HOME" not in cmd
    assert "$HOME/" in cmd


@pytest.mark.asyncio
async def test_find_codex_thread_id_remote_parses_ssh_output(
    plugin: TmuxPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stub _ssh_capture to return what the remote shell would print
    # (filename + tab + first JSONL line, per rollout file).
    uuid_str = "abcdef01-2345-6789-abcd-ef0123456789"
    cwd = "/remote/proj"
    captured_lines = (
        "/remote/.codex/sessions/2099/01/01/"
        f"rollout-2099-01-01T00-00-00-{uuid_str}.jsonl"
        "\t"
        f'{{"id": "{uuid_str}", "cwd": "{cwd}"}}\n'
    )

    async def fake_ssh_capture(target: object, remote_cmd: str) -> str:
        return captured_lines

    monkeypatch.setattr(TmuxPlugin, "_ssh_capture", staticmethod(fake_ssh_capture))
    found = await plugin._find_codex_thread_id_remote(
        cwd,
        datetime(2026, 1, 1, tzinfo=UTC),
        cast(SshLaunchTargetConfig, object()),
    )
    assert found == uuid_str


@pytest.mark.asyncio
async def test_find_codex_thread_id_remote_filters_by_cwd(
    plugin: TmuxPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two files; only the second matches our cwd.
    uuid_a = "11111111-1111-1111-1111-111111111111"
    uuid_b = "22222222-2222-2222-2222-222222222222"
    payload = (
        f"/r/.codex/sessions/2099/01/01/rollout-2099-01-01T00-00-00-{uuid_a}.jsonl"
        "\t"
        f'{{"id": "{uuid_a}", "cwd": "/other/proj"}}\n'
        f"/r/.codex/sessions/2099/01/01/rollout-2099-01-01T00-00-01-{uuid_b}.jsonl"
        "\t"
        f'{{"id": "{uuid_b}", "cwd": "/remote/proj"}}\n'
    )

    async def fake_ssh_capture(target: object, remote_cmd: str) -> str:
        return payload

    monkeypatch.setattr(TmuxPlugin, "_ssh_capture", staticmethod(fake_ssh_capture))
    found = await plugin._find_codex_thread_id_remote(
        "/remote/proj",
        datetime(2026, 1, 1, tzinfo=UTC),
        cast(SshLaunchTargetConfig, object()),
    )
    assert found == uuid_b


async def _fake_sleep(_seconds: float) -> None:
    # Yield control once so the watcher's loop runs to next iteration
    # without burning wall-clock time.
    return None


@dataclass
class _PaneTarget:
    session: str
    window: str
    pane: str
    pane_pid: int


class _FakeTmux:
    async def kill_session(self, name: str) -> None:
        return None

    async def start_managed_session(
        self, session_id: str, cwd: str, command: list[str]
    ) -> _PaneTarget:
        return _PaneTarget(
            session=f"{session_id}-tmux",
            window=f"{session_id}-w",
            pane=f"{session_id}-p",
            pane_pid=4242,
        )

    async def pipe_output(self, pane: str, log: Path) -> None:
        return None


class _FakeRuntime:
    """Minimal runtime stub for ``TmuxPlugin.restore_session``."""

    def __init__(self) -> None:
        self.tmux = _FakeTmux()
        self.file_offsets: dict[str, int] = {}
        self._tmux_thread_id_watchers: dict[str, Any] = {}
        self.updates: list[dict[str, Any]] = []

    def _find_launch_target(self, _lt_id: str | None) -> None:
        return None

    def _command_for_backend(
        self, backend: str, args: list[str], _lt: Any, _cwd: str
    ) -> list[str]:
        return ["claude", *args]

    def _ensure_monitor(self, _sid: str) -> None:
        return None

    async def _record_system_event(self, *args: Any, **kwargs: Any) -> None:
        return None

    class _Storage:
        def __init__(self, parent: "_FakeRuntime") -> None:
            self._parent = parent

        def update_session(self, sid: str, **fields: Any) -> None:
            self._parent.updates.append({"sid": sid, **fields})

    @property
    def storage(self) -> "_FakeRuntime._Storage":
        return _FakeRuntime._Storage(self)


def _exited_claude_session(sid: str, uuid_str: str, tmp_path: Path) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=sid,
        backend="claude_code",
        source=SessionSource.MANAGED,
        transport="tmux",
        title="t",
        cwd="/Users/me/proj",
        status=SessionStatus.EXITED,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        transport_state={
            "tmux_session": f"{sid}-old",
            "thread_id": uuid_str,
            "launch_args": ["--session-id", uuid_str],
        },
        raw_log_path=str(tmp_path / f"{sid}.raw.log"),
        structured_log_path=str(tmp_path / f"{sid}.events.jsonl"),
    )


@pytest.mark.asyncio
async def test_restore_session_claude_keeps_thread_id_across_terminate_cycles(
    plugin: TmuxPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two-cycle reconnect for claude_code.

    Cycle 1: user terminated before sending any message, so the
    conversation file doesn't exist yet — restore falls back to a
    verbatim ``--session-id <uuid>`` launch *but must preserve
    transport_state.thread_id* so cycle 2 (after the user has typed
    and the file now exists) can route through ``_conversation_exists``
    and produce ``--resume <uuid>``.

    Regression for the prior naive "drop thread_id on missing file"
    fix, which broke cycle 2 by short-circuiting the existence check.
    """
    uuid_str = "00000000-0000-0000-0000-000000000001"
    runtime = _FakeRuntime()

    # Cycle 1 — conversation file absent.
    async def absent(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(TmuxPlugin, "_conversation_exists", absent)
    session_1 = _exited_claude_session("sess-1", uuid_str, tmp_path)
    await plugin.restore_session(cast(Any, runtime), session_1)

    cycle1 = runtime.updates[-1]
    state1 = cycle1["transport_state"]
    # Verbatim launch (--session-id still in stored args).
    assert state1["launch_args"] == ["--session-id", uuid_str]
    # CRITICAL: thread_id is preserved even though no resume happened.
    assert state1["thread_id"] == uuid_str

    # Cycle 2 — user has typed; conversation file now exists.
    async def present(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(TmuxPlugin, "_conversation_exists", present)
    session_2 = _exited_claude_session("sess-1", uuid_str, tmp_path)
    await plugin.restore_session(cast(Any, runtime), session_2)

    cycle2 = runtime.updates[-1]
    state2 = cycle2["transport_state"]
    # Now we route through --resume, not --session-id.
    assert state2["launch_args"] == ["--resume", uuid_str]
    assert state2["thread_id"] == uuid_str


@pytest.mark.asyncio
async def test_restore_session_codex_drops_phantom_thread_id(
    plugin: TmuxPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phantom-id drop is still in force for codex.

    Codex's uuid is captured by a post-launch watcher, not pinned in
    stored_args. If the rollout file is gone, carrying the phantom id
    forward would suppress the watcher-spawn guard and the session
    could never re-acquire a real one.
    """
    uuid_str = "11111111-1111-1111-1111-111111111111"

    async def absent(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(TmuxPlugin, "_conversation_exists", absent)
    monkeypatch.setattr(
        TmuxPlugin,
        "_spawn_codex_thread_id_watcher",
        lambda *_args, **_kwargs: None,
    )

    runtime = _FakeRuntime()
    now = datetime.now(UTC)
    session = SessionRecord(
        id="sess-codex",
        backend="codex",
        source=SessionSource.MANAGED,
        transport="tmux",
        title="t",
        cwd="/Users/me/proj",
        status=SessionStatus.EXITED,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        transport_state={
            "tmux_session": "sess-codex-old",
            "thread_id": uuid_str,
            "launch_args": ["--foo"],
        },
        raw_log_path=str(tmp_path / "sess-codex.raw.log"),
        structured_log_path=str(tmp_path / "sess-codex.events.jsonl"),
    )
    await plugin.restore_session(cast(Any, runtime), session)

    state = runtime.updates[-1]["transport_state"]
    assert "thread_id" not in state, state
