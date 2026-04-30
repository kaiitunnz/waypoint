from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from waypoint.schemas import (
    Backend,
    EventKind,
    EventRecord,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
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
        launch_target_id="devbox",
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
    assert loaded.cwd == "/tmp"
    assert loaded.launch_target_id == "devbox"
    assert loaded.status == SessionStatus.RUNNING
    events = storage.list_events("session-1")
    assert len(events) == 1


def test_storage_round_trips_pinned_at(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-pin",
        backend=Backend.CODEX,
        source=SessionSource.MANAGED,
        title="Codex session",
        cwd="/tmp",
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )
    storage.create_session(session)

    loaded = storage.get_session("session-pin")
    assert loaded is not None
    assert loaded.pinned_at is None

    pinned = storage.update_session("session-pin", pinned_at=now)
    assert pinned.pinned_at == now

    unpinned = storage.update_session("session-pin", pinned_at=None)
    assert unpinned.pinned_at is None


def _seed_session_with_events(
    tmp_path,
    *,
    session_id: str = "sess",
    count: int = 12,
) -> Storage:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend=Backend.CODEX,
            source=SessionSource.MANAGED,
            title="seeded",
            cwd="/tmp",
            status=SessionStatus.RUNNING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )
    for index in range(count):
        storage.append_event(
            EventRecord(
                session_id=session_id,
                ts=now + timedelta(seconds=index),
                kind=EventKind.AGENT_OUTPUT,
                text=f"chunk-{index}",
                sequence=index + 1,
            )
        )
    return storage


def test_list_events_tail_limit_returns_latest_in_ascending_order(tmp_path) -> None:
    storage = _seed_session_with_events(tmp_path, count=10)
    tail = storage.list_events("sess", limit=4)
    assert [event.text for event in tail] == [
        "chunk-6",
        "chunk-7",
        "chunk-8",
        "chunk-9",
    ]


def test_list_events_before_sequence_returns_window_in_ascending_order(
    tmp_path,
) -> None:
    storage = _seed_session_with_events(tmp_path, count=10)
    older = storage.list_events("sess", limit=3, before_sequence=7)
    # sequence < 7 → 1..6; tail of 3 → 4..6 in ascending order.
    assert [event.sequence for event in older] == [4, 5, 6]


def test_list_events_no_limit_returns_full_history(tmp_path) -> None:
    storage = _seed_session_with_events(tmp_path, count=5)
    assert [event.sequence for event in storage.list_events("sess")] == [1, 2, 3, 4, 5]


def test_list_events_cursor_takes_precedence_over_tail_params(tmp_path) -> None:
    # Cursor-after semantics is the legacy reconnect/catch-up path; if both
    # `cursor` and `limit` are passed we keep the historical "all newer
    # rows, no clamp" behavior so existing callers aren't quietly truncated.
    storage = _seed_session_with_events(tmp_path, count=6)
    rows = storage.list_events("sess")
    second_id = rows[1].id
    assert second_id is not None
    catchup = storage.list_events("sess", cursor=second_id, limit=2)
    assert [event.sequence for event in catchup] == [3, 4, 5, 6]


def test_count_events_before_sequence(tmp_path) -> None:
    storage = _seed_session_with_events(tmp_path, count=5)
    assert storage.count_events_before_sequence("sess", 3) == 2
    assert storage.count_events_before_sequence("sess", 1) == 0
    assert storage.count_events_before_sequence("sess", 99) == 5


def test_storage_serializes_concurrent_threads(tmp_path) -> None:
    # Regression: FastAPI dispatches sync deps onto a threadpool, so the auth
    # path could call ``get_token_expiry`` on the shared connection from many
    # threads simultaneously and trip
    # ``sqlite3.InterfaceError: bad parameter or other API misuse``.
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    expires = now + timedelta(days=30)
    tokens = [f"tok-{i:03d}" for i in range(32)]
    for token in tokens:
        storage.insert_token(token, expires)

    def hammer(token: str) -> datetime | None:
        for _ in range(64):
            storage.get_token_expiry(token)
            storage.refresh_token_expiry(token, expires)
        return storage.get_token_expiry(token)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(hammer, tokens))

    assert all(result is not None for result in results)
