"""Live resume: the status after its Enter, and refusal while a dialog is open."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from waypoint.backends.tmux.transport import TmuxTransport
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    AWAITING_ANSWER_KEY,
    EventKind,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.transports import InputBlockedError

PANES = Path(__file__).parent / "fixtures" / "claude_tty_pane"
EMPTY_COMPOSER = (PANES / "ready.txt").read_text()
APPROVAL_DIALOG = (PANES / "approval_bash.txt").read_text()
UNKNOWN_POPUP = "❯ earlier prompt\n\n" + "─" * 100 + "\n Pick one\n\n ❯ This\n   That\n"
PENDING_MESSAGE = "\n".join(
    [
        "● ready",
        "────────────────────────────",
        "❯ Write the numbers 1 to 30.",
        "────────────────────────────",
        "  ⏸ manual mode on · ? for shortcuts",
    ]
)


def make_runtime(tmp_path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


def make_session(
    settings: Settings,
    transport: str,
    status: SessionStatus,
    backend: str = "claude_code",
) -> SessionRecord:
    session_dir = settings.sessions_dir / "sess"
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id="sess",
        backend=backend,
        source=SessionSource.MANAGED,
        transport=transport,
        title="t",
        cwd="/tmp/project",
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
        transport_state={"tmux_pane": "%7"},
    )


def stub_pane(runtime: SessionRuntime, monkeypatch, *screens: str) -> list[str]:
    pending = list(screens)
    enters: list[str] = []

    async def capture_snapshot(target: str, start_line: int = -200) -> str:
        # Enter submits the composer's message, which clears it.
        if enters:
            return EMPTY_COMPOSER
        return pending.pop(0) if len(pending) > 1 else pending[0]

    async def resume(target: str) -> None:
        enters.append(target)

    monkeypatch.setattr(runtime.tmux, "capture_snapshot", capture_snapshot)
    monkeypatch.setattr(runtime.tmux, "resume", resume)
    return enters


def resume_note(runtime: SessionRuntime):
    note = runtime.storage.list_events("sess")[-1]
    assert note.kind == EventKind.SYSTEM_NOTE
    assert note.text == "Sent resume"
    return note


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["claude_tty", "tmux"])
@pytest.mark.parametrize(
    "status",
    [SessionStatus.IDLE, SessionStatus.INTERRUPTED, SessionStatus.WAITING_INPUT],
)
async def test_resume_that_submits_nothing_keeps_status(
    tmp_path, monkeypatch, transport: str, status: SessionStatus
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, transport, status))
    enters = stub_pane(runtime, monkeypatch, EMPTY_COMPOSER)

    updated = await runtime.resume("sess")

    assert enters == ["%7"]
    assert updated.status == status
    assert "status" not in resume_note(runtime).metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["claude_tty", "tmux"])
@pytest.mark.parametrize(
    "pending",
    [
        PENDING_MESSAGE,
        (PANES / "composer_labelled.txt").read_text(),
        (PANES / "composer_shell.txt").read_text(),
    ],
    ids=["message", "labelled", "shell"],
)
async def test_resume_that_submits_input_marks_running(
    tmp_path, monkeypatch, transport: str, pending: str
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, transport, SessionStatus.IDLE)
    )
    enters = stub_pane(runtime, monkeypatch, pending)

    updated = await runtime.resume("sess")

    assert enters == ["%7"]
    assert updated.status == SessionStatus.RUNNING
    assert resume_note(runtime).metadata["status"] == SessionStatus.RUNNING


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["claude_tty", "tmux"])
@pytest.mark.parametrize(
    "fixture",
    [
        "approval_bash.txt",
        "plan_approval.txt",
        "trust_dialog.txt",
        "trust_dialog_2_1_331.txt",
    ],
)
async def test_resume_refuses_while_a_dialog_is_open(
    tmp_path, monkeypatch, transport: str, fixture: str
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, transport, SessionStatus.WAITING_INPUT)
    )
    enters = stub_pane(runtime, monkeypatch, (PANES / fixture).read_text())

    with pytest.raises(InputBlockedError):
        await runtime.resume("sess")

    assert enters == []
    assert runtime.get_session("sess").status == SessionStatus.WAITING_INPUT
    assert runtime.storage.list_events("sess") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["claude_tty", "tmux"])
async def test_resume_refuses_a_dialog_that_opens_after_the_guard(
    tmp_path, monkeypatch, transport: str
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, transport, SessionStatus.RUNNING)
    )
    enters = stub_pane(runtime, monkeypatch, EMPTY_COMPOSER, APPROVAL_DIALOG)

    with pytest.raises(InputBlockedError):
        await runtime.resume("sess")

    assert enters == []


@pytest.mark.asyncio
@pytest.mark.parametrize("screen", [PENDING_MESSAGE, APPROVAL_DIALOG])
async def test_resume_on_unreadable_pane_presses_enter_and_keeps_status(
    tmp_path, monkeypatch, screen: str
) -> None:
    # The agent reads neither its composer nor its dialogs off a generic tmux
    # pane, so resume presses Enter and the pane-output heuristic drives status.
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "tmux", SessionStatus.IDLE, backend="codex")
    )
    enters = stub_pane(runtime, monkeypatch, screen)

    updated = await runtime.resume("sess")

    assert enters == ["%7"]
    assert updated.status == SessionStatus.IDLE


@pytest.mark.asyncio
async def test_resume_keeps_awaiting_answer(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "claude_tty", SessionStatus.WAITING_INPUT)
    )
    runtime.storage.update_session(
        "sess", status=SessionStatus.WAITING_INPUT, awaiting_answer=True
    )
    stub_pane(runtime, monkeypatch, EMPTY_COMPOSER)

    updated = await runtime.resume("sess")

    assert updated.status == SessionStatus.WAITING_INPUT
    assert updated.awaiting_answer
    assert AWAITING_ANSWER_KEY not in resume_note(runtime).metadata


@pytest.mark.asyncio
async def test_claude_tty_resume_needs_the_composer_when_it_presses_enter(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "claude_tty", SessionStatus.IDLE)
    runtime.storage.create_session(session)
    enters = stub_pane(runtime, monkeypatch, EMPTY_COMPOSER)
    adapter = runtime.transport_for(session)
    assert isinstance(adapter, TmuxTransport)

    async with adapter._input_lock(session):
        task = asyncio.create_task(runtime.resume("sess"))
        await asyncio.sleep(0.05)
        # A popup nothing recognizes opens while the resume waits its turn.
        monkeypatch.setattr(
            runtime.tmux, "capture_snapshot", AsyncMock(return_value=UNKNOWN_POPUP)
        )
    with pytest.raises(InputBlockedError):
        await task

    assert enters == []


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["claude_tty", "tmux"])
async def test_resume_waits_for_a_send_in_progress(
    tmp_path, monkeypatch, transport: str
) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, transport, SessionStatus.IDLE)
    runtime.storage.create_session(session)
    enters = stub_pane(runtime, monkeypatch, EMPTY_COMPOSER)
    adapter = runtime.transport_for(session)
    assert isinstance(adapter, TmuxTransport)
    lock = adapter._input_lock(session)

    async with lock:
        task = asyncio.create_task(runtime.resume("sess"))
        await asyncio.sleep(0.05)
        assert enters == []
    await task

    assert enters == ["%7"]
