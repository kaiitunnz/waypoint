import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException
from starlette import status as http

from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    IdleMessageBatchMode,
    ScheduledMessageCreateRequest,
    ScheduledMessageRecord,
    ScheduledMessageStatus,
    ScheduledMessageTrigger,
    SessionInputItem,
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
        title="Test session",
        cwd="/tmp/project",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        transport_state={"thread_id": "thread-1"},
        raw_log_path=str(session_dir / "raw.log"),
        structured_log_path=str(session_dir / "events.jsonl"),
    )


# ── Storage tests ────────────────────────────────────────────────────────────


def test_scheduled_message_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    item = SessionInputItem(type="text", text="hello")
    record = ScheduledMessageRecord(
        id="msg-1",
        session_id="sess-1",
        text="ship it",
        items=[item],
        attachments=["att-1"],
        scheduled_at=now + timedelta(minutes=5),
        created_at=now,
        status=ScheduledMessageStatus.PENDING,
    )
    storage.create_scheduled_message(record)

    loaded = storage.get_scheduled_message("msg-1")
    assert loaded is not None
    assert loaded.text == "ship it"
    assert loaded.items == [item]
    assert loaded.attachments == ["att-1"]
    assert loaded.status == ScheduledMessageStatus.PENDING


def test_scheduled_message_stores_command(tmp_path) -> None:
    from waypoint.schemas import CompletionDispatch, SessionCommandInvocation

    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    cmd = SessionCommandInvocation(
        completion_id="cmd-1",
        name="test",
        arguments="--flag",
        dispatch=CompletionDispatch.PLAIN_TEXT,
    )
    record = ScheduledMessageRecord(
        id="msg-cmd",
        session_id="sess-1",
        command=cmd,
        scheduled_at=now + timedelta(minutes=5),
        created_at=now,
    )
    storage.create_scheduled_message(record)
    loaded = storage.get_scheduled_message("msg-cmd")
    assert loaded is not None
    assert loaded.command is not None
    assert loaded.command.completion_id == "cmd-1"
    assert loaded.command.name == "test"
    assert loaded.command.arguments == "--flag"


def test_scheduled_message_list_filters_by_status(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    for status in ScheduledMessageStatus:
        storage.create_scheduled_message(
            ScheduledMessageRecord(
                id=f"msg-{status.value}",
                session_id="sess-1",
                scheduled_at=now + timedelta(minutes=5),
                created_at=now,
                status=status,
            )
        )
    pending = storage.list_scheduled_messages([ScheduledMessageStatus.PENDING])
    assert len(pending) == 1
    assert pending[0].id == "msg-pending"


def test_scheduled_message_list_filters_by_session(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    for session_id in ("sess-1", "sess-2"):
        storage.create_scheduled_message(
            ScheduledMessageRecord(
                id=f"msg-{session_id}",
                session_id=session_id,
                scheduled_at=now + timedelta(minutes=5),
                created_at=now,
                status=ScheduledMessageStatus.PENDING,
            )
        )
    filtered = storage.list_scheduled_messages(session_id="sess-2")
    assert [item.id for item in filtered] == ["msg-sess-2"]


def test_scheduled_message_update(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    storage.create_scheduled_message(
        ScheduledMessageRecord(
            id="msg-upd",
            session_id="sess-1",
            scheduled_at=now + timedelta(minutes=5),
            created_at=now,
        )
    )
    updated = storage.update_scheduled_message(
        "msg-upd", status=ScheduledMessageStatus.SENT
    )
    assert updated.status == ScheduledMessageStatus.SENT
    loaded = storage.get_scheduled_message("msg-upd")
    assert loaded is not None
    assert loaded.status == ScheduledMessageStatus.SENT


def test_scheduled_message_update_missing_raises(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    with pytest.raises(KeyError):
        storage.update_scheduled_message(
            "does-not-exist", status=ScheduledMessageStatus.SENT
        )


def test_scheduled_message_delete(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    storage.create_scheduled_message(
        ScheduledMessageRecord(
            id="msg-del",
            session_id="sess-1",
            scheduled_at=now + timedelta(minutes=5),
            created_at=now,
        )
    )
    assert storage.delete_scheduled_message("msg-del") is True
    assert storage.get_scheduled_message("msg-del") is None
    assert storage.delete_scheduled_message("msg-del") is False


def test_scheduled_message_delete_by_status(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    for session_id, status in (
        ("sess-1", ScheduledMessageStatus.SENT),
        ("sess-1", ScheduledMessageStatus.FAILED),
        ("sess-1", ScheduledMessageStatus.PENDING),
        ("sess-2", ScheduledMessageStatus.SENT),
    ):
        storage.create_scheduled_message(
            ScheduledMessageRecord(
                id=f"msg-{session_id}-{status.value}",
                session_id=session_id,
                scheduled_at=now + timedelta(minutes=5),
                created_at=now,
                status=status,
            )
        )
    removed = storage.delete_scheduled_messages_by_status(
        [ScheduledMessageStatus.SENT, ScheduledMessageStatus.FAILED],
        session_id="sess-1",
    )
    assert removed == 2
    remaining = [r.id for r in storage.list_scheduled_messages()]
    assert remaining == ["msg-sess-1-pending", "msg-sess-2-sent"]


# ── Scheduler tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_message_schedule_persists(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="hello world",
            delay_seconds=60,
        ),
    )
    assert record.status == ScheduledMessageStatus.PENDING
    assert record.session_id == "sess-1"
    assert record.text == "hello world"
    assert record.scheduled_at > datetime.now(UTC)
    stored = runtime.storage.list_scheduled_messages()
    assert [r.id for r in stored] == [record.id]


@pytest.mark.asyncio
async def test_create_message_schedule_no_session_404(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_message_schedule(
            "no-such-session",
            ScheduledMessageCreateRequest(text="hi", delay_seconds=60),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_message_schedule_empty_body_400(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_message_schedule(
            "sess-1",
            ScheduledMessageCreateRequest(delay_seconds=60),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_message_schedule_rejects_past_time(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_message_schedule(
            "sess-1",
            ScheduledMessageCreateRequest(
                text="hi",
                scheduled_at=(datetime.now(UTC) - timedelta(hours=1)),
            ),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cancel_message_schedule(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="hi", delay_seconds=300),
    )
    cancelled = runtime.scheduler.cancel_message_schedule(record.id)
    assert cancelled.status == ScheduledMessageStatus.CANCELLED

    runtime.scheduler.cancel_message_schedule(record.id)
    assert runtime.storage.get_scheduled_message(record.id) is None

    with pytest.raises(HTTPException):
        runtime.scheduler.cancel_message_schedule(record.id)


@pytest.mark.asyncio
async def test_clear_message_history(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    other_session = make_session(runtime.settings, "sess-2")
    runtime.storage.create_session(session)
    runtime.storage.create_session(other_session)

    pending = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="keep", delay_seconds=600),
    )
    cancelled = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="cancel", delay_seconds=600),
    )
    runtime.scheduler.cancel_message_schedule(cancelled.id)
    runtime.storage.update_scheduled_message(
        runtime.scheduler.create_message_schedule(
            "sess-2",
            ScheduledMessageCreateRequest(text="sent", delay_seconds=600),
        ).id,
        status=ScheduledMessageStatus.SENT,
    )

    removed = runtime.scheduler.clear_message_history(session_id="sess-1")
    assert removed == 1
    remaining_sess_1 = [
        r.id for r in runtime.storage.list_scheduled_messages(session_id="sess-1")
    ]
    assert remaining_sess_1 == [pending.id]
    assert len(runtime.storage.list_scheduled_messages(session_id="sess-2")) == 1


@pytest.mark.asyncio
async def test_fire_due_message_schedule_sends_input(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="ship it",
            submit=False,
            delay_seconds=0,
        ),
    )
    runtime.storage.update_scheduled_message(
        record.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    inputs: list[tuple[str, str, bool]] = []

    async def fake_handle_input(session_id: str, request) -> SessionRecord:
        inputs.append((session_id, request.text, request.submit))
        return session

    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)

    await runtime.scheduler._fire_due_schedules()

    refreshed = runtime.storage.get_scheduled_message(record.id)
    assert refreshed is not None
    assert refreshed.status == ScheduledMessageStatus.SENT
    assert inputs == [("sess-1", "ship it", False)]


@pytest.mark.asyncio
async def test_fire_due_message_schedule_missing_session(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    now = datetime.now(UTC)

    # Insert directly via storage to bypass the scheduler's session check.
    runtime.storage.create_scheduled_message(
        ScheduledMessageRecord(
            id="msg-orphan",
            session_id="no-such-session",
            text="hi",
            scheduled_at=now - timedelta(seconds=1),
            created_at=now,
            status=ScheduledMessageStatus.PENDING,
        )
    )

    await runtime.scheduler._fire_due_schedules()

    refreshed = runtime.storage.get_scheduled_message("msg-orphan")
    assert refreshed is not None
    assert refreshed.status == ScheduledMessageStatus.FAILED
    assert refreshed.failure_reason == "session not found"


@pytest.mark.asyncio
async def test_purge_session_messages_drops_only_that_session(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    now = datetime.now(UTC)
    for msg_id, session_id, status in (
        ("msg-a1", "sess-a", ScheduledMessageStatus.PENDING),
        ("msg-a2", "sess-a", ScheduledMessageStatus.SENT),
        ("msg-b1", "sess-b", ScheduledMessageStatus.PENDING),
    ):
        runtime.storage.create_scheduled_message(
            ScheduledMessageRecord(
                id=msg_id,
                session_id=session_id,
                text="hi",
                scheduled_at=now + timedelta(minutes=5),
                created_at=now,
                status=status,
            )
        )

    removed = await runtime.scheduler.purge_session_messages("sess-a")

    assert removed == 2
    assert runtime.storage.get_scheduled_message("msg-a1") is None
    assert runtime.storage.get_scheduled_message("msg-a2") is None
    assert runtime.storage.get_scheduled_message("msg-b1") is not None


@pytest.mark.asyncio
async def test_fire_due_message_schedule_records_failure(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="hi", delay_seconds=0),
    )
    runtime.storage.update_scheduled_message(
        record.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    async def fake_handle_input(_session_id, _request) -> SessionRecord:
        raise RuntimeError("boom")

    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)

    await runtime.scheduler._fire_due_schedules()

    refreshed = runtime.storage.get_scheduled_message(record.id)
    assert refreshed is not None
    assert refreshed.status == ScheduledMessageStatus.FAILED
    assert refreshed.failure_reason == "boom"


# ── Recurring (cron) message schedules ────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_recurring_message_sets_next_run(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="stand-up",
            cron="0 9 * * 1-5",
            timezone="Asia/Singapore",
        ),
    )
    assert record.status == ScheduledMessageStatus.PENDING
    assert record.cron == "0 9 * * 1-5"
    assert record.timezone == "Asia/Singapore"
    assert record.scheduled_at > datetime.now(UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"cron": "* * * * *"},  # cron without timezone
        {"timezone": "UTC"},  # timezone without cron
        {"delay_seconds": 60, "cron": "* * * * *", "timezone": "UTC"},  # mix
        {"cron": "bad", "timezone": "UTC"},  # invalid cron
    ],
)
async def test_create_recurring_message_invalid_timing_400(tmp_path, kwargs) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_message_schedule(
            "sess-1", ScheduledMessageCreateRequest(text="hi", **kwargs)
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_recurring_message_stays_pending_and_advances(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="tick", cron="* * * * *", timezone="UTC"),
    )
    occurrence = datetime.now(UTC) - timedelta(seconds=1)
    runtime.storage.update_scheduled_message(record.id, scheduled_at=occurrence)

    sends = 0

    async def fake_handle_input(session_id, request) -> SessionRecord:
        nonlocal sends
        sends += 1
        return session

    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)

    await runtime.scheduler._fire_due_schedules()

    refreshed = runtime.storage.get_scheduled_message(record.id)
    assert refreshed is not None
    assert refreshed.status == ScheduledMessageStatus.PENDING
    assert refreshed.scheduled_at > datetime.now(UTC)
    assert refreshed.last_run_status == "sent"
    assert refreshed.last_run_at == occurrence
    assert sends == 1


@pytest.mark.asyncio
async def test_clear_history_never_deletes_active_recurrence(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    recurring = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="daily", cron="0 9 * * *", timezone="UTC"),
    )
    # Simulate a run that left a failure on the still-active recurrence.
    runtime.storage.update_scheduled_message(
        recurring.id, last_run_status="failed", last_failure_reason="nope"
    )

    removed = runtime.scheduler.clear_message_history(session_id="sess-1")
    assert removed == 0
    still_there = runtime.storage.get_scheduled_message(recurring.id)
    assert still_there is not None
    assert still_there.status == ScheduledMessageStatus.PENDING


@pytest.mark.asyncio
async def test_session_deletion_removes_recurring_messages(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    recurring = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="daily", cron="0 9 * * *", timezone="UTC"),
    )
    removed = await runtime.scheduler.purge_session_messages("sess-1")
    assert removed == 1
    assert runtime.storage.get_scheduled_message(recurring.id) is None


# ── API tests ────────────────────────────────────────────────────────────────


def test_message_schedule_create_request_schema() -> None:
    req = ScheduledMessageCreateRequest(
        text="hello",
        delay_seconds=60,
    )
    assert req.text == "hello"
    assert req.delay_seconds == 60
    assert req.scheduled_at is None
    assert req.command is None
    assert req.items is None
    assert req.attachments == []
    assert req.submit is True


# ── Idle-triggered messages ──────────────────────────────────────────────────


def _record_sends(runtime, monkeypatch) -> list[tuple[str, str]]:
    """Patch handle_input to record (session_id, text) and no-op the send."""
    sends: list[tuple[str, str]] = []

    async def fake_handle_input(session_id: str, request) -> SessionRecord:
        sends.append((session_id, request.text))
        return runtime.get_session(session_id)

    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)
    return sends


def _set_status(runtime, session_id: str, status: SessionStatus) -> None:
    runtime.storage.update_session(session_id, status=status)


@pytest.mark.asyncio
async def test_idle_schedule_allocates_batch_and_defaults_time(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="after this turn",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )
    assert record.trigger == ScheduledMessageTrigger.IDLE
    assert record.idle_batch == 1
    # with_previous on an empty queue creates the imminent batch (no wait).
    assert record.wait_for_idle_transition is False

    loaded = runtime.storage.get_scheduled_message(record.id)
    assert loaded is not None
    assert loaded.trigger == ScheduledMessageTrigger.IDLE
    assert loaded.wait_for_idle_transition is False


@pytest.mark.asyncio
async def test_idle_delivers_when_already_idle(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")  # IDLE by default
    runtime.storage.create_session(session)
    sends = _record_sends(runtime, monkeypatch)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="go now",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )
    await runtime.scheduler._fire_due_schedules()

    assert sends == [("sess-1", "go now")]
    refreshed = runtime.storage.get_scheduled_message(record.id)
    assert refreshed is not None
    assert refreshed.status == ScheduledMessageStatus.SENT


@pytest.mark.asyncio
async def test_idle_holds_through_running_and_waiting_input(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    _set_status(runtime, "sess-1", SessionStatus.RUNNING)
    sends = _record_sends(runtime, monkeypatch)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="later",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )

    await runtime.scheduler._fire_due_schedules()
    assert sends == []
    _set_status(runtime, "sess-1", SessionStatus.WAITING_INPUT)
    await runtime.scheduler._fire_due_schedules()
    assert sends == []  # an approval/question is not a safe idle point

    _set_status(runtime, "sess-1", SessionStatus.IDLE)
    await runtime.scheduler._fire_due_schedules()
    assert sends == [("sess-1", "later")]
    refreshed = runtime.storage.get_scheduled_message(record.id)
    assert refreshed is not None
    assert refreshed.status == ScheduledMessageStatus.SENT


@pytest.mark.asyncio
async def test_idle_not_treated_as_time_due(tmp_path, monkeypatch) -> None:
    # An idle record's enqueue timestamp is in the past, but the timed pass must
    # never fire it while the session is busy.
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    _set_status(runtime, "sess-1", SessionStatus.RUNNING)
    sends = _record_sends(runtime, monkeypatch)

    runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="held", trigger=ScheduledMessageTrigger.IDLE
        ),
    )
    await runtime.scheduler._fire_due_schedules()
    assert sends == []


@pytest.mark.asyncio
async def test_with_previous_batches_deliver_together(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    _set_status(runtime, "sess-1", SessionStatus.RUNNING)
    sends = _record_sends(runtime, monkeypatch)

    first = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="one",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )
    second = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="two",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )
    assert first.idle_batch == second.idle_batch  # shared batch

    _set_status(runtime, "sess-1", SessionStatus.IDLE)
    await runtime.scheduler._fire_due_schedules()
    # Both delivered in creation order at one idle point.
    assert sends == [("sess-1", "one"), ("sess-1", "two")]


@pytest.mark.asyncio
async def test_next_cycle_queues_behind_batch(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    _set_status(runtime, "sess-1", SessionStatus.RUNNING)
    sends = _record_sends(runtime, monkeypatch)

    imminent = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="first cycle",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )
    later = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="second cycle",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.NEXT_CYCLE,
        ),
    )
    assert imminent.idle_batch is not None
    assert later.idle_batch == imminent.idle_batch + 1

    _set_status(runtime, "sess-1", SessionStatus.IDLE)
    await runtime.scheduler._fire_due_schedules()
    # Only the imminent batch dispatches at this idle point.
    assert sends == [("sess-1", "first cycle")]
    behind = runtime.storage.get_scheduled_message(later.id)
    assert behind is not None
    assert behind.status == ScheduledMessageStatus.PENDING

    # The later batch is now armed to wait for a fresh transition; still idle is
    # not enough.
    await runtime.scheduler._fire_due_schedules()
    assert sends == [("sess-1", "first cycle")]

    # A later idle transition releases it.
    runtime.scheduler.notify_idle("sess-1", datetime.now(UTC) + timedelta(seconds=1))
    await runtime.scheduler._fire_due_schedules()
    assert sends == [("sess-1", "first cycle"), ("sess-1", "second cycle")]


@pytest.mark.asyncio
async def test_next_cycle_on_idle_waits_for_transition(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")  # IDLE
    runtime.storage.create_session(session)
    sends = _record_sends(runtime, monkeypatch)

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="next idle",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.NEXT_CYCLE,
        ),
    )
    assert record.wait_for_idle_transition is True

    # Currently idle from a prior turn is not a fresh transition.
    await runtime.scheduler._fire_due_schedules()
    assert sends == []

    # An idle event that predates the record does not release it.
    runtime.scheduler.notify_idle("sess-1", record.created_at - timedelta(seconds=1))
    await runtime.scheduler._fire_due_schedules()
    assert sends == []

    # A later idle transition does.
    runtime.scheduler.notify_idle("sess-1", record.created_at + timedelta(seconds=1))
    await runtime.scheduler._fire_due_schedules()
    assert sends == [("sess-1", "next idle")]


@pytest.mark.asyncio
async def test_separate_sessions_each_deliver_in_one_pass(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    for sid in ("sess-a", "sess-b"):
        runtime.storage.create_session(make_session(runtime.settings, sid))
    sends = _record_sends(runtime, monkeypatch)
    for sid in ("sess-a", "sess-b"):
        runtime.scheduler.create_message_schedule(
            sid,
            ScheduledMessageCreateRequest(
                text=f"hi {sid}",
                trigger=ScheduledMessageTrigger.IDLE,
                idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
            ),
        )
    await runtime.scheduler._fire_due_schedules()
    assert set(sends) == {("sess-a", "hi sess-a"), ("sess-b", "hi sess-b")}


def test_idle_rejects_timing_fields(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "sess-1"))
    for kwargs in (
        {"delay_seconds": 60},
        {"scheduled_at": datetime.now(UTC) + timedelta(minutes=5)},
        {"cron": "0 9 * * *", "timezone": "UTC"},
    ):
        with pytest.raises(HTTPException) as exc:
            runtime.scheduler.create_message_schedule(
                "sess-1",
                ScheduledMessageCreateRequest(
                    text="x", trigger=ScheduledMessageTrigger.IDLE, **kwargs
                ),
            )
        assert exc.value.status_code == 400


def test_idle_batch_mode_requires_idle_trigger(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "sess-1"))
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_message_schedule(
            "sess-1",
            ScheduledMessageCreateRequest(
                text="x",
                delay_seconds=60,
                idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
            ),
        )
    assert exc.value.status_code == 400


def test_idle_rejected_for_terminal_session(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "sess-1"))
    _set_status(runtime, "sess-1", SessionStatus.EXITED)
    with pytest.raises(HTTPException) as exc:
        runtime.scheduler.create_message_schedule(
            "sess-1",
            ScheduledMessageCreateRequest(
                text="x", trigger=ScheduledMessageTrigger.IDLE
            ),
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_idle_missing_session_marked_failed(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    now = datetime.now(UTC)
    runtime.storage.create_scheduled_message(
        ScheduledMessageRecord(
            id="msg-idle-orphan",
            session_id="gone",
            text="hi",
            scheduled_at=now,
            created_at=now,
            status=ScheduledMessageStatus.PENDING,
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch=1,
        )
    )
    await runtime.scheduler._fire_due_schedules()
    refreshed = runtime.storage.get_scheduled_message("msg-idle-orphan")
    assert refreshed is not None
    assert refreshed.status == ScheduledMessageStatus.FAILED
    assert refreshed.failure_reason == "session not found"


@pytest.mark.asyncio
async def test_idle_delivery_failure_arms_next_batch(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")  # IDLE
    runtime.storage.create_session(session)

    async def boom(_session_id, _request) -> SessionRecord:
        raise RuntimeError("send failed")

    monkeypatch.setattr(runtime, "handle_input", boom)

    failing = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="oops",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )
    queued = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="behind",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.NEXT_CYCLE,
        ),
    )
    await runtime.scheduler._fire_due_schedules()
    # The failing batch consumed the idle point; the next batch is armed to wait.
    failed = runtime.storage.get_scheduled_message(failing.id)
    assert failed is not None
    assert failed.status == ScheduledMessageStatus.FAILED
    behind = runtime.storage.get_scheduled_message(queued.id)
    assert behind is not None
    assert behind.status == ScheduledMessageStatus.PENDING
    assert behind.wait_for_idle_transition is True


def test_idle_legacy_row_defaults_to_time(tmp_path) -> None:
    # A row written before the trigger column existed reads back as time.
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    storage.connection.execute(
        """
        INSERT INTO scheduled_messages (id, session_id, text, submit,
            attachments, scheduled_at, created_at, status)
        VALUES ('legacy', 'sess-1', 'hi', 1, '[]', ?, ?, 'pending')
        """,
        (now.isoformat(), now.isoformat()),
    )
    storage.connection.commit()
    loaded = storage.get_scheduled_message("legacy")
    assert loaded is not None
    assert loaded.trigger == ScheduledMessageTrigger.TIME
    assert loaded.idle_batch is None
    assert loaded.wait_for_idle_transition is False


@pytest.mark.asyncio
async def test_idle_attachment_reference_retained_then_released(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    session = make_session(runtime.settings, "sess-1")
    runtime.storage.create_session(session)
    spec = runtime.attachments.save(
        "sess-1", data=b"hello", filename="note.txt", content_type="text/plain"
    )

    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="see file",
            attachments=[spec.id],
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.NEXT_CYCLE,
        ),
    )
    assert spec.id in runtime.attachments.schedule_referenced_ids("sess-1")

    runtime.scheduler.cancel_message_schedule(record.id)
    assert spec.id not in runtime.attachments.schedule_referenced_ids("sess-1")


def _capture_sends(runtime, monkeypatch) -> list[tuple[str, str, list[str] | None]]:
    sends: list[tuple[str, str, list[str] | None]] = []

    async def fake_handle_input(session_id: str, request) -> SessionRecord:
        sends.append((session_id, request.text, request.attachments))
        return runtime.get_session(session_id)

    monkeypatch.setattr(runtime, "handle_input", fake_handle_input)
    return sends


def _runtime_with_session(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.storage.create_session(make_session(runtime.settings, "sess-1"))
    return runtime


async def test_send_now_delivers_a_one_shot_and_marks_it_sent(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)
    sends = _capture_sends(runtime, monkeypatch)
    spec = runtime.attachments.save(
        "sess-1", data=b"hello", filename="note.txt", content_type="text/plain"
    )
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="ship it", attachments=[spec.id], delay_seconds=3600
        ),
    )

    sent, held = await runtime.scheduler.send_message_now(record.id)

    assert sent.status == ScheduledMessageStatus.SENT
    assert held is False
    assert sends == [("sess-1", "ship it", [spec.id])]
    # History reads the delivery time, not the time it was scheduled for.
    assert sent.scheduled_at <= datetime.now(UTC)
    assert spec.id not in runtime.attachments.schedule_referenced_ids("sess-1")


async def test_send_now_runs_one_recurring_occurrence_and_keeps_the_next_run(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)
    sends = _capture_sends(runtime, monkeypatch)
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="tick", cron="0 9 * * *", timezone="UTC"),
    )

    sent, _ = await runtime.scheduler.send_message_now(record.id)

    assert sends == [("sess-1", "tick", None)]
    assert sent.status == ScheduledMessageStatus.PENDING
    assert sent.scheduled_at == record.scheduled_at
    assert sent.last_run_status == ScheduledMessageStatus.SENT.value
    assert sent.last_run_at is not None


async def test_send_now_delivers_an_idle_message_and_arms_the_next_batch(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)
    runtime.storage.update_session("sess-1", status=SessionStatus.RUNNING)
    sends = _capture_sends(runtime, monkeypatch)
    first = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="first", trigger=ScheduledMessageTrigger.IDLE
        ),
    )
    second = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="second",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.NEXT_CYCLE,
        ),
    )

    sent, _ = await runtime.scheduler.send_message_now(first.id)

    assert sent.status == ScheduledMessageStatus.SENT
    assert sends == [("sess-1", "first", None)]
    # The session goes idle in the same cycle: the next batch waits for a fresh
    # idle transition instead of following the manual send at once.
    runtime.storage.update_session("sess-1", status=SessionStatus.IDLE)
    await runtime.scheduler._fire_due_schedules()
    waiting = runtime.storage.get_scheduled_message(second.id)
    assert waiting is not None
    assert waiting.status == ScheduledMessageStatus.PENDING
    assert waiting.wait_for_idle_transition is True
    assert sends == [("sess-1", "first", None)]


@pytest.mark.parametrize(
    "resolve",
    [
        {"status": ScheduledMessageStatus.SENT},
        {"status": ScheduledMessageStatus.CANCELLED},
        {"status": ScheduledMessageStatus.FAILED, "failure_reason": "boom"},
    ],
)
async def test_send_now_refuses_a_resolved_schedule(
    tmp_path, monkeypatch, resolve
) -> None:
    runtime = _runtime_with_session(tmp_path)
    sends = _capture_sends(runtime, monkeypatch)
    record = runtime.scheduler.create_message_schedule(
        "sess-1", ScheduledMessageCreateRequest(text="late", delay_seconds=60)
    )
    runtime.storage.update_scheduled_message(record.id, **resolve)

    with pytest.raises(HTTPException) as caught:
        await runtime.scheduler.send_message_now(record.id)

    assert caught.value.status_code == http.HTTP_409_CONFLICT
    assert caught.value.detail == f"message schedule is {resolve['status'].value}"
    assert sends == []


async def test_send_now_refuses_an_unknown_schedule(tmp_path) -> None:
    runtime = _runtime_with_session(tmp_path)

    with pytest.raises(HTTPException) as caught:
        await runtime.scheduler.send_message_now("msg-missing")

    assert caught.value.status_code == http.HTTP_404_NOT_FOUND


async def test_a_refused_send_now_leaves_the_schedule_pending(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)

    async def refuse(session_id: str, request) -> SessionRecord:
        raise HTTPException(
            status_code=http.HTTP_409_CONFLICT, detail="session is busy"
        )

    monkeypatch.setattr(runtime, "handle_input", refuse)
    record = runtime.scheduler.create_message_schedule(
        "sess-1", ScheduledMessageCreateRequest(text="hi", delay_seconds=60)
    )

    with pytest.raises(HTTPException) as caught:
        await runtime.scheduler.send_message_now(record.id)

    assert caught.value.status_code == http.HTTP_409_CONFLICT
    kept = runtime.storage.get_scheduled_message(record.id)
    assert kept is not None
    assert kept.status == ScheduledMessageStatus.PENDING
    assert kept.failure_reason is None


async def test_send_now_records_a_missing_session(tmp_path, monkeypatch) -> None:
    runtime = _runtime_with_session(tmp_path)
    _capture_sends(runtime, monkeypatch)
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="tick", cron="0 9 * * *", timezone="UTC"),
    )
    runtime.storage.delete_session("sess-1")

    with pytest.raises(HTTPException) as caught:
        await runtime.scheduler.send_message_now(record.id)

    assert caught.value.status_code == http.HTTP_404_NOT_FOUND
    kept = runtime.storage.get_scheduled_message(record.id)
    assert kept is not None
    assert kept.status == ScheduledMessageStatus.PENDING
    assert kept.last_run_status == ScheduledMessageStatus.FAILED.value
    assert kept.last_failure_reason == "session not found"


async def test_the_loop_skips_a_message_sent_now_since_its_pass_began(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)
    sends = _capture_sends(runtime, monkeypatch)
    record = runtime.scheduler.create_message_schedule(
        "sess-1", ScheduledMessageCreateRequest(text="once", delay_seconds=0)
    )
    runtime.storage.update_scheduled_message(
        record.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    listed = runtime.storage.list_scheduled_messages

    def list_then_send_now(*args, **kwargs):
        snapshot = listed(*args, **kwargs)
        # The pass has its snapshot; a send-now resolves the record meanwhile.
        runtime.storage.update_scheduled_message(
            record.id, status=ScheduledMessageStatus.SENT
        )
        return snapshot

    monkeypatch.setattr(runtime.storage, "list_scheduled_messages", list_then_send_now)
    await runtime.scheduler._fire_due_schedules()

    assert sends == []


async def test_a_timed_one_shot_releases_its_attachments_when_it_fires(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)
    _capture_sends(runtime, monkeypatch)
    spec = runtime.attachments.save(
        "sess-1", data=b"hello", filename="note.txt", content_type="text/plain"
    )
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="see", attachments=[spec.id], delay_seconds=0
        ),
    )
    runtime.storage.update_scheduled_message(
        record.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    await runtime.scheduler._fire_due_schedules()

    assert spec.id not in runtime.attachments.schedule_referenced_ids("sess-1")


@pytest.mark.parametrize(
    "timing",
    [{"delay_seconds": 60}, {"cron": "* * * * *", "timezone": "UTC"}],
    ids=["one-shot", "recurring"],
)
async def test_send_now_refuses_a_delivery_the_loop_has_in_flight(
    tmp_path, monkeypatch, timing
) -> None:
    runtime = _runtime_with_session(tmp_path)
    release = asyncio.Event()
    sends: list[str] = []

    async def slow_handle_input(session_id: str, request) -> SessionRecord:
        sends.append(request.text)
        if len(sends) == 1:
            await release.wait()
        return runtime.get_session(session_id)

    monkeypatch.setattr(runtime, "handle_input", slow_handle_input)
    record = runtime.scheduler.create_message_schedule(
        "sess-1", ScheduledMessageCreateRequest(text="once", **timing)
    )
    runtime.storage.update_scheduled_message(
        record.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    firing = asyncio.create_task(runtime.scheduler._fire_due_schedules())
    while not sends:
        await asyncio.sleep(0)
    try:
        with pytest.raises(HTTPException) as during_send:
            # An accepted send would queue behind the blocked one and never return.
            await asyncio.wait_for(runtime.scheduler.send_message_now(record.id), 5)
        with pytest.raises(HTTPException) as during_cancel:
            runtime.scheduler.cancel_message_schedule(record.id)
    finally:
        release.set()
        await firing

    assert during_send.value.status_code == http.HTTP_409_CONFLICT
    assert during_cancel.value.status_code == http.HTTP_409_CONFLICT
    assert sends == ["once"]


@pytest.mark.parametrize("by_hand", [False, True], ids=["loop", "send-now"])
@pytest.mark.parametrize(
    "timing",
    [{"delay_seconds": 60}, {"cron": "* * * * *", "timezone": "UTC"}],
    ids=["one-shot", "recurring"],
)
async def test_a_schedule_deleted_mid_delivery_ends_the_delivery_cleanly(
    tmp_path, monkeypatch, timing, by_hand
) -> None:
    runtime = _runtime_with_session(tmp_path)

    async def delete_while_sending(session_id: str, request) -> SessionRecord:
        await runtime.scheduler.purge_session_messages(session_id)
        return runtime.get_session(session_id)

    monkeypatch.setattr(runtime, "handle_input", delete_while_sending)
    record = runtime.scheduler.create_message_schedule(
        "sess-1", ScheduledMessageCreateRequest(text="once", **timing)
    )
    runtime.storage.update_scheduled_message(
        record.id, scheduled_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    if by_hand:
        await runtime.scheduler.send_message_now(record.id)
    else:
        await runtime.scheduler._fire_due_schedules()

    assert runtime.storage.get_scheduled_message(record.id) is None


@pytest.mark.parametrize("by_hand", [False, True], ids=["loop", "send-now"])
async def test_a_delivered_idle_message_records_when_it_ran(
    tmp_path, monkeypatch, by_hand
) -> None:
    runtime = _runtime_with_session(tmp_path)
    _capture_sends(runtime, monkeypatch)
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="after",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.WITH_PREVIOUS,
        ),
    )
    queued = datetime.now(UTC) - timedelta(hours=1)
    runtime.storage.update_scheduled_message(record.id, scheduled_at=queued)

    if by_hand:
        await runtime.scheduler.send_message_now(record.id)
    else:
        await runtime.scheduler._fire_due_schedules()

    sent = runtime.storage.get_scheduled_message(record.id)
    assert sent is not None
    assert sent.status == ScheduledMessageStatus.SENT
    assert sent.scheduled_at > queued + timedelta(minutes=59)


async def test_send_now_on_a_due_recurrence_counts_as_that_run(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)
    sends = _capture_sends(runtime, monkeypatch)
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="tick", cron="* * * * *", timezone="UTC"),
    )
    due = datetime.now(UTC) - timedelta(seconds=1)
    runtime.storage.update_scheduled_message(record.id, scheduled_at=due)

    sent, _ = await runtime.scheduler.send_message_now(record.id)
    await runtime.scheduler._fire_due_schedules()

    assert sends == [("sess-1", "tick", None)]
    assert sent.scheduled_at > due


async def test_a_refused_send_now_keeps_a_due_occurrence_due(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)

    async def refuse(session_id: str, request) -> SessionRecord:
        raise HTTPException(
            status_code=http.HTTP_409_CONFLICT, detail="session is busy"
        )

    monkeypatch.setattr(runtime, "handle_input", refuse)
    record = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(text="tick", cron="* * * * *", timezone="UTC"),
    )
    due = datetime.now(UTC) - timedelta(seconds=1)
    runtime.storage.update_scheduled_message(record.id, scheduled_at=due)

    with pytest.raises(HTTPException):
        await runtime.scheduler.send_message_now(record.id)

    kept = runtime.storage.get_scheduled_message(record.id)
    assert kept is not None
    assert kept.scheduled_at == due
    assert kept.last_run_at is None


@pytest.mark.parametrize(
    "timing",
    [
        {"delay_seconds": 60},
        {
            "trigger": ScheduledMessageTrigger.IDLE,
            "idle_batch_mode": IdleMessageBatchMode.WITH_PREVIOUS,
        },
    ],
    ids=["timed", "idle"],
)
async def test_one_message_failing_does_not_stop_the_pass(
    tmp_path, monkeypatch, timing
) -> None:
    runtime = _runtime_with_session(tmp_path)
    sends = _capture_sends(runtime, monkeypatch)
    broken, healthy = (
        runtime.scheduler.create_message_schedule(
            "sess-1", ScheduledMessageCreateRequest(text=text, **timing)
        )
        for text in ("broken", "healthy")
    )
    if "delay_seconds" in timing:
        for record, seconds_ago in ((broken, 2), (healthy, 1)):
            runtime.storage.update_scheduled_message(
                record.id,
                scheduled_at=datetime.now(UTC) - timedelta(seconds=seconds_ago),
            )
    update = runtime.storage.update_scheduled_message

    def update_unless_broken(message_id: str, **fields: Any):
        if message_id == broken.id:
            raise RuntimeError("disk I/O error")
        return update(message_id, **fields)

    monkeypatch.setattr(
        runtime.storage, "update_scheduled_message", update_unless_broken
    )

    await runtime.scheduler._fire_due_schedules()
    await runtime.scheduler._fire_due_schedules()

    # The one whose outcome was lost stays pending without being sent again.
    assert [text for _, text, _ in sends] == ["broken", "healthy"]
    done = runtime.storage.get_scheduled_message(healthy.id)
    assert done is not None
    assert done.status == ScheduledMessageStatus.SENT


async def test_send_now_reports_a_message_held_by_focus(tmp_path, monkeypatch) -> None:
    runtime = _runtime_with_session(tmp_path)
    sends = _capture_sends(runtime, monkeypatch)
    runtime.storage.update_session("sess-1", focus=True)
    record = runtime.scheduler.create_message_schedule(
        "sess-1", ScheduledMessageCreateRequest(text="later", delay_seconds=60)
    )

    sent, held = await runtime.scheduler.send_message_now(record.id)

    assert held is True
    assert sent.status == ScheduledMessageStatus.SENT
    assert sends == []


async def test_an_idle_batch_cancelled_mid_pass_does_not_consume_the_idle_point(
    tmp_path, monkeypatch
) -> None:
    runtime = _runtime_with_session(tmp_path)
    runtime.storage.update_session("sess-1", status=SessionStatus.RUNNING)
    sends = _capture_sends(runtime, monkeypatch)
    first = runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="first", trigger=ScheduledMessageTrigger.IDLE
        ),
    )
    runtime.scheduler.create_message_schedule(
        "sess-1",
        ScheduledMessageCreateRequest(
            text="second",
            trigger=ScheduledMessageTrigger.IDLE,
            idle_batch_mode=IdleMessageBatchMode.NEXT_CYCLE,
        ),
    )
    runtime.storage.update_session("sess-1", status=SessionStatus.IDLE)
    listed = runtime.storage.list_pending_idle_messages

    def list_then_cancel():
        snapshot = listed()
        runtime.storage.update_scheduled_message(
            first.id, status=ScheduledMessageStatus.CANCELLED
        )
        return snapshot

    monkeypatch.setattr(runtime.storage, "list_pending_idle_messages", list_then_cancel)
    await runtime.scheduler._fire_due_schedules()
    monkeypatch.setattr(runtime.storage, "list_pending_idle_messages", listed)
    await runtime.scheduler._fire_due_schedules()

    assert sends == [("sess-1", "second", None)]
