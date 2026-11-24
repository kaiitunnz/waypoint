"""Live resume presses Enter on a pane without claiming a turn started."""

from datetime import UTC, datetime
from typing import Any

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


def make_runtime(tmp_path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


def make_session(
    settings: Settings, transport: str, status: SessionStatus
) -> SessionRecord:
    session_dir = settings.sessions_dir / "sess"
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id="sess",
        backend="claude_code",
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


def record_tmux_commands(runtime: SessionRuntime, monkeypatch) -> list[tuple[Any]]:
    calls: list[tuple[Any]] = []

    async def fake_run(*args: Any) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(runtime.tmux, "_run", fake_run)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["claude_tty", "tmux"])
@pytest.mark.parametrize(
    "status",
    [SessionStatus.IDLE, SessionStatus.INTERRUPTED, SessionStatus.WAITING_INPUT],
)
async def test_live_resume_presses_enter_and_keeps_status(
    tmp_path, monkeypatch, transport: str, status: SessionStatus
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, transport, status))
    commands = record_tmux_commands(runtime, monkeypatch)

    updated = await runtime.resume("sess")

    # The Enter may start no turn; the transport's own status driver (the
    # transcript tailer, or the pane-output heuristic) reports one that does.
    assert commands == [("send-keys", "-t", "%7", "Enter")]
    assert updated.status == status
    assert runtime.get_session("sess").status == status
    note = runtime.storage.list_events("sess")[-1]
    assert note.kind == EventKind.SYSTEM_NOTE
    assert note.text == "Sent resume"
    assert "status" not in note.metadata


@pytest.mark.asyncio
async def test_live_resume_keeps_awaiting_answer(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(
        make_session(runtime.settings, "claude_tty", SessionStatus.WAITING_INPUT)
    )
    runtime.storage.update_session(
        "sess", status=SessionStatus.WAITING_INPUT, awaiting_answer=True
    )
    record_tmux_commands(runtime, monkeypatch)

    updated = await runtime.resume("sess")

    assert updated.status == SessionStatus.WAITING_INPUT
    assert updated.awaiting_answer
    assert AWAITING_ANSWER_KEY not in runtime.storage.list_events("sess")[-1].metadata
