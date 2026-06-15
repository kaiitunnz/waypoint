"""Tests for TmuxPlugin's reconnect/create lifecycle.

The end-to-end lifecycle is covered by the integration paths in
``test_runtime.py``; these tests pin down how the tmux wrapper drives an
agent through its launch contract — reconnect rebuilds the command line via
``resume_args``/``conversation_exists``, create pins or discovers the thread
id, and the rate-limit watcher refreshes wrapped sessions. The contract
methods themselves are unit-tested on the agent plugins in
``test_claude_launch_contract.py`` / ``test_codex_plugin.py``.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from waypoint.backends.tmux.plugin import TmuxPlugin
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus


@pytest.fixture
def plugin() -> TmuxPlugin:
    return TmuxPlugin()


class _FakeAgentPlugin:
    """Test double for an inner agent plugin's AgentLaunchContract.

    The unit-level behaviour of each contract method (launch_flags,
    resume_args, conversation_exists, capture_thread_id) is covered on the
    real agent plugins in ``test_claude_launch_contract.py`` /
    ``test_codex_plugin.py``. Here we only need a controllable stand-in so
    the tmux reconnect/create paths can be exercised: ``pregenerates``
    mimics claude (a pinned ``--session-id`` uuid) vs codex (id discovered
    post-launch), and ``exists`` flips ``conversation_exists`` per cycle.

    Deliberately omits ``probe_account_rate_limit`` so the rate-limit
    watcher spawn early-exits in restore/create tests.
    """

    _PREGEN_ID = "00000000-0000-0000-0000-0000000000ff"

    def __init__(self, *, pregenerates: bool, exists: bool = False) -> None:
        self._pregenerates = pregenerates
        self.exists = exists

    def launch_flags(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        permission_mode: str | None = None,
    ) -> list[str]:
        flags: list[str] = []
        if self._pregenerates:  # claude-style
            if model:
                flags += ["--model", model]
            if effort:
                flags += ["--effort", effort]
            if permission_mode:
                flags += ["--permission-mode", permission_mode]
        else:  # codex-style
            if model:
                flags += ["-m", model]
            if permission_mode == "default":
                flags += ["-a", "on-request", "-s", "workspace-write"]
            elif permission_mode == "full_access":
                flags += ["--dangerously-bypass-approvals-and-sandbox"]
        return flags

    def pregenerate_thread_id(self) -> str | None:
        return self._PREGEN_ID if self._pregenerates else None

    def resume_args(self, thread_id: str, prior_args: list[str]) -> list[str]:
        if self._pregenerates:
            scrubbed: list[str] = []
            skip = 0
            for arg in prior_args:
                if skip:
                    skip -= 1
                    continue
                if arg in ("--session-id", "--resume"):
                    skip = 1
                    continue
                scrubbed.append(arg)
            return ["--resume", thread_id, *scrubbed]
        scrubbed = list(prior_args)
        if len(scrubbed) >= 2 and scrubbed[0] == "resume":
            scrubbed = scrubbed[2:]
        return ["resume", thread_id, *scrubbed]

    async def conversation_exists(
        self, thread_id: str, cwd: str, launch_target: Any
    ) -> bool:
        return self.exists

    async def capture_thread_id(
        self,
        runtime: Any,
        session_id: str,
        cwd: str,
        since: Any,
        launch_target: Any,
    ) -> None:
        return None


@dataclass
class _PaneTarget:
    session: str
    window: str
    pane: str
    pane_pid: int
    pane_dead: bool = False


class _FakeTmux:
    def __init__(self) -> None:
        self.kill_calls: list[str] = []
        self.pipe_calls: list[tuple[str, Path]] = []
        self.stop_pipe_calls: list[str] = []
        self.describe_pane_dead = False

    async def kill_session(self, name: str) -> None:
        self.kill_calls.append(name)

    async def stop_pipe(self, target: str) -> None:
        self.stop_pipe_calls.append(target)

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
        self.pipe_calls.append((pane, log))

    async def describe_target(self, target: str) -> _PaneTarget:
        # Return the same pane id the caller passed in so the test
        # can assert reattach went to the user's pane, not a fresh
        # ``new-session``-spawned one.
        return _PaneTarget(
            session="user-tmux",
            window="0",
            pane=target,
            pane_pid=9999,
            pane_dead=self.describe_pane_dead,
        )


class _FakeRegistry:
    """Stand-in for SessionRuntime.registry.

    `_FakeRuntime` is used by the restore/create paths, which now ask
    the registry for the inner plugin so they can spawn a rate-limit
    refresh watcher. By default returns a bare object — no
    ``probe_account_rate_limit`` — so the spawn early-exits without
    scheduling a real task. Watcher-focused tests pass a real stub via
    ``plugin_override`` to exercise the loop.
    """

    def __init__(self, plugin_override: Any = None) -> None:
        self._override = plugin_override

    def get(self, _backend: str) -> Any:
        return self._override if self._override is not None else object()


class _FakeRuntime:
    """Minimal runtime stub for ``TmuxPlugin.restore_session``."""

    def __init__(self, inner_plugin: Any = None) -> None:
        self.tmux = _FakeTmux()
        self.file_offsets: dict[str, int] = {}
        self._tmux_thread_id_watchers: dict[str, Any] = {}
        self._tmux_rate_limit_watchers: dict[str, Any] = {}
        self.monitor_tasks: dict[str, Any] = {}
        self.registry = _FakeRegistry(plugin_override=inner_plugin)
        self.updates: list[dict[str, Any]] = []
        self.field_updates: list[dict[str, Any]] = []
        self.created: list[Any] = []
        self._session_dir_root: Path | None = None
        # Per-call record so individual tests can assert that the
        # tmux launch sites actually request a remote PTY.
        self.command_calls: list[dict[str, Any]] = []
        # Watcher tests override entries here; the refresh loop reads
        # via storage.get_session, so the storage stub returns whatever
        # is set on this map.
        self.sessions_by_id: dict[str, Any] = {}

    def _find_launch_target(self, _lt_id: str | None) -> None:
        return None

    def _command_for_backend(
        self,
        backend: str,
        args: list[str],
        _lt: Any,
        _cwd: str,
        *,
        allocate_tty: bool = False,
        session_id: str | None = None,
    ) -> list[str]:
        self.command_calls.append(
            {
                "backend": backend,
                "args": args,
                "allocate_tty": allocate_tty,
                "session_id": session_id,
            }
        )
        return [backend, *args]

    def _ensure_monitor(self, _sid: str) -> None:
        return None

    async def _record_system_event(self, *args: Any, **kwargs: Any) -> None:
        return None

    def _generate_session_id(self, backend: str) -> str:
        return f"{backend}-fake"

    def _session_dir(self, session_id: str) -> Path:
        root = self._session_dir_root or Path("/tmp")
        target = root / session_id
        target.mkdir(parents=True, exist_ok=True)
        return target

    def get_session(self, session_id: str) -> Any:
        # Return the most-recently-created record; import_thread_via_resume
        # uses this for its final ``return runtime.get_session(id)``.
        for record in reversed(self.created):
            if record.id == session_id:
                return record
        raise KeyError(session_id)

    async def update_session_fields(
        self, session_id: str, *, publish: bool = True, **updates: Any
    ) -> None:
        self.field_updates.append({"sid": session_id, "publish": publish, **updates})

    class _Storage:
        def __init__(self, parent: "_FakeRuntime") -> None:
            self._parent = parent

        def update_session(self, sid: str, **fields: Any) -> None:
            self._parent.updates.append({"sid": sid, **fields})

        def create_session(self, record: Any) -> None:
            self._parent.created.append(record)

        def get_session(self, session_id: str) -> Any:
            return self._parent.sessions_by_id.get(session_id)

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
    agent = _FakeAgentPlugin(pregenerates=True, exists=False)
    runtime = _FakeRuntime(inner_plugin=agent)

    # Cycle 1 — conversation file absent.
    session_1 = _exited_claude_session("sess-1", uuid_str, tmp_path)
    await plugin.restore_session(cast(Any, runtime), session_1)

    cycle1 = runtime.updates[-1]
    state1 = cycle1["transport_state"]
    # Verbatim launch (--session-id still in stored args).
    assert state1["launch_args"] == ["--session-id", uuid_str]
    # CRITICAL: thread_id is preserved even though no resume happened.
    assert state1["thread_id"] == uuid_str

    # Cycle 2 — user has typed; conversation file now exists.
    agent.exists = True
    session_2 = _exited_claude_session("sess-1", uuid_str, tmp_path)
    await plugin.restore_session(cast(Any, runtime), session_2)

    cycle2 = runtime.updates[-1]
    state2 = cycle2["transport_state"]
    # Now we route through --resume, not --session-id.
    assert state2["launch_args"] == ["--resume", uuid_str]
    assert state2["thread_id"] == uuid_str

    # Both cycles must request a remote PTY — otherwise the remote
    # claude flips to ``--print`` mode and errors on the missing stdin.
    assert [call["allocate_tty"] for call in runtime.command_calls] == [True, True]


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

    monkeypatch.setattr(
        TmuxPlugin,
        "_spawn_thread_id_watcher",
        lambda *_args, **_kwargs: None,
    )

    agent = _FakeAgentPlugin(pregenerates=False, exists=False)
    runtime = _FakeRuntime(inner_plugin=agent)
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
    assert runtime.command_calls[-1]["allocate_tty"] is True


def _exited_attached_session(sid: str, tmp_path: Path) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=sid,
        backend="claude_code",
        source=SessionSource.ATTACHED_TMUX,
        transport="tmux",
        title="t",
        cwd="/Users/me/proj",
        status=SessionStatus.EXITED,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        # ATTACHED sessions never get a ``launch_args`` — we attach
        # rather than launch — and the pane id points at the user's
        # external tmux pane.
        transport_state={
            "tmux_session": "user-tmux",
            "tmux_window": "0",
            "tmux_pane": "%42",
            "pid": 1234,
        },
        raw_log_path=str(tmp_path / f"{sid}.raw.log"),
        structured_log_path=str(tmp_path / f"{sid}.events.jsonl"),
    )


@pytest.mark.asyncio
async def test_restore_session_attached_repipes_and_does_not_kill(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    """ATTACHED-source reconnect must re-pipe to the user's existing
    pane, not kill it and spawn a fresh inner CLI. Symptom of the
    prior bug: clicking Reconnect on an attached session killed the
    user's tmux and launched a brand-new Claude conversation.
    """
    runtime = _FakeRuntime(inner_plugin=_FakeAgentPlugin(pregenerates=True))
    session = _exited_attached_session("sess-att", tmp_path)
    await plugin.restore_session(cast(Any, runtime), session)

    # User's tmux session must NOT be killed.
    assert runtime.tmux.kill_calls == [], runtime.tmux.kill_calls
    # pipe-pane must be re-established against the user's pane.
    assert len(runtime.tmux.pipe_calls) == 1
    pane, log = runtime.tmux.pipe_calls[0]
    assert pane == "%42"
    assert log == Path(session.raw_log_path)
    # Status update is IDLE (matching ``attach_tmux``), not STARTING,
    # and transport_state retains the user's pane coordinates.
    update = runtime.updates[-1]
    assert update["status"] == SessionStatus.IDLE
    assert update["transport_state"]["tmux_pane"] == "%42"
    # No launch_args ever stored for attached sessions.
    assert "launch_args" not in update["transport_state"]


@pytest.mark.asyncio
async def test_restore_session_attached_codex_respawns_thread_id_watcher(
    plugin: TmuxPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An attached codex session that hadn't captured a thread_id
    before terminate (user hadn't typed yet) loses its rollout
    watcher at terminate. The reattach path must respawn it so the
    uuid gets captured once the user does type."""
    spawned: list[dict[str, Any]] = []

    def fake_spawn(
        self: TmuxPlugin,
        runtime: Any,
        backend: str,
        session_id: str,
        cwd: str,
        since: Any,
        launch_target: Any,
    ) -> None:
        spawned.append({"session_id": session_id, "cwd": cwd})

    monkeypatch.setattr(TmuxPlugin, "_spawn_thread_id_watcher", fake_spawn)

    runtime = _FakeRuntime(inner_plugin=_FakeAgentPlugin(pregenerates=False))
    now = datetime.now(UTC)
    session = SessionRecord(
        id="sess-codex-att",
        backend="codex",
        source=SessionSource.ATTACHED_TMUX,
        transport="tmux",
        title="t",
        cwd="/Users/me/proj",
        status=SessionStatus.EXITED,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        # No thread_id — user attached before first input.
        transport_state={
            "tmux_session": "user-tmux",
            "tmux_window": "0",
            "tmux_pane": "%99",
            "pid": 1234,
        },
        raw_log_path=str(tmp_path / "sess-codex-att.raw.log"),
        structured_log_path=str(tmp_path / "sess-codex-att.events.jsonl"),
    )
    await plugin.restore_session(cast(Any, runtime), session)

    assert len(spawned) == 1, spawned
    assert spawned[0]["session_id"] == "sess-codex-att"
    assert spawned[0]["cwd"] == "/Users/me/proj"


@pytest.mark.asyncio
async def test_restore_session_attached_carries_forward_thread_id(
    plugin: TmuxPlugin, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the attached codex session already captured a uuid in its
    prior life, the reattach must preserve it (so codex's resume path
    keeps the uuid) and must not redundantly spawn the watcher."""
    spawned: list[Any] = []

    def fake_spawn(*args: Any, **kwargs: Any) -> None:
        spawned.append(args)

    monkeypatch.setattr(TmuxPlugin, "_spawn_thread_id_watcher", fake_spawn)

    runtime = _FakeRuntime(inner_plugin=_FakeAgentPlugin(pregenerates=False))
    now = datetime.now(UTC)
    uuid_str = "abcdef01-2345-6789-abcd-ef0123456789"
    session = SessionRecord(
        id="sess-codex-att-2",
        backend="codex",
        source=SessionSource.ATTACHED_TMUX,
        transport="tmux",
        title="t",
        cwd="/Users/me/proj",
        status=SessionStatus.EXITED,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        transport_state={
            "tmux_session": "user-tmux",
            "tmux_window": "0",
            "tmux_pane": "%99",
            "pid": 1234,
            "thread_id": uuid_str,
        },
        raw_log_path=str(tmp_path / "sess-codex-att-2.raw.log"),
        structured_log_path=str(tmp_path / "sess-codex-att-2.events.jsonl"),
    )
    await plugin.restore_session(cast(Any, runtime), session)

    update = runtime.updates[-1]
    assert update["transport_state"]["thread_id"] == uuid_str
    assert spawned == []


@pytest.mark.asyncio
async def test_restore_session_attached_dead_pane_flips_back_to_exited(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    """If the user's external pane has died between terminate and
    reconnect, we can't re-pipe to it — surface the failure as a
    system event and leave the session EXITED rather than silently
    spawning a fresh inner CLI under the same session id."""
    runtime = _FakeRuntime()
    runtime.tmux.describe_pane_dead = True
    session = _exited_attached_session("sess-att-dead", tmp_path)
    await plugin.restore_session(cast(Any, runtime), session)

    # No pipe attempt, no kill, no storage update — reattach should
    # bail before producing any user-visible state change.
    assert runtime.tmux.pipe_calls == []
    assert runtime.tmux.kill_calls == []
    assert runtime.updates == []


@pytest.mark.asyncio
async def test_import_thread_via_resume_claude_builds_resume_command(
    plugin: TmuxPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing a Claude thread under ``launch_mode=tmux_wrapper``
    must spawn a tmux session whose command is ``claude --resume <uuid>``
    against the thread's cwd, with the same thread_id pinned in
    ``transport_state``."""
    uuid_str = "11111111-1111-1111-1111-111111111111"
    cwd = "/Users/me/proj"

    # Pretend the conversation file exists.
    runtime = _FakeRuntime(
        inner_plugin=_FakeAgentPlugin(pregenerates=True, exists=True)
    )
    runtime._session_dir_root = tmp_path

    captured_commands: list[list[str]] = []
    original_start = runtime.tmux.start_managed_session

    async def start_capture(
        session_id: str, cwd: str, command: list[str]
    ) -> _PaneTarget:
        captured_commands.append(command)
        return await original_start(session_id, cwd, command)

    runtime.tmux.start_managed_session = start_capture  # type: ignore[method-assign]

    record = await plugin.import_thread_via_resume(
        cast(Any, runtime),
        backend="claude_code",
        thread_id=uuid_str,
        cwd=cwd,
        launch_target_id=None,
        title="resumed",
    )

    # Command should be ``claude --resume <uuid>`` — the fake
    # ``_command_for_backend`` prepends the backend id, _resume_args
    # produces the rest.
    assert captured_commands == [["claude_code", "--resume", uuid_str]]
    assert record.backend == "claude_code"
    assert record.launch_mode == "tmux_wrapper"
    assert record.transport_state["thread_id"] == uuid_str
    assert record.transport_state["launch_args"] == ["--resume", uuid_str]
    assert record.cwd == cwd
    # The import path must request a remote PTY too — same TTY rules as
    # create_session and restore.
    assert runtime.command_calls[-1]["allocate_tty"] is True


@pytest.mark.asyncio
async def test_import_thread_via_resume_codex_uses_resume_subcommand(
    plugin: TmuxPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex's resume is a sub-command (``codex resume <uuid>``), not a
    flag; the tmux import path must use it verbatim so the inner CLI
    binds to the existing rollout."""
    uuid_str = "22222222-2222-2222-2222-222222222222"

    runtime = _FakeRuntime(
        inner_plugin=_FakeAgentPlugin(pregenerates=False, exists=True)
    )
    runtime._session_dir_root = tmp_path
    captured_commands: list[list[str]] = []
    original_start = runtime.tmux.start_managed_session

    async def start_capture(
        session_id: str, cwd: str, command: list[str]
    ) -> _PaneTarget:
        captured_commands.append(command)
        return await original_start(session_id, cwd, command)

    runtime.tmux.start_managed_session = start_capture  # type: ignore[method-assign]

    record = await plugin.import_thread_via_resume(
        cast(Any, runtime),
        backend="codex",
        thread_id=uuid_str,
        cwd="/Users/me/proj",
        launch_target_id=None,
        title="resumed",
    )

    assert captured_commands == [["codex", "resume", uuid_str]]
    assert record.transport_state["launch_args"] == ["resume", uuid_str]
    assert runtime.command_calls[-1]["allocate_tty"] is True


@pytest.mark.asyncio
async def test_import_thread_via_resume_refuses_when_conversation_missing(
    plugin: TmuxPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No conversation file → no point launching tmux just to have the
    inner CLI fail with "no such session". Surface a clear 400 instead.
    """
    from fastapi import HTTPException

    runtime = _FakeRuntime(
        inner_plugin=_FakeAgentPlugin(pregenerates=True, exists=False)
    )
    runtime._session_dir_root = tmp_path

    with pytest.raises(HTTPException) as exc:
        await plugin.import_thread_via_resume(
            cast(Any, runtime),
            backend="claude_code",
            thread_id="ffffffff-ffff-ffff-ffff-ffffffffffff",
            cwd="/Users/me/proj",
            launch_target_id=None,
            title="nope",
        )
    assert exc.value.status_code == 400
    # No tmux session should have been created either.
    assert runtime.created == []


@pytest.mark.asyncio
async def test_create_session_requests_remote_pty(
    plugin: TmuxPlugin, tmp_path: Path
) -> None:
    """``create_session`` must request a remote PTY when building the
    inner command. Without ``allocate_tty=True`` a remote claude flips
    to ``--print`` mode and errors on the missing stdin TTY.
    """
    from waypoint.git_meta import GitMeta
    from waypoint.schemas import LaunchMode, SessionCreateRequest

    runtime = _FakeRuntime(inner_plugin=_FakeAgentPlugin(pregenerates=True))
    runtime._session_dir_root = tmp_path
    request = SessionCreateRequest(
        backend="claude_code",
        cwd="/Users/me/proj",
        args=["--model", "sonnet"],
        launch_mode=LaunchMode.TMUX_WRAPPER,
    )
    record = await plugin.create_session(
        cast(Any, runtime),
        request,
        session_id="sess-create",
        launch_target=None,
        title="t",
        raw_log=tmp_path / "raw.log",
        structured_log=tmp_path / "events.jsonl",
        git_meta=GitMeta(repo_name=None, branch=None),
        permission_mode=None,
        resolved_model=None,
        resolved_effort=None,
    )

    assert record.backend == "claude_code"
    assert runtime.command_calls[-1]["allocate_tty"] is True
    # Claude path pre-pins --session-id <uuid> ahead of the user's args.
    assert runtime.command_calls[-1]["args"][0] == "--session-id"
    assert runtime.command_calls[-1]["args"][-2:] == ["--model", "sonnet"]


class _InnerPluginStub:
    """Stub of the wrapped backend's plugin for rate-limit watcher tests.

    Just the slice that ``_spawn_rate_limit_watcher`` and the refresh
    loop reach: a ``probe_account_rate_limit`` coroutine that records
    each call and a ``cli_binary`` attribute the codex branch reads.
    """

    cli_binary = "claude"

    def __init__(self) -> None:
        self.calls = 0

    async def probe_account_rate_limit(
        self, _runtime: Any, _launch_target: Any, *, cwd: str | None = None
    ) -> Any:
        self.calls += 1
        from waypoint.schemas import SessionRateLimitUsage

        return SessionRateLimitUsage(
            source="claude_code",
            updated_at=datetime.now(UTC),
            windows=[],
        )


def _watcher_session(sid: str, status: SessionStatus = SessionStatus.STARTING):
    now = datetime.now(UTC)
    return SessionRecord(
        id=sid,
        backend="claude_code",
        source=SessionSource.MANAGED,
        transport="tmux",
        title="t",
        cwd="/Users/me/proj",
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=f"/tmp/{sid}.raw.log",
        structured_log_path=f"/tmp/{sid}.events.jsonl",
    )


@pytest.mark.asyncio
async def test_rate_limit_watcher_spawn_dedupes(plugin: TmuxPlugin) -> None:
    """Spawning twice with the same session id leaves a single task on
    the runtime's watcher map — the guard at the top of
    ``_spawn_rate_limit_watcher`` short-circuits the second call."""
    inner = _InnerPluginStub()
    runtime = _FakeRuntime(inner_plugin=inner)
    session = _watcher_session("sess-dedupe")
    runtime.sessions_by_id[session.id] = session
    plugin._spawn_rate_limit_watcher(cast(Any, runtime), session)
    first = runtime._tmux_rate_limit_watchers[session.id]
    plugin._spawn_rate_limit_watcher(cast(Any, runtime), session)
    second = runtime._tmux_rate_limit_watchers[session.id]
    assert first is second
    first.cancel()
    with suppress(asyncio.CancelledError):
        await first


@pytest.mark.asyncio
async def test_rate_limit_watcher_terminate_cancels_and_clears(
    plugin: TmuxPlugin,
) -> None:
    """``terminate_session`` cancels the running watcher and pops its
    entry from the map so a subsequent reattach can spawn a fresh one."""
    inner = _InnerPluginStub()
    runtime = _FakeRuntime(inner_plugin=inner)
    session = _watcher_session("sess-term")
    runtime.sessions_by_id[session.id] = session
    plugin._spawn_rate_limit_watcher(cast(Any, runtime), session)
    task = runtime._tmux_rate_limit_watchers[session.id]
    # Let the first iteration run so the probe is observed.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await plugin.terminate_session(cast(Any, runtime), session)
    assert session.id not in runtime._tmux_rate_limit_watchers
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_rate_limit_watcher_stops_on_natural_exit(
    plugin: TmuxPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural transitions to EXITED (pane closed, CLI typed /exit) are
    detected via storage status and break the loop without going through
    ``terminate_session``."""
    # Collapse the 300 s sleep so the loop's next iteration runs in the
    # same event-loop tick the test status flip happens on.
    real_sleep = asyncio.sleep

    async def instant_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr("waypoint.backends.tmux.plugin.asyncio.sleep", instant_sleep)

    inner = _InnerPluginStub()
    runtime = _FakeRuntime(inner_plugin=inner)
    session = _watcher_session("sess-natural-exit")
    runtime.sessions_by_id[session.id] = session
    plugin._spawn_rate_limit_watcher(cast(Any, runtime), session)
    task = runtime._tmux_rate_limit_watchers[session.id]

    # Yield repeatedly so the loop runs at least one full iteration.
    for _ in range(5):
        await asyncio.sleep(0)
    assert inner.calls >= 1, "probe should have fired at least once"

    # Flip status to EXITED without invoking terminate_session.
    runtime.sessions_by_id[session.id] = _watcher_session(
        session.id, status=SessionStatus.EXITED
    )
    # The loop's next iteration should observe the EXITED status and exit.
    await asyncio.wait_for(task, timeout=1.0)
    assert session.id not in runtime._tmux_rate_limit_watchers
