from datetime import UTC, datetime
from typing import Any, cast

import pytest

from waypoint.config import Settings
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    Backend,
    EventKind,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SessionTransport,
)
from waypoint.storage import Storage


class FakeStructuredAdapter:
    def __init__(self, pending: bool = False) -> None:
        self.pending = pending
        self.inputs: list[tuple[str, str]] = []

    async def send_input(self, session_id: str, text: str) -> None:
        self.inputs.append((session_id, text))

    def has_pending_approval(self, session_id: str) -> bool:
        return self.pending


def make_runtime(tmp_path) -> tuple[SessionRuntime, Storage, Settings]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    return runtime, storage, settings


def make_session(settings: Settings, **overrides) -> SessionRecord:
    session_dir = settings.sessions_dir / overrides.get("id", "sess")
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=overrides.get("id", "sess"),
        backend=overrides.get("backend", Backend.CODEX),
        source=SessionSource.MANAGED,
        transport=overrides.get("transport", SessionTransport.CODEX_APP_SERVER),
        title="Session",
        cwd="/tmp/project",
        remote_cwd=overrides.get("remote_cwd"),
        status=overrides.get("status", SessionStatus.IDLE),
        created_at=now,
        updated_at=now,
        last_event_at=now,
        thread_id=overrides.get("thread_id", "thread-1"),
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


@pytest.mark.asyncio
async def test_handle_input_builtin_status_intercepts_structured_session(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter()
    runtime.codex = cast(Any, fake)
    session = make_session(settings)
    storage.create_session(session)

    updated = await runtime.handle_input("sess", SessionInputRequest(text="/status"))

    assert fake.inputs == []
    assert updated.status == SessionStatus.IDLE
    events = storage.list_events("sess")
    assert [event.kind for event in events] == [EventKind.USER_INPUT, EventKind.SYSTEM_NOTE]
    assert events[0].text == "/status"
    assert "Transport: codex_app_server" in events[1].text
    assert "Thread: thread-1" in events[1].text


@pytest.mark.asyncio
async def test_handle_input_builtin_permissions_reports_pending_approval(tmp_path) -> None:
    runtime, storage, settings = make_runtime(tmp_path)
    fake = FakeStructuredAdapter(pending=True)
    runtime.claude = cast(Any, fake)
    session = make_session(
        settings,
        id="claude-sess",
        backend=Backend.CLAUDE_CODE,
        transport=SessionTransport.CLAUDE_CLI,
        thread_id="claude-thread",
    )
    storage.create_session(session)

    updated = await runtime.handle_input(
        "claude-sess", SessionInputRequest(text="/permissions")
    )

    assert fake.inputs == []
    assert updated.status == SessionStatus.IDLE
    events = storage.list_events("claude-sess")
    assert events[-1].kind == EventKind.SYSTEM_NOTE
    assert "Pending approval: yes" in events[-1].text
    assert "Approve for session" in events[-1].text
