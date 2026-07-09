import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from waypoint.backends.tmux.adapter import TmuxAdapter, TmuxError
from waypoint.backends.tmux.transport import TmuxTransport


def test_send_input_uses_literal_mode_and_submit() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.send_input("%1", "hello world", submit=True))

    assert commands == [
        ("send-keys", "-t", "%1", "-l", "--", "hello world"),
        ("send-keys", "-t", "%1", "Enter"),
    ]


def test_send_input_pastes_multiline_text_then_submits() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.send_input("%2", "first line\nsecond line", submit=True))

    # Multi-line text is delivered as a bracketed paste rather than a burst
    # of literal keystrokes + Ctrl-J: the C-j/Enter emulation intermittently
    # left the message typed-but-unsent against the wrapped TUI's line editor
    # (which has bracketed paste enabled). The paste carries the whole block;
    # the trailing Enter that ``submit=True`` adds submits it.
    set_buffer, paste_buffer, submit = commands
    assert set_buffer[:2] == ("set-buffer", "-b")
    buffer_name = set_buffer[2]
    assert buffer_name.startswith("waypoint-input-")
    assert set_buffer[3:] == ("--", "first line\nsecond line")
    # The paste targets the same buffer the set-buffer just wrote.
    assert paste_buffer == (
        "paste-buffer",
        "-d",
        "-p",
        "-r",
        "-b",
        buffer_name,
        "-t",
        "%2",
    )
    assert submit == ("send-keys", "-t", "%2", "Enter")


def test_send_input_pastes_carriage_returns_normalized_to_lf() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(
        adapter.send_input("%2", "msg\r\n\r\nAttached files:\r\n- /tmp/a", submit=True)
    )

    # CRLF/CR are normalized to LF before the buffer is set, so the paste
    # never carries a bare CR that the inner app would read as accept-line.
    set_buffer = commands[0]
    assert set_buffer[:2] == ("set-buffer", "-b")
    assert set_buffer[3:] == ("--", "msg\n\nAttached files:\n- /tmp/a")


def test_multiline_sends_use_distinct_buffer_names() -> None:
    names: list[str] = []

    async def fake_run(*args: str) -> str:
        if args[0] == "set-buffer":
            names.append(args[2])
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    # Input is not serialized per pane, so each send must use its own buffer
    # name or concurrent sends to the same pane would clobber each other.
    asyncio.run(adapter.send_input("%2", "a\nb", submit=True))
    asyncio.run(adapter.send_input("%2", "c\nd", submit=True))

    assert len(names) == 2
    assert names[0] != names[1]


def test_multiline_paste_failure_drops_orphaned_buffer() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        if args[0] == "paste-buffer":
            raise TmuxError("pane gone")
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    with pytest.raises(TmuxError):
        asyncio.run(adapter.send_input("%2", "a\nb", submit=True))

    # paste-buffer -d never deleted the buffer (it failed), so a best-effort
    # delete-buffer runs for the same buffer before the error propagates.
    set_buffer, paste_buffer, delete_buffer = commands
    buffer_name = set_buffer[2]
    assert paste_buffer[0] == "paste-buffer"
    assert delete_buffer == ("delete-buffer", "-b", buffer_name)


def test_send_bytes_forwards_hex_escape_sequences() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    # ESC [ A — up arrow.
    asyncio.run(adapter.send_bytes("%1", b"\x1b[A"))
    # Ctrl-C.
    asyncio.run(adapter.send_bytes("%1", b"\x03"))
    # UTF-8 multibyte (é).
    asyncio.run(adapter.send_bytes("%1", "é".encode()))

    assert commands == [
        ("send-keys", "-t", "%1", "-H", "1b", "5b", "41"),
        ("send-keys", "-t", "%1", "-H", "03"),
        ("send-keys", "-t", "%1", "-H", "c3", "a9"),
    ]


def test_send_bytes_skips_empty_payload() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.send_bytes("%1", b""))

    assert commands == []


def test_resize_window_pins_manual_size_and_disables_status() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.resize_window("waypoint-abc", 120, 40))

    assert commands == [
        ("set-option", "-t", "waypoint-abc", "window-size", "manual"),
        ("set-option", "-t", "waypoint-abc", "status", "off"),
        ("resize-window", "-t", "waypoint-abc", "-x", "120", "-y", "40"),
    ]


def test_resize_pane_targets_pane_id() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.resize_pane("%4", 120, 40))

    assert commands == [
        ("resize-pane", "-t", "%4", "-x", "120", "-y", "40"),
    ]


def test_pane_screen_state_parses_alt_and_cursor() -> None:
    async def fake_run(*args: str) -> str:
        assert args == (
            "display-message",
            "-p",
            "-t",
            "%4",
            "#{alternate_on}|#{cursor_x}|#{cursor_y}",
        )
        # tmux reports 0-based coordinates; the helper returns 1-based.
        return "1|7|29\n"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    alt, col, row = asyncio.run(adapter.pane_screen_state("%4"))
    assert alt is True
    assert (col, row) == (8, 30)


def test_pane_screen_state_handles_normal_screen() -> None:
    async def fake_run(*args: str) -> str:
        return "0|0|0"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    alt, col, row = asyncio.run(adapter.pane_screen_state("%4"))
    assert alt is False
    assert (col, row) == (1, 1)


def test_describe_target_parses_live_pane() -> None:
    async def fake_run(*args: str) -> str:
        return "sess|0|%7|/home/u|0|4242\n"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    target = asyncio.run(adapter.describe_target("%7"))
    assert target.pane == "%7"
    assert target.pane_dead is False
    assert target.pane_pid == 4242


def test_describe_target_raises_on_missing_pane() -> None:
    # `display-message -t` does not validate the target: a missing pane expands
    # to all-empty fields with exit 0 ("|||||"). describe_target must treat that
    # as a dead target so liveness checks mark the session exited instead of
    # seeing pane_dead=False on a phantom pane.
    async def fake_run(*args: str) -> str:
        return "|||||"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    with pytest.raises(TmuxError):
        asyncio.run(adapter.describe_target("%77"))


def test_target_exists_false_for_missing_pane() -> None:
    async def fake_run(*args: str) -> str:
        return "|||||"

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    assert asyncio.run(adapter.target_exists("%77")) is False


def test_submit_sends_bare_enter() -> None:
    commands: list[tuple[str, ...]] = []

    async def fake_run(*args: str) -> str:
        commands.append(args)
        return ""

    adapter = TmuxAdapter()
    adapter._run = fake_run  # type: ignore[method-assign]

    asyncio.run(adapter.submit("%1"))

    assert commands == [("send-keys", "-t", "%1", "Enter")]


class _FakeAdapter:
    """Adapter stub for the transport submit-confirm loop. The pane state is a
    function of how many Enters have landed: the composer reports cleared once
    ``clear_after`` submits land, and a modal dialog appears once
    ``dialog_after`` submits land (the message submitted and opened one)."""

    def __init__(
        self, *, clear_after: int = 1, dialog_after: int | None = None
    ) -> None:
        self.submits = 0
        self.captures = 0
        self._clear_after = clear_after
        self._dialog_after = dialog_after

    async def submit(self, target: str) -> None:
        self.submits += 1

    async def capture_snapshot(self, target: str, start_line: int = -200) -> str:
        self.captures += 1
        if self._dialog_after is not None and self.submits >= self._dialog_after:
            return "DIALOG"
        return "SUBMITTED" if self.submits >= self._clear_after else "PENDING"


class _Confirmer:
    def pane_ready_for_input(self, pane_text: str) -> bool:
        return True

    def confirm_pane_submit(self, pane_text: str, sent_text: str) -> bool:
        return pane_text == "SUBMITTED"

    def pane_shows_blocking_dialog(self, pane_text: str) -> bool:
        return pane_text == "DIALOG"


def _transport(adapter: Any) -> TmuxTransport:
    return TmuxTransport(SimpleNamespace(tmux=adapter))  # type: ignore[arg-type]


def test_submit_confirmed_retries_until_composer_clears() -> None:
    # First Enter absorbed (composer still populated), second lands.
    adapter = _FakeAdapter(clear_after=2)
    transport = _transport(adapter)

    asyncio.run(
        transport._submit_confirmed(
            "%1", _Confirmer(), "msg", attempts=8, poll_seconds=0.0
        )
    )

    assert adapter.submits == 2


def test_submit_confirmed_stops_after_first_success() -> None:
    # A landed Enter must not be followed by a stray one (could hit a started
    # turn or a dialog).
    adapter = _FakeAdapter(clear_after=1)
    transport = _transport(adapter)

    asyncio.run(
        transport._submit_confirmed(
            "%1", _Confirmer(), "msg", attempts=8, poll_seconds=0.0
        )
    )

    assert adapter.submits == 1


def test_submit_confirmed_is_bounded_when_never_confirmed() -> None:
    # If the TUI never confirms, stop after `attempts` rather than spamming.
    adapter = _FakeAdapter(clear_after=999)
    transport = _transport(adapter)

    asyncio.run(
        transport._submit_confirmed(
            "%1", _Confirmer(), "msg", attempts=4, poll_seconds=0.0
        )
    )

    assert adapter.submits == 4


def test_submit_confirmed_default_budget_outlasts_image_ingestion() -> None:
    # The Claude TUI drops the submit Enter while it ingests pasted image paths
    # into chips; the default retry budget must keep firing through that window
    # so the first message with attachments is not stranded. Model a long
    # ingestion as many absorbed Enters before the composer clears.
    adapter = _FakeAdapter(clear_after=20)
    transport = _transport(adapter)

    asyncio.run(
        transport._submit_confirmed("%1", _Confirmer(), "msg", poll_seconds=0.0)
    )

    assert adapter.submits == 20


def test_submit_confirmed_stops_when_dialog_appears() -> None:
    # The message submits (one Enter) and opens a dialog; the loop must stop
    # rather than drive a second Enter into it — which would select an option
    # (e.g. auto-approve a tool).
    adapter = _FakeAdapter(clear_after=999, dialog_after=1)
    transport = _transport(adapter)

    asyncio.run(
        transport._submit_confirmed(
            "%1", _Confirmer(), "msg", attempts=8, poll_seconds=0.0
        )
    )

    assert adapter.submits == 1


class _RecordingAdapter:
    """Records the high-level calls send_input makes so we can assert which
    submit path (confirm-retry vs single Enter) the transport chose. Replays
    `boot_frames` "BOOTING" snapshots before "SUBMITTED" to model a pane that is
    still launching when the message arrives."""

    def __init__(self, boot_frames: int = 0, dialog: bool = False) -> None:
        self.calls: list[tuple] = []
        self._boot_frames = boot_frames
        self._dialog = dialog
        self._captures = 0
        self.submits = 0

    async def send_input(self, target, text, submit=True):
        self.calls.append(("send_input", target, text, submit))

    async def submit(self, target):
        self.calls.append(("submit", target))
        self.submits += 1

    async def capture_snapshot(self, target, start_line=-200):
        self.calls.append(("capture", target))
        if self._dialog:
            return "DIALOG"
        if self._captures < self._boot_frames:
            self._captures += 1
            return "BOOTING"  # composer not drawn yet
        self._captures += 1
        # Composer is drawn (READY); it reports cleared only once an Enter lands.
        return "SUBMITTED" if self.submits > 0 else "READY"


class _AgentConfirmer:
    id = "codex"

    def pane_ready_for_input(self, pane_text):
        return pane_text in ("READY", "SUBMITTED")

    def confirm_pane_submit(self, pane_text, sent_text):
        return pane_text == "SUBMITTED"

    def pane_shows_blocking_dialog(self, pane_text):
        return pane_text == "DIALOG"


class _PlainAgent:
    id = "opencode"  # no pane hooks -> not a confirmer


def _transport_with(agent, boot_frames: int = 0, dialog: bool = False):
    adapter = _RecordingAdapter(boot_frames, dialog)
    registry = SimpleNamespace(get=lambda backend_id: agent)
    runtime = SimpleNamespace(tmux=adapter, registry=registry)
    return TmuxTransport(runtime), adapter  # type: ignore[arg-type]


def _session(backend):
    return SimpleNamespace(backend=backend, transport_state={"tmux_pane": "%9"})


def test_send_input_uses_confirm_path_for_confirmer_agent() -> None:
    transport, adapter = _transport_with(_AgentConfirmer())
    asyncio.run(transport.send_input(_session("codex"), "hi"))
    # Pastes without submitting, then drives submit via the confirm loop —
    # NOT a single bundled submit. Resolved by agent id, not the tmux transport.
    assert ("send_input", "%9", "hi", False) in adapter.calls
    assert ("submit", "%9") in adapter.calls
    assert not any(c == ("send_input", "%9", "hi", True) for c in adapter.calls)


def test_send_input_single_enter_for_non_confirmer_agent() -> None:
    transport, adapter = _transport_with(_PlainAgent())
    asyncio.run(transport.send_input(_session("opencode"), "hi"))
    assert adapter.calls == [("send_input", "%9", "hi", True)]


def test_send_input_waits_for_ready_before_pasting() -> None:
    # A relaunched pane needs two frames to draw its composer; the transport
    # must poll readiness and only paste once it is ready, never into the boot
    # screen (which would drop the keystrokes).
    transport, adapter = _transport_with(_AgentConfirmer(), boot_frames=2)
    asyncio.run(transport.send_input(_session("codex"), "hi"))
    paste_idx = adapter.calls.index(("send_input", "%9", "hi", False))
    captures_before_paste = adapter.calls[:paste_idx].count(("capture", "%9"))
    assert captures_before_paste == 3  # two BOOTING frames, then ready


def test_await_pane_ready_is_bounded_when_never_ready() -> None:
    # A pane that never draws its composer must not block the request forever.
    transport, adapter = _transport_with(_AgentConfirmer(), boot_frames=99)
    asyncio.run(
        transport._await_pane_ready(
            "%9", _AgentConfirmer(), attempts=4, poll_seconds=0.0
        )
    )
    assert adapter.calls.count(("capture", "%9")) == 4


def test_await_pane_ready_raises_on_blocking_dialog() -> None:
    # A modal dialog must not be treated as a ready composer; surface it instead
    # of pasting/Enter'ing into it.
    transport, _ = _transport_with(_AgentConfirmer(), dialog=True)
    with pytest.raises(TmuxError):
        asyncio.run(transport._await_pane_ready("%9", _AgentConfirmer()))


def test_send_input_refuses_when_dialog_open() -> None:
    # Sending a message while an approval/trust dialog is open must not paste or
    # fire Enter into it (which would select an option). It surfaces an error
    # and never reaches the paste.
    transport, adapter = _transport_with(_AgentConfirmer(), dialog=True)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(transport.send_input(_session("codex"), "hi"))
    assert exc.value.status_code == 400
    assert not any(c[0] == "send_input" for c in adapter.calls)
    assert not any(c[0] == "submit" for c in adapter.calls)


class _CaptureOnlyAdapter:
    """Fake adapter for flush_before_restart's pane-idle fallback: records
    every capture-pane call and replays snapshots from a caller-supplied
    sequence function."""

    def __init__(self, snapshot_fn) -> None:
        self._snapshot_fn = snapshot_fn
        self.calls = 0

    async def capture_snapshot(self, target: str, start_line: int = -200) -> str:
        self.calls += 1
        return self._snapshot_fn(self.calls)


class _ArtifactAgent:
    """Fake wrapped-agent plugin for flush_before_restart's artifact-mtime
    path: records the config_dir it was called with and returns a fixed
    artifact path (or none)."""

    def __init__(self, config_dir_env_var: str, artifact_path=None) -> None:
        self.capabilities = SimpleNamespace(config_dir_env_var=config_dir_env_var)
        self._artifact_path = artifact_path
        self.config_dir_calls: list[str | None] = []

    def native_thread_artifacts(self, session, config_dir=None):
        self.config_dir_calls.append(config_dir)
        return [self._artifact_path] if self._artifact_path is not None else []


class _FlushRegistry:
    def __init__(self, backend_id: str, agent) -> None:
        self._backend_id = backend_id
        self._agent = agent

    def has_backend(self, backend_id: str) -> bool:
        return backend_id == self._backend_id

    def get(self, backend_id: str):
        assert backend_id == self._backend_id
        return self._agent


def _flush_session(
    backend: str = "claude_code",
    launch_target_id: str | None = None,
    launch_env: dict[str, str] | None = None,
):
    return SimpleNamespace(
        id="sess-flush",
        backend=backend,
        launch_target_id=launch_target_id,
        launch_env=launch_env or {},
        transport_state={"tmux_pane": "%9"},
    )


def _fast_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real defaults (8s timeout / 0.3s poll) would make these tests slow;
    # shrink both so a settled or timed-out loop completes in milliseconds.
    monkeypatch.setattr(
        "waypoint.backends.tmux.transport._FLUSH_SETTLE_TIMEOUT_SECONDS", 0.05
    )
    monkeypatch.setattr(
        "waypoint.backends.tmux.transport._FLUSH_SETTLE_POLL_SECONDS", 0.01
    )


def test_flush_before_restart_settles_on_stable_artifact_mtime(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the wrapped agent already has a native artifact on disk, the
    settle signal is its mtime, not the pane — a pane can keep re-rendering
    (spinners, cursor blink) long after the transcript itself stopped
    changing, so watching the artifact directly is the stronger signal."""
    _fast_settle(monkeypatch)
    artifact = tmp_path / "thread.jsonl"
    artifact.write_text("{}")
    agent = _ArtifactAgent("CLAUDE_CONFIG_DIR", artifact_path=artifact)
    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.get_registry",
        lambda: _FlushRegistry("claude_code", agent),
    )
    adapter = _CaptureOnlyAdapter(lambda n: "SHOULD NOT BE POLLED")
    transport = _transport(adapter)
    session = _flush_session(launch_env={"CLAUDE_CONFIG_DIR": "/profile/dir"})

    asyncio.run(transport.flush_before_restart(session))

    assert adapter.calls == 0  # settled on the artifact, never touched the pane
    assert agent.config_dir_calls == ["/profile/dir"]


def test_flush_before_restart_falls_back_to_pane_idle_without_an_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No native artifact yet (e.g. a not-yet-persisted first turn) falls
    back to the pane's captured content length; a pane that stops changing
    for two consecutive polls is treated as settled."""
    _fast_settle(monkeypatch)
    agent = _ArtifactAgent("CLAUDE_CONFIG_DIR", artifact_path=None)
    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.get_registry",
        lambda: _FlushRegistry("claude_code", agent),
    )
    adapter = _CaptureOnlyAdapter(lambda n: "IDLE PANE")
    transport = _transport(adapter)

    asyncio.run(transport.flush_before_restart(_flush_session()))

    assert adapter.calls >= 2


def test_flush_before_restart_skips_the_artifact_probe_for_a_remote_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote session's artifact lives on the remote host; stat-ing it
    every poll tick would be an SSH round trip. The tmux pane itself is
    always local (even for a remote session it's a local pane running
    ``ssh ... <agent CLI>``), so a remote session always uses the pane-idle
    signal, even when the registry would otherwise resolve an artifact."""
    _fast_settle(monkeypatch)
    agent = _ArtifactAgent("CODEX_HOME", artifact_path=object())
    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.get_registry",
        lambda: _FlushRegistry("codex", agent),
    )
    adapter = _CaptureOnlyAdapter(lambda n: "IDLE PANE")
    transport = _transport(adapter)
    session = _flush_session(backend="codex", launch_target_id="remote-1")

    asyncio.run(transport.flush_before_restart(session))

    assert adapter.calls >= 2
    assert agent.config_dir_calls == []  # never asked for the artifact at all


def test_flush_before_restart_times_out_and_proceeds_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pane that never stops changing (or a wrapped agent that never
    settles) must not block or fail the switch — flush is best-effort;
    fail-before-destroy is enforced later by the transcript step, not here."""
    _fast_settle(monkeypatch)
    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.get_registry",
        lambda: _FlushRegistry("claude_code", _ArtifactAgent("CLAUDE_CONFIG_DIR")),
    )
    # Ever-growing snapshot: the settle signal never repeats.
    adapter = _CaptureOnlyAdapter(lambda n: "x" * n)
    transport = _transport(adapter)

    asyncio.run(transport.flush_before_restart(_flush_session()))  # must not raise

    assert adapter.calls >= 2
