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


def _seed_session(
    tmp_path,
    *,
    session_id: str = "sess",
) -> tuple[Storage, datetime]:
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
    return storage, now


def _append(
    storage: Storage,
    session_id: str,
    *,
    sequence: int,
    kind: EventKind,
    text: str,
    ts: datetime,
    item_id: str | None = None,
) -> None:
    metadata: dict[str, object] = {}
    if item_id is not None:
        metadata["item_id"] = item_id
    storage.append_event(
        EventRecord(
            session_id=session_id,
            ts=ts,
            kind=kind,
            text=text,
            metadata=metadata,
            sequence=sequence,
        )
    )


def test_list_events_returns_full_history_in_ascending_order(tmp_path) -> None:
    storage, now = _seed_session(tmp_path)
    for i in range(5):
        _append(
            storage,
            "sess",
            sequence=i + 1,
            kind=EventKind.AGENT_OUTPUT,
            text=f"chunk-{i}",
            ts=now + timedelta(seconds=i),
        )
    assert [event.sequence for event in storage.list_events("sess")] == [1, 2, 3, 4, 5]


def test_list_events_with_cursor_returns_only_newer_rows(tmp_path) -> None:
    # Cursor-after semantics is the legacy reconnect/catch-up path: return
    # everything strictly newer than the supplied id, no clamp.
    storage, now = _seed_session(tmp_path)
    for i in range(6):
        _append(
            storage,
            "sess",
            sequence=i + 1,
            kind=EventKind.AGENT_OUTPUT,
            text=f"chunk-{i}",
            ts=now + timedelta(seconds=i),
        )
    rows = storage.list_events("sess")
    second_id = rows[1].id
    assert second_id is not None
    catchup = storage.list_events("sess", cursor=second_id)
    assert [event.sequence for event in catchup] == [3, 4, 5, 6]


def test_list_events_by_message_count_groups_agent_deltas_by_item_id(
    tmp_path,
) -> None:
    # A single Codex agent reply streams as many same-item_id deltas. The
    # paginator must treat the run as one logical message so a tail of 1
    # reliably captures the entire reply.
    storage, now = _seed_session(tmp_path)
    for i in range(20):
        _append(
            storage,
            "sess",
            sequence=i + 1,
            kind=EventKind.AGENT_OUTPUT,
            text=f"d{i}",
            ts=now + timedelta(seconds=i),
            item_id="msg-A",
        )
    page = storage.list_events_by_message_count("sess", message_limit=1)
    assert [event.sequence for event in page] == list(range(1, 21))


def test_list_events_by_message_count_pairs_tool_call_with_results(tmp_path) -> None:
    # tool_call + tool_result events sharing item_id render as one tool
    # pair (frontend buildTranscriptItems), so they share a key and count
    # as one logical message.
    storage, now = _seed_session(tmp_path)
    _append(
        storage,
        "sess",
        sequence=1,
        kind=EventKind.TOOL_CALL,
        text="ls",
        ts=now,
        item_id="call-A",
    )
    for i in range(3):
        _append(
            storage,
            "sess",
            sequence=2 + i,
            kind=EventKind.TOOL_RESULT,
            text=f"out{i}",
            ts=now + timedelta(seconds=i + 1),
            item_id="call-A",
        )
    page = storage.list_events_by_message_count("sess", message_limit=1)
    assert [event.sequence for event in page] == [1, 2, 3, 4]


def test_list_events_by_message_count_only_anchors_consume_budget(
    tmp_path,
) -> None:
    # Only anchor kinds (user/agent/tool/approval) consume the page budget;
    # bookkeeping system_notes ride along free so the count tracks visible
    # bubbles, not raw chattiness.
    storage, now = _seed_session(tmp_path)
    _append(
        storage,
        "sess",
        sequence=1,
        kind=EventKind.USER_INPUT,
        text="hi",
        ts=now,
    )
    # Codex-style bookkeeping noise around the agent reply.
    _append(
        storage,
        "sess",
        sequence=2,
        kind=EventKind.SYSTEM_NOTE,
        text="Started reasoning",
        ts=now + timedelta(seconds=1),
    )
    for i in range(3):
        _append(
            storage,
            "sess",
            sequence=3 + i,
            kind=EventKind.AGENT_OUTPUT,
            text=f"d{i}",
            ts=now + timedelta(seconds=i + 2),
            item_id="msg-A",
        )
    _append(
        storage,
        "sess",
        sequence=6,
        kind=EventKind.SYSTEM_NOTE,
        text="Turn completed",
        ts=now + timedelta(seconds=6),
    )
    # 2 anchors = user_input + agent run. Both system_notes ride along.
    page = storage.list_events_by_message_count("sess", message_limit=2)
    assert [event.sequence for event in page] == [1, 2, 3, 4, 5, 6]
    # 1 anchor = just the agent run. Both system_notes ride along (the
    # backward walk doesn't break on non-anchors so the leading
    # "Started reasoning" stays attached to the agent run); user_input
    # falls off the page.
    page = storage.list_events_by_message_count("sess", message_limit=1)
    assert [event.sequence for event in page] == [2, 3, 4, 5, 6]


def test_list_events_by_message_count_dedupes_non_contiguous_anchor_keys(
    tmp_path,
) -> None:
    # Codex interleaves events for concurrent tool calls, so the same
    # item_id can appear as multiple non-contiguous runs in sequence
    # order. The frontend coalesces them into one bubble (mergeEvents
    # matches by item_id regardless of position), so the paginator must
    # too — otherwise an N-message page comes up short of N visible
    # bubbles.
    storage, now = _seed_session(tmp_path)
    # Tool A starts, B starts and finishes, A finishes — A's events end
    # up split across sequences 1 and 4-5.
    _append(
        storage,
        "sess",
        sequence=1,
        kind=EventKind.TOOL_CALL,
        text="ls",
        ts=now,
        item_id="call-A",
    )
    _append(
        storage,
        "sess",
        sequence=2,
        kind=EventKind.TOOL_CALL,
        text="cat",
        ts=now + timedelta(seconds=1),
        item_id="call-B",
    )
    _append(
        storage,
        "sess",
        sequence=3,
        kind=EventKind.TOOL_RESULT,
        text="hello",
        ts=now + timedelta(seconds=2),
        item_id="call-B",
    )
    _append(
        storage,
        "sess",
        sequence=4,
        kind=EventKind.TOOL_RESULT,
        text="out",
        ts=now + timedelta(seconds=3),
        item_id="call-A",
    )
    _append(
        storage,
        "sess",
        sequence=5,
        kind=EventKind.TOOL_RESULT,
        text="done",
        ts=now + timedelta(seconds=4),
        item_id="call-A",
    )
    # 2 visible bubbles total (tool A, tool B). A page of 2 should
    # surface every event, even though A appears as two contiguous runs.
    page = storage.list_events_by_message_count("sess", message_limit=2)
    assert [event.sequence for event in page] == [1, 2, 3, 4, 5]


def test_list_events_by_message_count_before_sequence_returns_older_window(
    tmp_path,
) -> None:
    # Mirrors a "Load older" call: paginate strictly before a cursor.
    storage, now = _seed_session(tmp_path)
    for i in range(6):
        _append(
            storage,
            "sess",
            sequence=i + 1,
            kind=EventKind.USER_INPUT,
            text=f"u{i}",
            ts=now + timedelta(seconds=i),
        )
    page = storage.list_events_by_message_count(
        "sess", message_limit=2, before_sequence=5
    )
    # sequences < 5 → 1..4; latest 2 messages (each user_input is its own
    # message) → 3, 4 in ascending order.
    assert [event.sequence for event in page] == [3, 4]


def test_has_events_before_sequence(tmp_path) -> None:
    storage, now = _seed_session(tmp_path)
    for i in range(3):
        _append(
            storage,
            "sess",
            sequence=i + 1,
            kind=EventKind.AGENT_OUTPUT,
            text=f"d{i}",
            ts=now + timedelta(seconds=i),
        )
    assert storage.has_events_before_sequence("sess", 1) is False
    assert storage.has_events_before_sequence("sess", 2) is True
    assert storage.has_events_before_sequence("sess", 99) is True


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
