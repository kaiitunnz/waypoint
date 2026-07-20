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

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
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

    async def fake_create_session(_request, **_kwargs) -> SessionRecord:
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

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
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

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
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

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
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

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
        captured.append(request.transport)
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    await runtime.scheduler._fire_due_schedules()

    assert captured == ["claude_tty"]


# ── Recurring (cron) schedules ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_recurring_schedule_sets_next_run(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
            cwd="/tmp/project",
            cron="0 9 * * 1-5",
            timezone="Asia/Singapore",
        )
    )
    assert schedule.status == ScheduleStatus.PENDING
    assert schedule.cron == "0 9 * * 1-5"
    assert schedule.timezone == "Asia/Singapore"
    assert schedule.scheduled_at > datetime.now(UTC)
    assert schedule.last_run_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # no timing at all
        {"delay_seconds": 60, "scheduled_at": "unused"},  # both one-time
        {"delay_seconds": 60, "cron": "* * * * *", "timezone": "UTC"},  # mix
        {"cron": "* * * * *"},  # cron without timezone
        {"timezone": "UTC"},  # timezone without cron
        {"cron": "not valid", "timezone": "UTC"},  # bad cron
        {"cron": "* * * * *", "timezone": "Not/AZone"},  # bad timezone
    ],
)
async def test_create_schedule_invalid_timing_returns_400(tmp_path, kwargs) -> None:
    runtime = make_runtime(tmp_path)
    if kwargs.get("scheduled_at") == "unused":
        kwargs["scheduled_at"] = cast(Any, datetime.now(UTC) + timedelta(minutes=5))
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_schedule(
            ScheduleCreateRequest(backend="codex", cwd="/tmp/project", **kwargs)
        )
    assert exc.value.status_code == 400


async def _make_due_recurring(runtime, *, seconds_ago: int):
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
            cwd="/tmp/project",
            initial_prompt="daily",
            cron="* * * * *",
            timezone="UTC",
        )
    )
    occurrence = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    runtime.storage.update_schedule(schedule.id, scheduled_at=occurrence)
    return schedule, occurrence


@pytest.mark.asyncio
async def test_recurring_schedule_stays_pending_and_advances(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    schedule, occurrence = await _make_due_recurring(runtime, seconds_ago=1)
    created_session = make_session(runtime.settings, "codex-rec-1")
    runtime.storage.create_session(created_session)
    launches = 0

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
        nonlocal launches
        launches += 1
        return created_session

    async def fake_handle_input(session_id: str, request) -> SessionRecord:
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)
    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)

    await runtime.scheduler._fire_due_schedules()

    refreshed = runtime.storage.get_schedule(schedule.id)
    assert refreshed is not None
    assert refreshed.status == ScheduleStatus.PENDING  # never terminal
    assert refreshed.scheduled_at > datetime.now(UTC)  # advanced
    assert refreshed.last_run_status == "launched"
    assert refreshed.last_run_at == occurrence  # claimed occurrence, not now
    assert refreshed.last_failure_reason is None
    assert launches == 1

    # Fire a second occurrence: still pending, advances again, still runs.
    next_occurrence = datetime.now(UTC) - timedelta(seconds=1)
    runtime.storage.update_schedule(schedule.id, scheduled_at=next_occurrence)
    await runtime.scheduler._fire_due_schedules()
    refreshed2 = runtime.storage.get_schedule(schedule.id)
    assert refreshed2 is not None
    assert refreshed2.status == ScheduleStatus.PENDING
    assert launches == 2
    assert refreshed2.last_run_at == next_occurrence


@pytest.mark.asyncio
async def test_recurring_failure_keeps_pending_then_clears_on_success(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    schedule, occurrence = await _make_due_recurring(runtime, seconds_ago=1)
    created_session = make_session(runtime.settings, "codex-rec-2")
    runtime.storage.create_session(created_session)
    should_fail = True

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
        if should_fail:
            raise RuntimeError("launch boom")
        return created_session

    async def fake_handle_input(session_id: str, request) -> SessionRecord:
        return created_session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)
    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)

    await runtime.scheduler._fire_due_schedules()
    failed = runtime.storage.get_schedule(schedule.id)
    assert failed is not None
    assert failed.status == ScheduleStatus.PENDING  # RFC req 9: not disabled
    assert failed.last_run_status == "failed"
    assert failed.last_failure_reason == "launch boom"

    # Next occurrence succeeds → latest error cleared, still pending.
    should_fail = False
    runtime.storage.update_schedule(
        schedule.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    await runtime.scheduler._fire_due_schedules()
    recovered = runtime.storage.get_schedule(schedule.id)
    assert recovered is not None
    assert recovered.status == ScheduleStatus.PENDING
    assert recovered.last_run_status == "launched"
    assert recovered.last_failure_reason is None


@pytest.mark.asyncio
async def test_recurring_missed_occurrence_skips_action_but_advances(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    # Due an hour ago — well beyond the grace window (downtime).
    schedule, _ = await _make_due_recurring(runtime, seconds_ago=3600)
    launches = 0

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
        nonlocal launches
        launches += 1
        raise AssertionError("missed occurrence must not launch")

    monkeypatch.setattr(runtime, "create_session", fake_create_session)

    await runtime.scheduler._fire_due_schedules()

    refreshed = runtime.storage.get_schedule(schedule.id)
    assert refreshed is not None
    assert launches == 0  # no backlog replay
    assert refreshed.status == ScheduleStatus.PENDING
    assert refreshed.scheduled_at > datetime.now(UTC)  # still advanced
    assert refreshed.last_run_status is None  # never ran


@pytest.mark.asyncio
async def test_multiple_due_recurring_all_fire_in_one_batch(
    tmp_path, monkeypatch
) -> None:
    # Guards the batch-``now`` fix: every due recurring item in a batch is
    # classified against the same captured ``now``, so none is misclassified
    # as missed because an earlier item's launch consumed wall-clock time.
    runtime = make_runtime(tmp_path)
    ids = []
    for _ in range(3):
        schedule, _ = await _make_due_recurring(runtime, seconds_ago=1)
        ids.append(schedule.id)
    session = make_session(runtime.settings, "codex-batch")
    runtime.storage.create_session(session)

    async def fake_create_session(request, **_kwargs) -> SessionRecord:
        return session

    async def fake_handle_input(session_id: str, request) -> SessionRecord:
        return session

    monkeypatch.setattr(runtime, "create_session", fake_create_session)
    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)

    await runtime.scheduler._fire_due_schedules()

    for schedule_id in ids:
        refreshed = runtime.storage.get_schedule(schedule_id)
        assert refreshed is not None
        assert refreshed.last_run_status == "launched"
        assert refreshed.status == ScheduleStatus.PENDING


@pytest.mark.asyncio
async def test_cancel_recurring_stops_future_claims(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    schedule = runtime.scheduler.create_schedule(
        ScheduleCreateRequest(
            backend="codex",
            cwd="/tmp/project",
            cron="* * * * *",
            timezone="UTC",
        )
    )
    cancelled = runtime.scheduler.cancel_schedule(schedule.id)
    assert cancelled.status == ScheduleStatus.CANCELLED
    # A claim racing the cancel is a no-op (WHERE status='pending').
    claimed = runtime.storage.claim_recurring_schedule(
        schedule.id,
        cancelled.scheduled_at,
        datetime.now(UTC) + timedelta(minutes=1),
    )
    assert claimed is None
