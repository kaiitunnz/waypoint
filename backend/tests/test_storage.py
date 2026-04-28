from datetime import UTC, datetime

from waypoint.schemas import Backend, EventKind, EventRecord, SessionRecord, SessionSource, SessionStatus
from waypoint.storage import Storage


def test_storage_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-1",
        backend=Backend.CODEX,
        source=SessionSource.MANAGED,
        title="Codex session",
        cwd="/tmp",
        remote_cwd="~/workspace",
        status=SessionStatus.STARTING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )
    storage.create_session(session)
    event = EventRecord(
        session_id="session-1",
        ts=now,
        kind=EventKind.USER_INPUT,
        text="hello",
        metadata={"status": SessionStatus.RUNNING},
        sequence=1,
    )
    persisted = storage.append_event(event)
    assert persisted.id is not None
    loaded = storage.get_session("session-1")
    assert loaded is not None
    assert loaded.remote_cwd == "~/workspace"
    assert loaded.status == SessionStatus.RUNNING
    events = storage.list_events("session-1")
    assert len(events) == 1
