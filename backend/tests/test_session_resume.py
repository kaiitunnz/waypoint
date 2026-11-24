"""A live resume marks a session running only when its Enter submits input."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

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

PANES = Path(__file__).parent / "fixtures" / "claude_tty_pane"
EMPTY_COMPOSER = (PANES / "ready.txt").read_text()
APPROVAL_DIALOG = (PANES / "approval_bash.txt").read_text()
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


def stub_pane(runtime: SessionRuntime, monkeypatch, screen: str) -> list[str]:
    enters: list[str] = []

    async def capture_snapshot(target: str, start_line: int = -200) -> str:
        return screen

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
@pytest.mark.parametrize("screen", [EMPTY_COMPOSER, APPROVAL_DIALOG])
@pytest.mark.parametrize(
    "status",
    [SessionStatus.IDLE, SessionStatus.INTERRUPTED, SessionStatus.WAITING_INPUT],
)
async def test_resume_that_submits_nothing_keeps_status(
    tmp_path, monkeypatch, transport: str, screen: str, status: SessionStatus
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, transport, status))
    enters = stub_pane(runtime, monkeypatch, screen)

    updated = await runtime.resume("sess")

    assert enters == ["%7"]
    assert updated.status == status
    assert "status" not in resume_note(runtime).metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["claude_tty", "tmux"])
async def test_resume_that_submits_input_marks_running(
    tmp_path, monkeypatch, transport: str
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, transport, SessionStatus.IDLE)
    )
    enters = stub_pane(runtime, monkeypatch, PENDING_MESSAGE)

    updated = await runtime.resume("sess")

    assert enters == ["%7"]
    assert updated.status == SessionStatus.RUNNING
    assert resume_note(runtime).metadata["status"] == SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_resume_on_unreadable_pane_keeps_status(tmp_path, monkeypatch) -> None:
    # The agent can't read its composer off a generic tmux pane, so the pane-output
    # heuristic stays the only status driver.
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "tmux", SessionStatus.IDLE, backend="codex")
    )
    enters = stub_pane(runtime, monkeypatch, PENDING_MESSAGE)

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
