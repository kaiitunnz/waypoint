from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi import HTTPException

from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    LaunchMode,
    ScheduleCreateRequest,
    ScheduleStatus,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
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
        backend="codex",
        source=SessionSource.MANAGED,
        transport="codex_app_server",
        title="Scheduled",
        cwd="/tmp/project",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        transport_state={"thread_id": "thread-1"},
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


@pytest.mark.asyncio
async def test_create_schedule_with_delay_persists_pending(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
            cwd="/tmp/project",
            initial_prompt="hello",
            delay_seconds=60,
            launch_mode=LaunchMode.TMUX_WRAPPER,
        )
    )
    assert schedule.status == ScheduleStatus.PENDING
    assert schedule.scheduled_at > datetime.now(UTC)
    stored = runtime.storage.list_schedules()
    assert [item.id for item in stored] == [schedule.id]
    assert stored[0].initial_prompt == "hello"
    assert stored[0].launch_mode == LaunchMode.TMUX_WRAPPER


@pytest.mark.asyncio
async def test_create_schedule_persists_launch_env_privately(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
            cwd="/tmp/project",
            delay_seconds=60,
            launch_env={"OPENAI_API_KEY": "secret"},
        )
    )
    stored = runtime.storage.get_schedule(schedule.id)
    assert stored is not None
    assert stored.launch_env == {"OPENAI_API_KEY": "secret"}
    assert "launch_env" not in stored.model_dump(mode="json")


@pytest.mark.asyncio
async def test_create_schedule_requires_time_input(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_schedule(
            ScheduleCreateRequest(backend="codex", cwd="/tmp/project")
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cancel_schedule_marks_cancelled(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(backend="codex", cwd="/tmp/project", delay_seconds=300)
    )
    cancelled = runtime.scheduler.cancel_schedule(schedule.id)
    assert cancelled.status == ScheduleStatus.CANCELLED
    # Second call on a non-pending schedule should hard-delete the row so
    # the user can clear it from the list.
    runtime.scheduler.cancel_schedule(schedule.id)
    assert runtime.storage.get_schedule(schedule.id) is None
    # Third call has nothing to act on.
    with pytest.raises(HTTPException):
        runtime.scheduler.cancel_schedule(schedule.id)


@pytest.mark.asyncio
async def test_clear_history_removes_terminal_schedules(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    pending = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(backend="codex", cwd="/tmp/project", delay_seconds=600)
    )
    cancelled = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(backend="codex", cwd="/tmp/project", delay_seconds=600)
    )
    runtime.scheduler.cancel_schedule(cancelled.id)
    runtime.storage.update_schedule(
        runtime.scheduler.create_schedule(
            ScheduleCreateRequest(
                backend="codex", cwd="/tmp/project", delay_seconds=600
            )
        ).id,
        status=ScheduleStatus.LAUNCHED,
    )

    removed = runtime.scheduler.clear_history()
    assert removed == 2
    remaining = [item.id for item in runtime.storage.list_schedules()]
    assert remaining == [pending.id]


@pytest.mark.asyncio
async def test_fire_due_schedules_creates_session_and_sends_prompt(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
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
        ScheduleCreateRequest(backend="codex", cwd="/tmp/project", delay_seconds=0)
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
            backend="codex",
            cwd="/tmp/project",
            scheduled_at=cast(Any, naive_when),
        )
    )
    assert schedule.scheduled_at.tzinfo is not None
    assert schedule.scheduled_at.utcoffset() == timedelta(0)


@pytest.mark.asyncio
async def test_create_schedule_persists_permission_mode(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="claude_code",
            cwd="/tmp/project",
            permission_mode="plan",
            delay_seconds=60,
        )
    )
    assert schedule.permission_mode == "plan"
    stored = runtime.storage.get_schedule(schedule.id)
    assert stored is not None
    assert stored.permission_mode == "plan"


@pytest.mark.asyncio
async def test_create_schedule_rejects_unknown_permission_mode(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_schedule(
            ScheduleCreateRequest(
                backend="codex",
                cwd="/tmp/project",
                permission_mode="not-a-mode",
                delay_seconds=60,
            )
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_fire_passes_permission_mode_to_create_session(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="claude_code",
            cwd="/tmp/project",
            permission_mode="acceptEdits",
            delay_seconds=0,
        )
    )
    runtime.storage.update_schedule(
        schedule.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    created_session = make_session(runtime.settings, "claude-aaaaaaaa")
    runtime.storage.create_session(created_session)
    captured: list[str | None] = []

    async def fake_create_session(request) -> SessionRecord:
        captured.append(request.permission_mode)
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    await runtime.scheduler._fire_due_schedules()

    assert captured == ["acceptEdits"]


@pytest.mark.asyncio
async def test_fire_passes_launch_env_to_create_session(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
            cwd="/tmp/project",
            delay_seconds=0,
            launch_env={"OPENAI_API_KEY": "secret"},
        )
    )
    runtime.storage.update_schedule(
        schedule.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    created_session = make_session(runtime.settings, "codex-env")
    runtime.storage.create_session(created_session)
    captured: list[dict[str, str]] = []

    async def fake_create_session(request) -> SessionRecord:
        captured.append(request.launch_env)
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    await runtime.scheduler._fire_due_schedules()

    assert captured == [{"OPENAI_API_KEY": "secret"}]


@pytest.mark.asyncio
async def test_fire_passes_launch_mode_to_create_session(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
            cwd="/tmp/project",
            launch_mode=LaunchMode.TMUX_WRAPPER,
            delay_seconds=0,
        )
    )
    runtime.storage.update_schedule(
        schedule.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    created_session = make_session(runtime.settings, "codex-aaaaaaaa")
    runtime.storage.create_session(created_session)
    captured: list[LaunchMode] = []

    async def fake_create_session(request) -> SessionRecord:
        captured.append(request.launch_mode)
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    await runtime.scheduler._fire_due_schedules()

    assert captured == [LaunchMode.TMUX_WRAPPER]


@pytest.mark.asyncio
async def test_create_schedule_persists_transport(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="claude_code",
            cwd="/tmp/project",
            transport="claude_tty",
            delay_seconds=60,
        )
    )
    assert schedule.transport == "claude_tty"
    stored = runtime.storage.get_schedule(schedule.id)
    assert stored is not None
    assert stored.transport == "claude_tty"


@pytest.mark.asyncio
async def test_create_schedule_without_transport_defaults_none(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="claude_code",
            cwd="/tmp/project",
            delay_seconds=60,
        )
    )
    assert schedule.transport is None
    stored = runtime.storage.get_schedule(schedule.id)
    assert stored is not None
    assert stored.transport is None


@pytest.mark.asyncio
async def test_create_schedule_rejects_unsupported_transport(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_schedule(
            ScheduleCreateRequest(
                backend="claude_code",
                cwd="/tmp/project",
                transport="codex_app_server",
                delay_seconds=60,
            )
        )
    assert exc.value.status_code == 400
    assert "codex_app_server" in exc.value.detail


@pytest.mark.asyncio
async def test_fire_passes_transport_to_create_session(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="claude_code",
            cwd="/tmp/project",
            transport="claude_tty",
            delay_seconds=0,
        )
    )
    runtime.storage.update_schedule(
        schedule.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    created_session = make_session(runtime.settings, "claude-aaaaaaaa")
    runtime.storage.create_session(created_session)
    captured: list[str | None] = []

    async def fake_create_session(request) -> SessionRecord:
        captured.append(request.transport)
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    await runtime.scheduler._fire_due_schedules()

    assert captured == ["claude_tty"]
