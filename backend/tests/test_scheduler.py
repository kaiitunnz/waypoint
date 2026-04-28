from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import HTTPException

from waypoint.config import Settings
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    Backend,
    ScheduleCreateRequest,
    ScheduleStatus,
    SessionRecord,
    SessionSource,
    SessionStatus,
    SessionTransport,
)
from waypoint.storage import Storage


def make_runtime(tmp_path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage)


def make_session(settings: Settings, session_id: str) -> SessionRecord:
    session_dir = settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend=Backend.CODEX,
        source=SessionSource.MANAGED,
        transport=SessionTransport.CODEX_APP_SERVER,
        title="Scheduled",
        cwd="/tmp/project",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        thread_id="thread-1",
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


@pytest.mark.asyncio
async def test_create_schedule_with_delay_persists_pending(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend=Backend.CODEX,
            cwd="/tmp/project",
            initial_prompt="hello",
            delay_seconds=60,
        )
    )
    assert schedule.status == ScheduleStatus.PENDING
    assert schedule.scheduled_at > datetime.now(UTC)
    stored = runtime.storage.list_schedules()
    assert [item.id for item in stored] == [schedule.id]
    assert stored[0].initial_prompt == "hello"


@pytest.mark.asyncio
async def test_create_schedule_requires_time_input(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_schedule(
            ScheduleCreateRequest(backend=Backend.CODEX, cwd="/tmp/project")
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cancel_schedule_marks_cancelled(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend=Backend.CODEX, cwd="/tmp/project", delay_seconds=300
        )
    )
    cancelled = runtime.scheduler.cancel_schedule(schedule.id)
    assert cancelled.status == ScheduleStatus.CANCELLED
    with pytest.raises(HTTPException):
        runtime.scheduler.cancel_schedule(schedule.id)


@pytest.mark.asyncio
async def test_fire_due_schedules_creates_session_and_sends_prompt(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend=Backend.CODEX,
            cwd="/tmp/project",
            initial_prompt="ship it",
            delay_seconds=0,
        )
    )
    runtime.storage.update_schedule(
        schedule.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    created_session = make_session(runtime.settings, "codex-aaaaaaaa")
    runtime.storage.create_session(created_session)
    inputs: list[tuple[str, str]] = []

    async def fake_create_session(request) -> SessionRecord:
        return created_session

    async def fake_handle_input(session_id: str, request) -> SessionRecord:
        inputs.append((session_id, request.text))
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)
    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)

    await runtime.scheduler._fire_due_schedules()

    refreshed = runtime.storage.get_schedule(schedule.id)
    assert refreshed is not None
    assert refreshed.status == ScheduleStatus.LAUNCHED
    assert refreshed.session_id == created_session.id
    assert inputs == [(created_session.id, "ship it")]


@pytest.mark.asyncio
async def test_fire_due_schedules_records_failure(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend=Backend.CODEX, cwd="/tmp/project", delay_seconds=0
        )
    )
    runtime.storage.update_schedule(
        schedule.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    async def fake_create_session(_request) -> SessionRecord:
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    await runtime.scheduler._fire_due_schedules()
    refreshed = runtime.storage.get_schedule(schedule.id)
    assert refreshed is not None
    assert refreshed.status == ScheduleStatus.FAILED
    assert refreshed.failure_reason == "boom"


@pytest.mark.asyncio
async def test_scheduled_at_input_normalised_to_utc(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    naive_when = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend=Backend.CODEX,
            cwd="/tmp/project",
            scheduled_at=cast(Any, naive_when),
        )
    )
    assert schedule.scheduled_at.tzinfo is not None
    assert schedule.scheduled_at.utcoffset() == timedelta(0)
