from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    ScheduledMessageCreateRequest,
    ScheduledMessageRecord,
    ScheduledMessageStatus,
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
    assert refreshed.failure_reason == "404: session not found"


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
