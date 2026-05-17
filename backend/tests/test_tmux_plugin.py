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


def test_resume_args_claude_does_not_double_inject_on_second_cycle(
    plugin: TmuxPlugin,
) -> None:
    """Reconnect cycle N must not stack a fresh ``--resume`` on top of
    the prior cycle's output. Without scrubbing the leading ``--resume``,
    the stored ``launch_args`` would grow unboundedly across reconnects.
    """
    cycle1 = plugin._resume_args(
        "claude_code", "abc-123", ["--session-id", "abc-123", "--model", "sonnet"]
    )
    assert cycle1 == ["--resume", "abc-123", "--model", "sonnet"]
    cycle2 = plugin._resume_args("claude_code", "abc-123", cycle1)
    assert cycle2 == ["--resume", "abc-123", "--model", "sonnet"]


def test_resume_args_codex_does_not_double_inject_on_second_cycle(
    plugin: TmuxPlugin,
) -> None:
    cycle1 = plugin._resume_args("codex", "abc-123", ["--model", "o3"])
    assert cycle1 == ["resume", "abc-123", "--model", "o3"]
    cycle2 = plugin._resume_args("codex", "abc-123", cycle1)
    assert cycle2 == ["resume", "abc-123", "--model", "o3"]


@pytest.mark.parametrize(
    "backend,model,effort,permission_mode,expected",
    [
        # Claude: each picked knob maps 1:1 to a flag.
        ("claude_code", "opus", None, None, ["--model", "opus"]),
        ("claude_code", None, "high", None, ["--effort", "high"]),
        ("claude_code", None, None, "plan", ["--permission-mode", "plan"]),
        (
            "claude_code",
            "opus",
            "high",
            "acceptEdits",
            [
                "--model",
                "opus",
                "--effort",
                "high",
                "--permission-mode",
                "acceptEdits",
            ],
        ),
        ("claude_code", None, None, None, []),
        # Codex: only the model and the two unambiguous permission presets
        # translate to launch flags. Effort and the collaboration-mode
        # presets ("plan", "auto_review") have no CLI flag mapping and
        # are intentionally dropped — the SessionRecord-side caller
        # mirrors this by storing None for the unapplied fields.
        ("codex", "gpt-5", None, None, ["-m", "gpt-5"]),
        (
            "codex",
            None,
            None,
            "default",
            ["-a", "on-request", "-s", "workspace-write"],
        ),
        (
            "codex",
            None,
            None,
            "full_access",
            ["--dangerously-bypass-approvals-and-sandbox"],
        ),
        ("codex", None, "high", None, []),
        ("codex", None, None, "plan", []),
        ("codex", None, None, "auto_review", []),
        ("codex", None, None, None, []),
    ],
)
def test_inner_cli_flags(
    plugin: TmuxPlugin,
    backend: str,
    model: str | None,
    effort: str | None,
    permission_mode: str | None,
    expected: list[str],
) -> None:
    assert (
        plugin._inner_cli_flags(
            backend,
            model=model,
            effort=effort,
            permission_mode=permission_mode,
        )
        == expected
    )


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
    # don't actually want to spawn ssh in a unit test. Both backends
    # now go through ``_ssh_capture`` (claude globs project dirs since
    # the dashed-cwd key can't be computed reliably for remote paths;
    # codex always needed the rollout glob).
    calls: list[tuple[str, str]] = []

    async def fake_ssh_capture(target: object, remote_cmd: str) -> str:
        calls.append(("capture", remote_cmd))
        if "00000000-0000-0000-0000-000000000001" in remote_cmd:
            return (
                "/remote/.claude/projects/-remote-proj/"
                "00000000-0000-0000-0000-000000000001.jsonl\n"
            )
        if "found" in remote_cmd:
            return "/remote/.codex/sessions/2026/05/16/rollout-2026-05-16T10-00-00-found.jsonl\n"
        return ""

    monkeypatch.setattr(TmuxPlugin, "_ssh_capture", staticmethod(fake_ssh_capture))

    # ``_ssh_capture`` is stubbed, so the actual SshLaunchTargetConfig
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
    assert [kind for kind, _ in calls] == ["capture", "capture"]


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
    # single-quoted span so the remote shell expands it. Also asserts the
    # query is a glob across ``projects/*`` so a mismatch between the
    # raw ``~/foo`` cwd and claude's dashed key doesn't cause a miss.
    captured: list[str] = []

    async def fake_ssh_capture(target: object, remote_cmd: str) -> str:
        captured.append(remote_cmd)
        return ""

    monkeypatch.setattr(TmuxPlugin, "_ssh_capture", staticmethod(fake_ssh_capture))
    target = cast(SshLaunchTargetConfig, object())
    await plugin._conversation_exists(
        "claude_code",
        "00000000-0000-0000-0000-000000000001",
        "/Users/me/proj",
        target,
    )
    assert len(captured) == 1
    cmd = captured[0]
    assert cmd.startswith("ls $HOME/.claude/projects/*/")
    # ``$HOME`` must not be inside a single-quoted span, otherwise the
    # remote shell sees the four literal characters instead of the
    # home-dir path.
    assert "'$HOME" not in cmd
    assert "$HOME/" in cmd
    # Unconditional glob over project dirs — the dashed-cwd key isn't
    # embedded.
    assert "/Users-me-proj/" not in cmd


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
    pane_dead: bool = False


class _FakeTmux:
    def __init__(self) -> None:
        self.kill_calls: list[str] = []
        self.pipe_calls: list[tuple[str, Path]] = []
        self.describe_pane_dead = False

    async def kill_session(self, name: str) -> None:
        self.kill_calls.append(name)

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


class _FakeRuntime:
    """Minimal runtime stub for ``TmuxPlugin.restore_session``."""

    def __init__(self) -> None:
        self.tmux = _FakeTmux()
        self.file_offsets: dict[str, int] = {}
        self._tmux_thread_id_watchers: dict[str, Any] = {}
        self.updates: list[dict[str, Any]] = []
        self.created: list[Any] = []
        self._session_dir_root: Path | None = None
        # Per-call record so individual tests can assert that the
        # tmux launch sites actually request a remote PTY.
        self.command_calls: list[dict[str, Any]] = []

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
    ) -> list[str]:
        self.command_calls.append(
            {"backend": backend, "args": args, "allocate_tty": allocate_tty}
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

    class _Storage:
        def __init__(self, parent: "_FakeRuntime") -> None:
            self._parent = parent

        def update_session(self, sid: str, **fields: Any) -> None:
            self._parent.updates.append({"sid": sid, **fields})

        def create_session(self, record: Any) -> None:
            self._parent.created.append(record)

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
    runtime = _FakeRuntime()
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
        session_id: str,
        cwd: str,
        since: Any,
        launch_target: Any,
    ) -> None:
        spawned.append({"session_id": session_id, "cwd": cwd})

    monkeypatch.setattr(TmuxPlugin, "_spawn_codex_thread_id_watcher", fake_spawn)

    runtime = _FakeRuntime()
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

    monkeypatch.setattr(TmuxPlugin, "_spawn_codex_thread_id_watcher", fake_spawn)

    runtime = _FakeRuntime()
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
    async def present(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(TmuxPlugin, "_conversation_exists", present)

    runtime = _FakeRuntime()
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

    async def present(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(TmuxPlugin, "_conversation_exists", present)

    runtime = _FakeRuntime()
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

    async def absent(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(TmuxPlugin, "_conversation_exists", absent)

    runtime = _FakeRuntime()
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

    runtime = _FakeRuntime()
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
