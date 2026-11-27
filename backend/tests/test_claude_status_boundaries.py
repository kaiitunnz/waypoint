"""A Claude session's status changes only at a turn boundary or on turn activity.

The fixtures in ``fixtures/claude_status/`` are trimmed records from Claude Code
2.1.332: ``tty_*`` from a TUI transcript, ``stream_*`` from ``-p`` stream-json.
"""

import asyncio
import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from claude_tty_transcripts import FedSource, jsonl, prompt, result, use
from waypoint.backends.claude_code import turn_boundaries
from waypoint.backends.claude_code.adapter import (
    ClaudeCliAdapter,
    ClaudePendingApproval,
    ClaudeSessionState,
)
from waypoint.backends.claude_code.history import convert_transcript_records
from waypoint.backends.claude_code.transport import ClaudeTransport
from waypoint.backends.claude_tty.normalize import NormalizedEvent, TranscriptNormalizer
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.claude_tty.transport import ClaudeTtyTransport
from waypoint.backends.transcripts import TranscriptUnavailableError
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    AccountProbeResult,
    EventKind,
    EventRecord,
    LaunchSettingsUpdateRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures" / "claude_status"
INTERRUPTED = "Interrupted"
DECLINED = "Turn ended — tool use declined"
MARKER = "[Request interrupted by user]"
TOOL_MARKER = "[Request interrupted by user for tool use]"


def records(name: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / f"{name}.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines]


def marker(text: str = MARKER) -> dict[str, Any]:
    return {"type": "user", "message": {"content": [{"type": "text", "text": text}]}}


def human_prompt(text: str, source: str = "typed") -> dict[str, Any]:
    return prompt(text, origin={"kind": "human"}, promptSource=source)


def tool_use(tool_use_id: str, name: str = "Bash") -> dict[str, Any]:
    return use(tool_use_id, name, {}, msg=f"msg-{tool_use_id}")


def rejection(tool_use_id: str, *, before_run: bool = False) -> dict[str, Any]:
    return {
        **result(tool_use_id),
        "toolUseResult": "User rejected tool use",
        **({"toolDenialBeforeRun": True} if before_run else {}),
    }


def notification(key: str = "a1", **extra: Any) -> dict[str, Any]:
    return prompt(
        f"<task-notification><task-id>{key}</task-id>"
        '<status>completed</status><summary>Agent "x" finished</summary>'
        "</task-notification>",
        origin={"kind": "task-notification"},
        **extra,
    )


def peer(key: str = "hi", **extra: Any) -> dict[str, Any]:
    return prompt(
        '<cross-session-message from="uds:/tmp/peer.sock" '
        f'from-name="peer" from-mode="prompting">\n{key}\n</cross-session-message>',
        origin={"kind": "peer"},
        **extra,
    )


def enqueued(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "queue-operation",
        "operation": "enqueue",
        "content": record["message"]["content"],
    }


def through_marker(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``rows`` up to and including the first interrupt marker."""
    cut = next(
        i for i, row in enumerate(rows) if "Request interrupted" in json.dumps(row)
    )
    return rows[: cut + 1]


def feed(
    norm: TranscriptNormalizer, rows: Iterable[dict[str, Any]]
) -> list[NormalizedEvent]:
    return [event for row in rows for event in norm.process_record(row)]


def notes(events: Iterable[NormalizedEvent]) -> list[tuple[str, SessionStatus | None]]:
    return [(e.text, e.status) for e in events if e.kind is EventKind.SYSTEM_NOTE]


# ── claude_tty: interrupt markers ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fixture", "note"),
    [("tty_interrupt_tool", INTERRUPTED), ("tty_declined_dialog", DECLINED)],
)
def test_a_cut_off_or_declined_tool_ends_the_turn_once(fixture: str, note: str) -> None:
    events = feed(TranscriptNormalizer(), records(fixture))

    assert notes(events) == [(note, SessionStatus.IDLE)]
    assert events[-1].status is SessionStatus.IDLE


def test_an_interrupt_followed_by_a_queued_message_starts_a_new_turn() -> None:
    events = feed(TranscriptNormalizer(), records("tty_interrupt_queued"))

    assert [(e.kind, e.status) for e in events] == [
        (EventKind.AGENT_OUTPUT, SessionStatus.RUNNING),
        (EventKind.SYSTEM_NOTE, SessionStatus.IDLE),
        (EventKind.STATUS_UPDATE, SessionStatus.RUNNING),
        (EventKind.AGENT_OUTPUT, SessionStatus.RUNNING),
        (EventKind.SYSTEM_NOTE, SessionStatus.IDLE),
    ]
    assert events[1].text == INTERRUPTED
    assert events[1].metadata["stop_reason"] == "interrupted"


def test_a_typed_prompt_sets_no_status_even_quoting_a_marker() -> None:
    # An Esc before any output records nothing more, so a prompt typed in the
    # pane must not mark the session running by itself.
    assert feed(TranscriptNormalizer(), [human_prompt(MARKER)]) == []


def test_parallel_rejections_end_the_turn_with_one_note() -> None:
    events = feed(
        TranscriptNormalizer(),
        [
            tool_use("a"),
            tool_use("b"),
            rejection("a"),
            rejection("b"),
            marker(TOOL_MARKER),
        ],
    )

    assert notes(events) == [(INTERRUPTED, SessionStatus.IDLE)]


def test_a_dismissed_question_adds_no_note() -> None:
    events = feed(TranscriptNormalizer(), records("tty_question_dismissed"))

    assert notes(events) == []
    assert events[-1].kind is EventKind.STATUS_UPDATE
    assert events[-1].status is SessionStatus.IDLE


def test_a_sibling_result_after_a_dismissal_cannot_leave_it_running() -> None:
    events = feed(
        TranscriptNormalizer(),
        [
            tool_use("q", "AskUserQuestion"),
            tool_use("b"),
            rejection("q", before_run=True),
            rejection("b", before_run=True),
            marker(TOOL_MARKER),
        ],
    )

    assert notes(events) == []
    assert events[-1].status is SessionStatus.IDLE


def test_a_dismissal_does_not_swallow_a_later_interrupt() -> None:
    norm = TranscriptNormalizer()
    feed(norm, records("tty_question_dismissed"))

    assert notes(feed(norm, [marker()])) == [(INTERRUPTED, SessionStatus.IDLE)]


# ── claude_tty: Waypoint's own interrupt ─────────────────────────────────────


def test_the_marker_of_a_waypoint_interrupt_adds_no_note() -> None:
    rows = records("tty_declined_dialog")
    norm = TranscriptNormalizer()
    feed(norm, rows[:1])
    norm.expected_interrupt.arm()
    events = feed(norm, rows[1:])

    assert notes(events) == []
    assert events[-1].kind is EventKind.STATUS_UPDATE
    assert events[-1].status is SessionStatus.INTERRUPTED


def test_the_stopped_turns_own_prompt_keeps_the_interrupt_expected() -> None:
    # Stop pressed before the tailer read that turn's prompt record.
    norm = TranscriptNormalizer()
    norm.expected_interrupt.arm()
    events = feed(norm, [human_prompt("go"), marker()])

    assert [(e.kind, e.status) for e in events] == [
        (EventKind.STATUS_UPDATE, SessionStatus.INTERRUPTED)
    ]


def test_a_superseded_interrupt_moves_nothing() -> None:
    norm = TranscriptNormalizer()
    norm.expected_interrupt.arm()
    norm.expected_interrupt.supersede()

    assert feed(norm, [marker()]) == []
    assert notes(feed(norm, [marker()])) == [(INTERRUPTED, SessionStatus.IDLE)]


def test_an_interrupt_that_wrote_no_marker_is_forgotten_at_the_next_prompt() -> None:
    norm = TranscriptNormalizer()
    norm.expected_interrupt.arm()
    norm.expected_interrupt.supersede()
    events = feed(norm, [human_prompt("again"), marker()])

    assert notes(events) == [(INTERRUPTED, SessionStatus.IDLE)]


def test_a_message_queued_behind_a_stopped_turn_starts_the_next_one() -> None:
    norm = TranscriptNormalizer()
    rows = records("tty_interrupt_queued")
    feed(norm, rows[:1])
    norm.expected_interrupt.arm()
    events = feed(norm, rows[1:])

    assert [e.status for e in events if e.kind is EventKind.STATUS_UPDATE] == [
        SessionStatus.INTERRUPTED,
        SessionStatus.RUNNING,
    ]


@pytest.mark.parametrize(
    "turn_start",
    [human_prompt("queued", "queued"), notification(), peer()],
    ids=["queued", "notification", "peer"],
)
def test_a_send_after_stop_lets_the_next_record_start_a_turn(
    turn_start: dict[str, Any],
) -> None:
    norm = TranscriptNormalizer()
    norm.expected_interrupt.arm()
    norm.expected_interrupt.supersede()

    assert feed(norm, [turn_start])[-1].status is SessionStatus.RUNNING


@pytest.mark.parametrize(
    ("stopped", "sent_since", "noted", "left"),
    [
        (False, False, [INTERRUPTED], SessionStatus.IDLE),
        (True, False, [], SessionStatus.INTERRUPTED),
        (True, True, [], SessionStatus.RUNNING),
    ],
    ids=["esc", "stop", "stop-then-send"],
)
def test_a_queued_prompt_written_ahead_of_the_marker(
    stopped: bool, sent_since: bool, noted: list[str], left: SessionStatus
) -> None:
    # The CLI's order when an interrupt cuts a running tool with a message queued.
    norm = TranscriptNormalizer()
    feed(norm, [tool_use("a")])
    if stopped:
        norm.expected_interrupt.arm()
    if sent_since:
        norm.expected_interrupt.supersede()
    events = feed(
        norm, [human_prompt("next", "queued"), rejection("a"), marker(TOOL_MARKER)]
    )

    assert [text for text, _ in notes(events)] == noted
    assert events[-1].status is left


# ── claude_tty: records that start no turn ───────────────────────────────────


INJECTED = pytest.mark.parametrize("injected", [notification, peer])


@INJECTED
def test_its_transcript_only_copy_moves_nothing_after_the_enqueue(
    injected: Callable[..., dict[str, Any]],
) -> None:
    norm = TranscriptNormalizer()
    [queued] = feed(norm, [enqueued(injected())])
    assert queued.status is None

    assert feed(norm, [injected(queueTranscriptOnly=True)]) == []


# ── history import matches live ──────────────────────────────────────────────


def _live_statuses(rows: list[dict[str, Any]]) -> dict[tuple[str, str], Any]:
    return {
        (e.kind, str(e.metadata.get("item_id") or e.text)): e.status
        for e in feed(TranscriptNormalizer(), rows)
        if e.kind is not EventKind.STATUS_UPDATE
    }


def _imported(rows: list[dict[str, Any]]) -> list[EventRecord]:
    return convert_transcript_records("s1", rows)


def _last_status(seen: Iterable[SessionStatus | None]) -> SessionStatus | None:
    return [status for status in seen if status is not None][-1]


@pytest.mark.parametrize(
    "fixture",
    [
        "tty_interrupt_tool",
        "tty_declined_dialog",
        "tty_interrupt_queued",
        "tty_question_dismissed",
    ],
)
def test_imported_statuses_match_the_live_ones(fixture: str) -> None:
    rows = records(fixture)
    imported = _imported(rows)

    live = _live_statuses(rows)
    for event in imported:
        key = (event.kind, str(event.metadata.get("item_id") or event.text))
        if key in live:
            assert event.metadata.get("status") == live[key], key
    cut = through_marker(rows)
    assert _last_status(e.metadata.get("status") for e in _imported(cut)) is (
        _last_status(e.status for e in feed(TranscriptNormalizer(), cut))
    )
    assert [
        (e.text, e.metadata.get("status"))
        for e in imported
        if e.kind is EventKind.SYSTEM_NOTE
    ] == [
        (text, status)
        for text, status in notes(feed(TranscriptNormalizer(), rows))
        if text in (INTERRUPTED, DECLINED)
    ]
    assert not [
        e
        for e in imported
        if e.kind is EventKind.USER_INPUT and "Request interrupted" in e.text
    ]


@INJECTED
def test_imported_records_that_started_no_turn_set_no_status(
    injected: Callable[..., dict[str, Any]],
) -> None:
    queued, late, delivered = injected("q"), injected("l"), injected("d")
    imported = _imported(
        [enqueued(queued), {**late, "queueTranscriptOnly": True}, delivered]
    )

    assert [e.metadata.get("status") for e in imported] == [
        None,
        None,
        SessionStatus.RUNNING,
    ]
    assert "status" not in imported[0].metadata


def test_an_imported_decline_after_an_answered_question_is_noted() -> None:
    imported = _imported(
        [
            tool_use("q", "AskUserQuestion"),
            result("q"),
            tool_use("b"),
            rejection("b", before_run=True),
            marker(TOOL_MARKER),
        ]
    )

    last = imported[-1]
    assert (last.text, last.metadata["status"]) == (DECLINED, SessionStatus.IDLE)


def test_a_dismissed_question_imports_its_turn_end() -> None:
    imported = _imported(records("tty_question_dismissed"))

    assert [(e.kind, e.metadata.get("status")) for e in imported] == [
        (EventKind.USER_INPUT, SessionStatus.RUNNING),
        (EventKind.TOOL_CALL, SessionStatus.WAITING_INPUT),
        (EventKind.TOOL_RESULT, SessionStatus.IDLE),
    ]


# ── claude_tty through the runtime ───────────────────────────────────────────


def make_runtime(tmp_path: Path, transport: str) -> SessionRuntime:
    profile = {
        "label": "Work",
        "config_dir": str(tmp_path / "work"),
        "transcript_policy": "require_existing",
        "expected_account_key": "claude_code:work",
    }
    settings = Settings(
        data_dir=tmp_path / "data",
        plugin_configs={"claude_code": {"account_profiles": {"work": profile}}},
    )
    settings.ensure_dirs()
    runtime = SessionRuntime(settings, Storage(settings.database_path))
    session_dir = settings.sessions_dir / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    runtime.storage.create_session(
        SessionRecord(
            id="s1",
            backend="claude_code",
            source=SessionSource.MANAGED,
            transport=transport,
            title="status",
            cwd="/tmp",
            status=SessionStatus.RUNNING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
            transport_state={"tmux_pane": "%0", "thread_id": "thread-1"},
        )
    )
    return runtime


def status_of(runtime: SessionRuntime) -> SessionStatus:
    return runtime.get_session("s1").status


def note_texts(runtime: SessionRuntime) -> list[str]:
    return [
        e.text
        for e in runtime.storage.list_events("s1")
        if e.kind is EventKind.SYSTEM_NOTE
    ]


class _Tty:
    """A claude_tty session whose pane is faked and transcript fed by hand."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.runtime = make_runtime(tmp_path, "claude_tty")
        self.transcript = FedSource()
        plugin = ClaudeTtyPlugin()
        self.tailer = TranscriptTailer("s1", self.transcript, self.runtime, plugin)
        plugin._tailers["s1"] = SimpleNamespace(tailer=self.tailer)  # type: ignore[assignment]
        self.tmux = AsyncMock()
        self.tmux.capture_snapshot.return_value = ""
        self.runtime.tmux = self.tmux
        self.transport = ClaudeTtyTransport(self.runtime, plugin)
        monkeypatch.setattr(
            self.runtime, "transport_for", lambda session: self.transport
        )

    async def read(self, rows: Iterable[dict[str, Any]]) -> None:
        self.transcript.feed(jsonl(*rows))
        await self.tailer._drain()


@pytest.fixture
def tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tty:
    return _Tty(tmp_path, monkeypatch)


async def test_an_esc_in_the_pane_leaves_the_session_idle(tty: _Tty) -> None:
    await tty.read(records("tty_interrupt_tool"))

    assert status_of(tty.runtime) is SessionStatus.IDLE
    assert note_texts(tty.runtime) == [INTERRUPTED]


@pytest.mark.parametrize("marker_first", [True, False])
async def test_stop_gives_one_turn_end_and_one_note(
    tty: _Tty, marker_first: bool
) -> None:
    rows = records("tty_interrupt_tool")
    await tty.read(rows[:3])

    async def esc(*_args: Any) -> None:
        # The marker lands before the runtime has recorded its own note.
        if marker_first:
            await tty.read(rows[3:])

    tty.tmux.send_bytes.side_effect = esc
    await tty.runtime.interrupt("s1")
    if not marker_first:
        await tty.read(rows[3:])

    assert status_of(tty.runtime) is SessionStatus.INTERRUPTED
    assert note_texts(tty.runtime) == ["Sent interrupt"]


async def test_a_send_after_stop_keeps_the_new_turn_running(
    tty: _Tty, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = records("tty_interrupt_tool")
    await tty.read(rows[:3])
    await tty.runtime.interrupt("s1")

    async def send(*_args: Any) -> None:
        # The send outlasts a tailer poll, which reads the old turn's marker.
        tty.runtime.storage.update_session("s1", status=SessionStatus.RUNNING)
        await tty.read([rows[4]])

    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.TmuxTransport.send_input", send
    )
    await tty.transport.send_input(tty.runtime.get_session("s1"), "next")

    assert status_of(tty.runtime) is SessionStatus.RUNNING
    assert note_texts(tty.runtime) == ["Sent interrupt"]


async def test_a_late_notification_leaves_an_idle_session_idle(tty: _Tty) -> None:
    done = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "done"}],
        },
    }
    await tty.read([enqueued(notification()), done])
    assert status_of(tty.runtime) is SessionStatus.IDLE

    await tty.read([notification(queueTranscriptOnly=True)])

    assert status_of(tty.runtime) is SessionStatus.IDLE


# ── a restart that aborts before teardown ────────────────────────────────────


async def abort_profile_switch(
    tty: _Tty, monkeypatch: pytest.MonkeyPatch, read_during_flush: list[dict[str, Any]]
) -> None:
    """A profile switch that interrupts the turn and then fails its transcript
    step, with the tailer reading ``read_during_flush`` in between.
    """

    async def flush(_transport: Any, _session: SessionRecord) -> None:
        await tty.read(read_during_flush)

    async def probe(*_args: Any, **_kwargs: Any) -> AccountProbeResult:
        return AccountProbeResult(account_key="claude_code:work", account_label="w")

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise TranscriptUnavailableError("not there")

    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.TmuxTransport.flush_before_restart", flush
    )
    monkeypatch.setattr("waypoint.runtime.probe_account", probe)
    monkeypatch.setattr("waypoint.runtime.ensure_thread_available", unavailable)
    monkeypatch.setattr(
        tty.runtime, "_ensure_profile_config_dir_ready", lambda *_args: None
    )

    with pytest.raises(HTTPException, match="cannot switch account profile"):
        await tty.runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(account_profile_id="work", restart=True)
        )


async def test_a_cut_turns_marker_cannot_leave_an_aborted_switch_running(
    tty: _Tty, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = records("tty_interrupt_tool")
    await tty.read(rows[:2])

    await abort_profile_switch(tty, monkeypatch, rows[2:])

    assert status_of(tty.runtime) is SessionStatus.INTERRUPTED


@pytest.mark.parametrize(
    "turn_start",
    [human_prompt("queued", "queued"), notification(), peer()],
    ids=["queued", "notification", "peer"],
)
async def test_a_turn_cut_before_any_output_is_not_left_running(
    tty: _Tty, monkeypatch: pytest.MonkeyPatch, turn_start: dict[str, Any]
) -> None:
    # An interrupt that early writes no marker, so a turn start the tailer reads
    # only now has nothing after it to end it.
    await abort_profile_switch(tty, monkeypatch, [turn_start])

    assert status_of(tty.runtime) is SessionStatus.INTERRUPTED


# ── structured claude_code ───────────────────────────────────────────────────


class _Structured:
    """A structured session fed stream-json records by hand."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.runtime = make_runtime(tmp_path, "claude_cli")
        self.adapter = ClaudeCliAdapter(
            self.runtime._emit_adapter_event,
            on_session_update=self.runtime.session_update_callback(),
        )
        stdin = MagicMock(drain=AsyncMock())
        stdin.is_closing.return_value = False
        process = SimpleNamespace(stdin=stdin, returncode=None)
        self.state = ClaudeSessionState(
            session_id="s1",
            cwd="/tmp",
            process=process,  # type: ignore[arg-type]
            claude_session_id="thread-1",
            stdout_task=asyncio.create_task(asyncio.sleep(0)),
            stderr_task=asyncio.create_task(asyncio.sleep(0)),
            wait_task=asyncio.create_task(asyncio.sleep(0)),
        )
        self.adapter._sessions["s1"] = self.state
        plugin = SimpleNamespace(adapter=self.adapter)
        self.transport = ClaudeTransport(self.runtime, plugin)  # type: ignore[arg-type]
        monkeypatch.setattr(
            self.runtime, "transport_for", lambda session: self.transport
        )
        # The faked binary never answers a control request.
        self.adapter._send_control_request = AsyncMock()  # type: ignore[method-assign]

    async def read(self, rows: Iterable[dict[str, Any]]) -> list[SessionStatus]:
        """Dispatch ``rows``, returning the session's status after each."""
        seen: list[SessionStatus] = []
        for row in rows:
            await self.adapter._dispatch(self.state, row)
            seen.append(status_of(self.runtime))
        return seen

    def set_status(self, status: SessionStatus) -> None:
        self.runtime.storage.update_session("s1", status=status)

    async def stop(self, answered_by: Iterable[dict[str, Any]] = ()) -> None:
        """Waypoint's Stop; ``answered_by`` is read before the runtime notes it."""

        async def interrupt_request(*_args: Any) -> None:
            await self.read(answered_by)

        self.adapter._send_control_request.side_effect = interrupt_request  # type: ignore[attr-defined]
        await self.runtime.interrupt("s1")

    def turn_ends(self) -> list[str]:
        ended = {"idle", "interrupted", "error", "exited"}
        return [
            e.text
            for e in self.runtime.storage.list_events("s1")
            if e.metadata.get("status") in ended
        ]


@pytest.fixture
async def structured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Structured:
    return _Structured(tmp_path, monkeypatch)


def system(subtype: str, **extra: Any) -> dict[str, Any]:
    return {"type": "system", "subtype": subtype, **extra}


ABORTED = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "terminal_reason": "aborted_streaming",
}
SUCCEEDED = {"type": "result", "subtype": "success", "is_error": False}


@pytest.mark.parametrize(
    ("status", "record"),
    [
        (SessionStatus.IDLE, system("commands_changed")),
        (SessionStatus.RUNNING, system("commands_changed")),
        (SessionStatus.IDLE, system("brand_new_subtype")),
        (SessionStatus.RUNNING, system("brand_new_subtype")),
        (SessionStatus.IDLE, system("status", status=None, compact_result="failed")),
        (SessionStatus.IDLE, {"type": "rate_limit_event"}),
        (SessionStatus.IDLE, {"type": "something_new"}),
        (SessionStatus.RUNNING, {"type": "something_new"}),
    ],
)
async def test_a_record_that_marks_no_boundary_keeps_the_status(
    structured: _Structured, status: SessionStatus, record: dict[str, Any]
) -> None:
    structured.set_status(status)

    assert await structured.read([record]) == [status]
    assert len(note_texts(structured.runtime)) == 1


async def test_a_status_record_with_nothing_to_report_adds_no_note(
    structured: _Structured,
) -> None:
    await structured.read([system("status", status=None)])

    assert note_texts(structured.runtime) == []


async def test_commands_changed_after_a_turn_keeps_it_idle(
    structured: _Structured,
) -> None:
    seen = await structured.read(records("stream_commands_changed"))

    assert seen[-2:] == [SessionStatus.IDLE, SessionStatus.IDLE]


async def test_a_queued_compact_reads_running_until_its_boundary(
    structured: _Structured,
) -> None:
    # The turn it was queued behind ends before the compaction begins.
    rows = [SUCCEEDED, *records("stream_manual_compact")]

    seen = await structured.read(rows)

    boundary = next(
        i for i, row in enumerate(rows) if row.get("subtype") == "compact_boundary"
    )
    assert seen[0] is SessionStatus.IDLE
    assert set(seen[1:boundary]) == {SessionStatus.RUNNING}
    assert set(seen[boundary:]) == {SessionStatus.IDLE}


async def test_auto_compaction_stays_running_until_the_result(
    structured: _Structured,
) -> None:
    seen = await structured.read(records("stream_auto_compact"))

    assert set(seen[:-1]) == {SessionStatus.RUNNING}
    assert seen[-1] is SessionStatus.IDLE


async def test_a_background_subagent_does_not_wake_an_idle_session(
    structured: _Structured,
) -> None:
    rows = records("stream_background_agent")

    seen = await structured.read(rows)

    first_result = next(i for i, row in enumerate(rows) if row["type"] == "result")
    follow_up = next(
        i
        for i, row in enumerate(rows)
        if i > first_result and row.get("subtype") == "init"
    )
    assert set(seen[first_result:follow_up]) == {SessionStatus.IDLE}
    assert seen[follow_up] is SessionStatus.RUNNING
    assert seen[-1] is SessionStatus.IDLE


async def test_a_subagent_edit_and_todo_keep_an_idle_session_idle(
    structured: _Structured,
) -> None:
    structured.set_status(SessionStatus.IDLE)
    parent = {"parent_tool_use_id": "agent-1"}

    def subagent_call(tool_use_id: str, name: str, tool_input: dict) -> dict[str, Any]:
        return {**use(tool_use_id, name, tool_input), **parent}

    seen = await structured.read(
        [
            subagent_call("t1", "TaskCreate", {"subject": "step"}),
            {**result("t1", "Task #1 created successfully: step"), **parent},
            subagent_call("t2", "TaskUpdate", {"taskId": "1", "status": "completed"}),
        ]
    )
    await structured.adapter._emit_tool_diff_preview(
        structured.state,
        {"tool_use_id": "e1", "tool_name": "Edit", "diff_preview": {"files": []}},
    )

    assert set(seen) == {SessionStatus.IDLE}
    assert status_of(structured.runtime) is SessionStatus.IDLE
    assert len(structured.runtime.storage.list_events("s1")) == 3


# ── structured: Stop ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fixture", "result_first"),
    [("stream_interrupt_tool", True), ("stream_interrupt_streaming", False)],
)
async def test_a_stopped_turn_ends_interrupted_once(
    structured: _Structured, fixture: str, result_first: bool
) -> None:
    rows = records(fixture)

    await structured.stop(answered_by=rows if result_first else ())
    if not result_first:
        await structured.read(rows)

    assert status_of(structured.runtime) is SessionStatus.INTERRUPTED
    assert structured.turn_ends() == ["Sent interrupt"]
    assert INTERRUPTED not in note_texts(structured.runtime)


async def test_a_result_read_after_the_next_send_moves_nothing(
    structured: _Structured,
) -> None:
    await structured.stop()
    await structured.adapter.send_input("s1", "next")
    structured.set_status(SessionStatus.RUNNING)

    await structured.read([ABORTED])

    assert status_of(structured.runtime) is SessionStatus.RUNNING
    assert note_texts(structured.runtime) == ["Sent interrupt"]


async def test_a_second_stop_answers_for_the_turn_sent_after_the_first(
    structured: _Structured,
) -> None:
    await structured.stop()
    await structured.adapter.send_input("s1", "next")
    await structured.stop()
    structured.set_status(SessionStatus.RUNNING)

    await structured.read([ABORTED])

    assert status_of(structured.runtime) is SessionStatus.INTERRUPTED
    assert INTERRUPTED not in note_texts(structured.runtime)


async def test_a_finished_turns_result_leaves_the_stop_pending(
    structured: _Structured,
) -> None:
    # The CLI applies the interrupt to the queued turn behind it.
    await structured.stop()
    await structured.read([SUCCEEDED])

    await structured.read([ABORTED])

    assert status_of(structured.runtime) is SessionStatus.INTERRUPTED
    assert INTERRUPTED not in note_texts(structured.runtime)


async def test_an_abort_waypoint_did_not_ask_for_is_noted(
    structured: _Structured,
) -> None:
    await structured.read([ABORTED])

    assert status_of(structured.runtime) is SessionStatus.IDLE
    assert note_texts(structured.runtime) == [INTERRUPTED]


async def test_the_binarys_own_abort_is_noted_after_a_stale_stop(
    structured: _Structured,
) -> None:
    await structured.stop()
    await structured.adapter.send_input("s1", "next")
    structured.set_status(SessionStatus.RUNNING)

    await structured.read([{**ABORTED, "stopped_by_permission_check": True}])

    assert status_of(structured.runtime) is SessionStatus.IDLE
    assert note_texts(structured.runtime) == ["Sent interrupt", INTERRUPTED]


async def test_a_stop_that_cut_nothing_short_lapses(
    structured: _Structured, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [100.0]
    monkeypatch.setattr(turn_boundaries.time, "monotonic", lambda: clock[0])
    await structured.stop()
    clock[0] += 31.0
    await structured.adapter.send_input("s1", "next")
    structured.set_status(SessionStatus.RUNNING)

    await structured.read([ABORTED])

    assert status_of(structured.runtime) is SessionStatus.IDLE
    assert note_texts(structured.runtime) == ["Sent interrupt", INTERRUPTED]


@pytest.mark.parametrize(
    "error",
    [
        {"subtype": "success", "is_error": True, "terminal_reason": "api_error"},
        {"subtype": "error_during_execution", "is_error": True},
    ],
)
async def test_a_genuine_error_stays_an_error_after_a_stop(
    structured: _Structured, error: dict[str, Any]
) -> None:
    await structured.stop()

    await structured.read([{"type": "result", **error}])

    assert status_of(structured.runtime) is SessionStatus.ERROR


def _park(structured: _Structured, tool_name: str) -> None:
    structured.state.pending["tool-1"] = ClaudePendingApproval(
        tool_use_id="tool-1", payload={"tool_name": tool_name}, request_id="req-1"
    )


WITHDRAWN = {"type": "control_cancel_request", "request_id": "req-1"}


@pytest.mark.parametrize(
    ("stopped", "note"),
    [
        (True, "Pending approval cleared by interrupt"),
        (False, "Pending approval withdrawn by Claude"),
    ],
)
async def test_a_withdrawn_approval_is_cleared(
    structured: _Structured, stopped: bool, note: str
) -> None:
    _park(structured, "Bash")
    if stopped:
        await structured.stop()

    await structured.read([WITHDRAWN])

    assert not structured.adapter.has_pending_approval("s1")
    event = structured.runtime.storage.list_events("s1")[-1]
    assert event.text == note
    assert event.metadata["method"] == "approval.invalidated"
    assert event.metadata["approval_id"] == "tool-1"
    assert "status" not in event.metadata


async def test_a_withdrawn_question_records_nothing(structured: _Structured) -> None:
    _park(structured, "AskUserQuestion")

    await structured.read([WITHDRAWN])

    assert not structured.state.pending
    assert note_texts(structured.runtime) == []
