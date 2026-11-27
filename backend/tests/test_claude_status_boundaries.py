"""A Claude session's status changes only at a turn boundary.

The fixtures in ``fixtures/claude_status/`` are trimmed records from Claude Code
2.1.332: ``tty_*`` from a TUI transcript, ``stream_*`` from ``-p`` stream-json.
"""

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_tty_transcripts import NoTranscriptTree
from waypoint.backends.claude_code.adapter import (
    ClaudeCliAdapter,
    ClaudePendingApproval,
    ClaudeSessionState,
)
from waypoint.backends.claude_code.history import convert_transcript_records
from waypoint.backends.claude_tty import normalize as tty_normalize
from waypoint.backends.claude_tty.byte_source import TranscriptRead
from waypoint.backends.claude_tty.normalize import NormalizedEvent, TranscriptNormalizer
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.backends.claude_tty.tailer import TranscriptTailer
from waypoint.backends.claude_tty.transport import ClaudeTtyTransport
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    EventRecord,
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
    return {
        "type": "user",
        "message": {"content": [{"type": "text", "text": text}]},
    }


def human_prompt(text: str, source: str = "typed") -> dict[str, Any]:
    return {
        "type": "user",
        "origin": {"kind": "human"},
        "promptSource": source,
        "message": {"content": text},
    }


def tool_use(tool_use_id: str, name: str = "Bash") -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": f"msg-{tool_use_id}",
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}
            ],
        },
    }


def tool_result(
    tool_use_id: str, *, rejected: bool = False, before_run: bool = False
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": "x"}
            ]
        },
    }
    if rejected:
        record["toolUseResult"] = "User rejected tool use"
    if before_run:
        record["toolDenialBeforeRun"] = True
    return record


def notification(task_id: str = "a1", **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "origin": {"kind": "task-notification"},
        "message": {
            "content": f"<task-notification><task-id>{task_id}</task-id>"
            '<status>completed</status><summary>Agent "x" finished</summary>'
            "</task-notification>"
        },
        **extra,
    }


def enqueued(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "queue-operation",
        "operation": "enqueue",
        "content": record["message"]["content"],
    }


def feed(
    norm: TranscriptNormalizer, rows: Iterable[dict[str, Any]]
) -> list[NormalizedEvent]:
    return [event for row in rows for event in norm.process_record(row)]


def notes(events: Iterable[NormalizedEvent]) -> list[tuple[str, SessionStatus | None]]:
    return [(e.text, e.status) for e in events if e.kind is EventKind.SYSTEM_NOTE]


def statuses(events: Iterable[NormalizedEvent]) -> list[SessionStatus | None]:
    return [e.status for e in events]


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


def test_a_typed_prompt_leaves_the_status_to_the_input_boundary() -> None:
    # An Esc before any output records nothing more, so a prompt typed in the
    # pane must not mark the session running by itself.
    assert feed(TranscriptNormalizer(), [human_prompt("hello")]) == []


def test_a_typed_prompt_quoting_a_marker_is_no_interrupt() -> None:
    assert feed(TranscriptNormalizer(), [human_prompt(MARKER)]) == []


def test_parallel_rejections_end_the_turn_with_one_note() -> None:
    events = feed(
        TranscriptNormalizer(),
        [
            tool_use("a"),
            tool_use("b"),
            tool_result("a", rejected=True),
            tool_result("b", rejected=True),
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
            tool_result("q", rejected=True, before_run=True),
            tool_result("b", rejected=True, before_run=True),
            marker(TOOL_MARKER),
        ],
    )

    assert notes(events) == []
    assert events[-1].status is SessionStatus.IDLE


def test_a_dismissal_does_not_swallow_a_later_interrupt() -> None:
    norm = TranscriptNormalizer()
    feed(norm, records("tty_question_dismissed"))
    later = feed(norm, [notification(), marker()])

    assert notes(later)[-1] == (INTERRUPTED, SessionStatus.IDLE)


# ── claude_tty: Waypoint's own interrupt ─────────────────────────────────────


@pytest.mark.parametrize(
    "fixture", ["tty_interrupt_tool", "tty_declined_dialog", "tty_interrupt_queued"]
)
def test_the_marker_of_a_waypoint_interrupt_adds_no_note(fixture: str) -> None:
    rows = records(fixture)
    cut = next(
        i for i, row in enumerate(rows) if "Request interrupted" in json.dumps(row)
    )
    norm = TranscriptNormalizer()
    feed(norm, rows[:1])
    norm.expect_interrupt()
    events = feed(norm, rows[1 : cut + 1])

    assert notes(events) == []
    assert events[-1].kind is EventKind.STATUS_UPDATE
    assert events[-1].status is SessionStatus.INTERRUPTED


def test_the_stopped_turns_own_prompt_keeps_the_interrupt_expected() -> None:
    # Stop pressed before the tailer read that turn's prompt record.
    norm = TranscriptNormalizer()
    norm.expect_interrupt()
    events = feed(norm, [human_prompt("go"), marker()])

    assert notes(events) == []
    assert statuses(events) == [SessionStatus.INTERRUPTED]


def test_a_superseded_interrupt_moves_nothing() -> None:
    norm = TranscriptNormalizer()
    norm.expect_interrupt()
    norm.supersede_interrupt()

    assert feed(norm, [marker()]) == []
    assert notes(feed(norm, [marker()])) == [(INTERRUPTED, SessionStatus.IDLE)]


def test_an_interrupt_that_wrote_no_marker_is_forgotten_at_the_next_prompt() -> None:
    norm = TranscriptNormalizer()
    norm.expect_interrupt()
    norm.supersede_interrupt()
    events = feed(norm, [human_prompt("again"), marker()])

    assert notes(events) == [(INTERRUPTED, SessionStatus.IDLE)]


def test_a_second_interrupt_clears_the_superseded_state() -> None:
    norm = TranscriptNormalizer()
    norm.expect_interrupt()
    norm.supersede_interrupt()
    norm.expect_interrupt()

    assert statuses(feed(norm, [marker()])) == [SessionStatus.INTERRUPTED]


def test_input_with_no_interrupt_pending_supersedes_nothing() -> None:
    norm = TranscriptNormalizer()
    norm.supersede_interrupt()

    assert notes(feed(norm, [marker()])) == [(INTERRUPTED, SessionStatus.IDLE)]


def test_an_expected_interrupt_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(tty_normalize.time, "monotonic", lambda: clock[0])
    norm = TranscriptNormalizer()
    norm.expect_interrupt()
    clock[0] += 31.0

    assert notes(feed(norm, [marker()])) == [(INTERRUPTED, SessionStatus.IDLE)]


# ── claude_tty: records that start no turn ───────────────────────────────────


def test_a_transcript_only_notification_sets_no_status() -> None:
    late = notification(queueTranscriptOnly=True, promptSource="system")

    [note] = feed(TranscriptNormalizer(), [late])
    assert note.kind is EventKind.SYSTEM_NOTE
    assert note.status is None


def test_its_transcript_only_copy_moves_nothing_after_the_enqueue() -> None:
    norm = TranscriptNormalizer()
    [queued] = feed(norm, [enqueued(notification())])
    assert queued.status is None

    assert feed(norm, [notification(queueTranscriptOnly=True)]) == []


def test_a_delivered_notification_starts_a_turn() -> None:
    norm = TranscriptNormalizer()
    feed(norm, [enqueued(notification())])

    assert statuses(feed(norm, [notification()])) == [SessionStatus.RUNNING]


def test_a_manual_compact_ends_idle_and_an_auto_one_is_ignored() -> None:
    manual = feed(TranscriptNormalizer(), records("tty_manual_compact"))
    assert [(e.kind, e.status) for e in manual] == [
        (EventKind.SYSTEM_NOTE, SessionStatus.IDLE)
    ]

    boundary = records("tty_manual_compact")[0]
    auto = {**boundary, "compactMetadata": {"trigger": "auto"}}
    assert feed(TranscriptNormalizer(), [auto]) == []


# ── history import matches live ──────────────────────────────────────────────


def _live_statuses(rows: list[dict[str, Any]]) -> dict[tuple[str, str], Any]:
    return {
        (e.kind, str(e.metadata.get("item_id") or e.text)): e.status
        for e in feed(TranscriptNormalizer(), rows)
        if e.kind is not EventKind.STATUS_UPDATE
    }


def _imported(rows: list[dict[str, Any]]) -> list[EventRecord]:
    return convert_transcript_records("s1", rows)


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
    live = _live_statuses(rows)
    imported = _imported(rows)

    for event in imported:
        key = (event.kind, str(event.metadata.get("item_id") or event.text))
        if key in live:
            assert event.metadata.get("status") == live[key], key
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


def test_imported_notifications_that_started_no_turn_set_no_status() -> None:
    queued, late, delivered = notification("q"), notification("l"), notification("d")
    imported = _imported(
        [enqueued(queued), {**late, "queueTranscriptOnly": True}, delivered]
    )

    assert [e.metadata.get("status") for e in imported] == [
        None,
        None,
        SessionStatus.RUNNING,
    ]
    assert "status" not in imported[0].metadata


def test_a_dismissed_question_imports_its_turn_end() -> None:
    imported = _imported(records("tty_question_dismissed"))

    assert [(e.kind, e.metadata.get("status")) for e in imported] == [
        (EventKind.USER_INPUT, SessionStatus.RUNNING),
        (EventKind.TOOL_CALL, SessionStatus.WAITING_INPUT),
        (EventKind.TOOL_RESULT, SessionStatus.IDLE),
    ]


# ── claude_tty through the runtime ───────────────────────────────────────────


def make_runtime(tmp_path: Path, transport: str) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
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


class _Transcript(NoTranscriptTree):
    def __init__(self) -> None:
        self._pending = b""
        self._size = 0

    def append(self, rows: Iterable[dict[str, Any]]) -> None:
        self._pending += b"".join(json.dumps(row).encode() + b"\n" for row in rows)

    def read_from(
        self, offset: int, *, metadata_only: bool = False, force: bool = False
    ) -> TranscriptRead:
        data, self._pending = self._pending, b""
        self._size += len(data)
        return TranscriptRead(
            observed=True, data=data, size=self._size, identity=(1, 1)
        )


class _Tty:
    """A claude_tty session whose pane is faked and transcript fed by hand."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.runtime = make_runtime(tmp_path, "claude_tty")
        self.transcript = _Transcript()
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
        self.transcript.append(rows)
        await self.tailer._drain()


@pytest.fixture
def tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tty:
    return _Tty(tmp_path, monkeypatch)


@pytest.mark.parametrize("fixture", ["tty_interrupt_tool", "tty_interrupt_queued"])
async def test_an_esc_in_the_pane_leaves_the_session_idle(
    tty: _Tty, fixture: str
) -> None:
    rows = records(fixture)
    cut = next(
        i for i, row in enumerate(rows) if "Request interrupted" in json.dumps(row)
    )
    await tty.read(rows[: cut + 1])

    assert status_of(tty.runtime) is SessionStatus.IDLE
    assert note_texts(tty.runtime) == [INTERRUPTED]


@pytest.mark.parametrize("marker_first", [True, False])
async def test_stop_gives_one_turn_end_and_one_note(
    tty: _Tty, marker_first: bool, monkeypatch: pytest.MonkeyPatch
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
    send = AsyncMock()
    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.TmuxTransport.send_input", send
    )
    session = tty.runtime.get_session("s1")
    await tty.transport.send_input(session, "next")
    tty.runtime.storage.update_session("s1", status=SessionStatus.RUNNING)

    # The old turn's marker, read only now.
    await tty.read([rows[4]])

    send.assert_awaited_once()
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


# ── structured claude_code ───────────────────────────────────────────────────


class _Pipe:
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return False


class _Structured:
    """A structured session fed stream-json records by hand."""

    def __init__(self, tmp_path: Path) -> None:
        self.runtime = make_runtime(tmp_path, "claude_cli")
        self.adapter = ClaudeCliAdapter(
            self.runtime._emit_adapter_event,
            on_session_update=self.runtime.session_update_callback(),
        )
        process = SimpleNamespace(stdin=_Pipe(), returncode=None)
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

    async def read(self, rows: Iterable[dict[str, Any]]) -> list[SessionStatus]:
        """Dispatch ``rows``, returning the session's status after each."""
        seen: list[SessionStatus] = []
        for row in rows:
            await self.adapter._dispatch(self.state, row)
            seen.append(status_of(self.runtime))
        return seen

    def set_status(self, status: SessionStatus) -> None:
        self.runtime.storage.update_session("s1", status=status)

    async def stop(self) -> None:
        # What runtime.interrupt does around the transport, without the control
        # response the faked binary never sends.
        self.adapter._send_control_request = AsyncMock()  # type: ignore[method-assign]
        await self.adapter.interrupt("s1")

    async def note_stop(self) -> None:
        await self.runtime._record_system_event(
            "s1", "Sent interrupt", status=SessionStatus.INTERRUPTED
        )
        self.set_status(SessionStatus.INTERRUPTED)


def system(subtype: str, **extra: Any) -> dict[str, Any]:
    return {"type": "system", "subtype": subtype, **extra}


@pytest.mark.parametrize("status", [SessionStatus.IDLE, SessionStatus.RUNNING])
@pytest.mark.parametrize(
    "record",
    [
        system("commands_changed"),
        system("brand_new_subtype"),
        system("thinking_tokens"),
        system("task_started"),
        system("task_notification", status="completed"),
        system("status", status="compacting"),
        system("status", status=None, compact_result="success"),
        system("status", status=None, compact_result="failed"),
        system("compact_boundary", compact_metadata={"trigger": "auto"}),
        {"type": "rate_limit_event"},
        {"type": "something_new"},
    ],
)
async def test_a_record_that_marks_no_boundary_keeps_the_status(
    tmp_path: Path, status: SessionStatus, record: dict[str, Any]
) -> None:
    session = _Structured(tmp_path)
    session.set_status(status)

    assert await session.read([record]) == [status]
    assert len(note_texts(session.runtime)) == 1


async def test_commands_changed_after_a_turn_keeps_it_idle(tmp_path: Path) -> None:
    session = _Structured(tmp_path)

    seen = await session.read(records("stream_commands_changed"))

    assert seen[-2:] == [SessionStatus.IDLE, SessionStatus.IDLE]


async def test_a_manual_compact_ends_idle(tmp_path: Path) -> None:
    session = _Structured(tmp_path)
    rows = records("stream_manual_compact")

    seen = await session.read(rows)

    boundary = next(
        i for i, row in enumerate(rows) if row.get("subtype") == "compact_boundary"
    )
    assert SessionStatus.IDLE not in seen[:boundary]
    assert seen[boundary] is SessionStatus.IDLE
    assert seen[-1] is SessionStatus.IDLE


async def test_auto_compaction_stays_running_until_the_result(
    tmp_path: Path,
) -> None:
    session = _Structured(tmp_path)

    seen = await session.read(records("stream_auto_compact"))

    assert set(seen[:-1]) == {SessionStatus.RUNNING}
    assert seen[-1] is SessionStatus.IDLE


async def test_a_background_subagent_does_not_wake_an_idle_session(
    tmp_path: Path,
) -> None:
    session = _Structured(tmp_path)
    rows = records("stream_background_agent")

    seen = await session.read(rows)

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
    tmp_path: Path,
) -> None:
    session = _Structured(tmp_path)
    session.set_status(SessionStatus.IDLE)
    parent = {"parent_tool_use_id": "agent-1"}

    def assistant(tool_use_id: str, name: str, tool_input: dict) -> dict[str, Any]:
        block = {"type": "tool_use", "id": tool_use_id, "name": name}
        return {
            "type": "assistant",
            "message": {"id": "m1", "content": [{**block, "input": tool_input}]},
            **parent,
        }

    def result(tool_use_id: str, content: str) -> dict[str, Any]:
        block = {"type": "tool_result", "tool_use_id": tool_use_id}
        return {
            "type": "user",
            "message": {"content": [{**block, "content": content}]},
            **parent,
        }

    seen = await session.read(
        [
            assistant("t1", "TaskCreate", {"subject": "step"}),
            result("t1", "Task #1 created successfully: step"),
            assistant("t2", "TaskUpdate", {"taskId": "1", "status": "completed"}),
        ]
    )
    await session.adapter._emit_tool_diff_preview(
        session.state,
        {"tool_use_id": "e1", "tool_name": "Edit", "diff_preview": {"files": []}},
    )

    assert set(seen) == {SessionStatus.IDLE}
    assert status_of(session.runtime) is SessionStatus.IDLE
    assert len(session.runtime.storage.list_events("s1")) == 3


# ── structured: Stop ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture", ["stream_interrupt_streaming", "stream_interrupt_tool"]
)
@pytest.mark.parametrize("result_first", [True, False])
async def test_a_stopped_turn_ends_interrupted_with_one_note(
    tmp_path: Path, fixture: str, result_first: bool
) -> None:
    session = _Structured(tmp_path)
    await session.stop()

    if not result_first:
        await session.note_stop()
    await session.read(records(fixture))
    if result_first:
        await session.note_stop()

    assert status_of(session.runtime) is SessionStatus.INTERRUPTED
    assert [
        text
        for text in note_texts(session.runtime)
        if not text.startswith(("system/", "Claude"))
    ] == ["Sent interrupt"]


async def test_a_result_read_after_the_next_send_moves_nothing(
    tmp_path: Path,
) -> None:
    session = _Structured(tmp_path)
    await session.stop()
    await session.adapter.send_input("s1", "next")
    session.set_status(SessionStatus.RUNNING)

    await session.read([records("stream_interrupt_streaming")[-1]])

    assert status_of(session.runtime) is SessionStatus.RUNNING
    assert note_texts(session.runtime) == []
    assert not session.state.stop_requested


async def test_a_finished_turns_result_leaves_the_stop_pending(
    tmp_path: Path,
) -> None:
    # The CLI applies the interrupt to the queued turn behind it.
    session = _Structured(tmp_path)
    await session.stop()
    await session.read([{"type": "result", "subtype": "success", "is_error": False}])

    await session.read([records("stream_interrupt_streaming")[-1]])

    assert status_of(session.runtime) is SessionStatus.INTERRUPTED
    assert INTERRUPTED not in note_texts(session.runtime)


async def test_an_abort_waypoint_did_not_ask_for_is_noted(tmp_path: Path) -> None:
    session = _Structured(tmp_path)

    await session.read([records("stream_interrupt_streaming")[-1]])

    assert status_of(session.runtime) is SessionStatus.IDLE
    assert note_texts(session.runtime) == [INTERRUPTED]


@pytest.mark.parametrize(
    "result",
    [
        {"subtype": "success", "is_error": True, "terminal_reason": "api_error"},
        {
            "subtype": "error_during_execution",
            "is_error": True,
            "terminal_reason": "model_error",
        },
        {"subtype": "error_max_turns", "is_error": True},
    ],
)
async def test_a_genuine_error_stays_an_error_after_a_stop(
    tmp_path: Path, result: dict[str, Any]
) -> None:
    session = _Structured(tmp_path)
    await session.stop()

    await session.read([{"type": "result", **result}])

    assert status_of(session.runtime) is SessionStatus.ERROR


async def test_a_stop_is_recognized_without_a_terminal_reason(
    tmp_path: Path,
) -> None:
    session = _Structured(tmp_path)
    await session.stop()

    await session.read(
        [{"type": "result", "subtype": "error_during_execution", "is_error": True}]
    )

    assert status_of(session.runtime) is SessionStatus.INTERRUPTED


async def test_a_stop_clears_the_approval_the_binary_withdraws(
    tmp_path: Path,
) -> None:
    session = _Structured(tmp_path)
    session.state.pending["tool-1"] = ClaudePendingApproval(
        tool_use_id="tool-1", payload={"tool_name": "Bash"}, request_id="req-1"
    )
    await session.stop()

    await session.read([{"type": "control_cancel_request", "request_id": "req-1"}])

    assert not session.adapter.has_pending_approval("s1")
    [event] = session.runtime.storage.list_events("s1")
    assert event.text == "Pending approval cleared by interrupt"
    assert event.metadata["method"] == "approval.invalidated"
    assert event.metadata["approval_id"] == "tool-1"
    assert "status" not in event.metadata


# ── claude_tty transport ─────────────────────────────────────────────────────


async def test_stop_expects_the_marker_before_pressing_esc() -> None:
    calls: list[str] = []
    plugin = MagicMock()
    plugin.drop_pending_approval = AsyncMock()
    plugin.expect_interrupt.side_effect = lambda _id: calls.append("expect")
    runtime = MagicMock()
    runtime.tmux = AsyncMock()
    runtime.tmux.capture_snapshot.return_value = ""
    runtime.tmux.send_bytes.side_effect = lambda *_a: calls.append("esc")
    transport = ClaudeTtyTransport(runtime, plugin)
    session = MagicMock()
    session.transport_state = {"tmux_pane": "%0"}

    await transport.interrupt(session)

    assert calls[:2] == ["expect", "esc"]


async def test_a_send_supersedes_the_interrupt_before_typing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    plugin = MagicMock()
    plugin.supersede_interrupt.side_effect = lambda _id: calls.append("supersede")

    async def send(*_args: Any) -> None:
        calls.append("send")

    monkeypatch.setattr(
        "waypoint.backends.tmux.transport.TmuxTransport.send_input", send
    )

    await ClaudeTtyTransport(MagicMock(), plugin).send_input(MagicMock(), "hi")

    assert calls == ["supersede", "send"]
