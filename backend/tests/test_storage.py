from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from waypoint.schemas import (
    EventKind,
    EventRecord,
    LaunchMode,
    ScheduledSessionRecord,
    ScheduleStatus,
    SessionContextUsage,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
    UsageWindow,
)
from waypoint.storage import Storage


def test_storage_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-1",
        backend="codex",
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
        context_usage=SessionContextUsage(
            used_tokens=2048,
            context_window_tokens=8192,
            updated_at=now,
            source="codex",
            breakdown={"input_tokens": 1024, "output_tokens": 1024},
        ),
        rate_limit_usage=SessionRateLimitUsage(
            source="claude_code",
            updated_at=now,
            windows=[
                UsageWindow(
                    id="five_hour",
                    label="5h",
                    used_percent=42.0,
                    used_tokens=420,
                    remaining_tokens=580,
                    limit_tokens=1000,
                )
            ],
            notes=["CLI creds"],
        ),
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
    assert loaded.context_usage is not None
    assert loaded.context_usage.used_tokens == 2048
    assert loaded.context_usage.context_window_tokens == 8192
    assert loaded.context_usage.breakdown == {
        "input_tokens": 1024,
        "output_tokens": 1024,
    }
    assert loaded.rate_limit_usage is not None
    assert loaded.rate_limit_usage.source == "claude_code"
    assert loaded.rate_limit_usage.windows[0].label == "5h"
    assert loaded.rate_limit_usage.windows[0].used_percent == 42.0
    assert loaded.rate_limit_usage.windows[0].remaining_tokens == 580
    events = storage.list_events("session-1")
    assert len(events) == 1


def test_storage_round_trips_pinned_at(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-pin",
        backend="codex",
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


def test_schedule_round_trip_persists_launch_mode(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    schedule = ScheduledSessionRecord(
        id="schedule-1",
        backend="codex",
        cwd="/tmp/project",
        launch_mode=LaunchMode.TMUX_WRAPPER,
        title="Nightly run",
        args=["--dangerously-skip-permissions"],
        config_overrides=["model_reasoning_effort=high"],
        initial_prompt="Ship it",
        permission_mode="full_access",
        model="gpt-4.1",
        effort="high",
        scheduled_at=now + timedelta(minutes=15),
        created_at=now,
        status=ScheduleStatus.PENDING,
    )
    storage.create_schedule(schedule)

    loaded = storage.get_schedule(schedule.id)
    assert loaded is not None
    assert loaded.launch_mode == LaunchMode.TMUX_WRAPPER
    assert loaded.args == ["--dangerously-skip-permissions"]
    assert loaded.config_overrides == ["model_reasoning_effort=high"]
    assert storage.list_schedules()[0].launch_mode == LaunchMode.TMUX_WRAPPER


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
            backend="codex",
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


def test_list_events_by_message_count_caps_anchorless_pages(tmp_path) -> None:
    # Sessions with no anchor events at all (tmux raw_terminal_chunk
    # streams, system_note-only history after a cold restart) would
    # otherwise pull the entire transcript per page since nothing
    # increments the message counter. The hard event cap keeps payload
    # bounded and surfaces ``has_more`` so the caller can paginate.
    storage, now = _seed_session(tmp_path)
    for i in range(2500):
        _append(
            storage,
            "sess",
            sequence=i + 1,
            kind=EventKind.SYSTEM_NOTE,
            text=f"note-{i}",
            ts=now + timedelta(seconds=i),
        )
    page = storage.list_events_by_message_count("sess", message_limit=20)
    assert len(page) == 2000  # _MAX_EVENTS_PER_PAGE
    # Cap was hit walking DESC from sequence 2500; the page covers the
    # 2000 newest events (501..2500 in ASC order).
    assert page[0].sequence == 501
    assert page[-1].sequence == 2500
    assert storage.has_events_before_sequence("sess", page[0].sequence) is True


def test_list_events_by_message_count_does_not_truncate_a_single_anchor(
    tmp_path,
) -> None:
    # The event cap is a safety net for anchorless walks; a real
    # logical message must come through whole even if it has more raw
    # events than the cap. Otherwise the chat view paints a chopped-off
    # Codex reply (2500-delta agent_output) and the user has to "Load
    # older" to reassemble one bubble — which contradicts the
    # paginator's contract.
    storage, now = _seed_session(tmp_path)
    for i in range(2500):
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
    # All 2500 events belong to the single anchor and must arrive
    # together, even though that exceeds _MAX_EVENTS_PER_PAGE.
    assert len(page) == 2500
    assert page[0].sequence == 1
    assert page[-1].sequence == 2500


def test_list_events_by_message_count_caps_at_anchor_boundary(tmp_path) -> None:
    # When pages span multiple anchors and the cumulative event count
    # crosses the cap, stop at the next *anchor boundary* rather than
    # in the middle of a message.
    storage, now = _seed_session(tmp_path)
    seq = 0
    # 1500-event anchor A (newer), then 1500-event anchor B (older).
    for i in range(1500):
        seq += 1
        _append(
            storage,
            "sess",
            sequence=seq,
            kind=EventKind.AGENT_OUTPUT,
            text=f"b{i}",
            ts=now + timedelta(seconds=seq),
            item_id="msg-B",
        )
    for i in range(1500):
        seq += 1
        _append(
            storage,
            "sess",
            sequence=seq,
            kind=EventKind.AGENT_OUTPUT,
            text=f"a{i}",
            ts=now + timedelta(seconds=seq),
            item_id="msg-A",
        )
    page = storage.list_events_by_message_count("sess", message_limit=10)
    # A fits whole (1500 events). The walk would cross into B at event
    # 1501, but cap (>= 2000) hasn't been reached yet, so B starts
    # collecting too. After all 1500 of B's events the cap is at 3000
    # > 2000, but we already finished B before the next anchor — page
    # contains everything (3000 events) and has_more=False.
    assert len(page) == 3000


def test_list_events_by_message_count_caps_before_third_anchor(tmp_path) -> None:
    # With three large anchors, the cap kicks in at the boundary into
    # the third (cumulative > 2000), keeping the first two whole.
    storage, now = _seed_session(tmp_path)
    seq = 0
    for label in ("c", "b", "a"):  # oldest to newest, so DESC order is a, b, c
        for i in range(1500):
            seq += 1
            _append(
                storage,
                "sess",
                sequence=seq,
                kind=EventKind.AGENT_OUTPUT,
                text=f"{label}{i}",
                ts=now + timedelta(seconds=seq),
                item_id=f"msg-{label.upper()}",
            )
    page = storage.list_events_by_message_count("sess", message_limit=10)
    # DESC walk: A whole (1500), B starts (cap not yet hit), B whole
    # (cumulative 3000 >= 2000). Trying to cross into C: cap check
    # fires, break before adding any C events.
    assert len(page) == 3000
    assert page[0].text == "b0"  # oldest in page
    assert page[-1].text == "a1499"  # newest in page


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


def test_transport_state_round_trip(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-state",
        backend="codex",
        source=SessionSource.MANAGED,
        title="Codex session",
        cwd="/tmp",
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        transport_state={"thread_id": "tid-42", "extra": {"nested": True}},
    )
    storage.create_session(session)
    loaded = storage.get_session("session-state")
    assert loaded is not None
    assert loaded.transport_state == {"thread_id": "tid-42", "extra": {"nested": True}}

    updated = storage.update_session(
        "session-state", transport_state={"thread_id": "tid-43"}
    )
    assert updated.transport_state == {"thread_id": "tid-43"}


def test_session_with_empty_transport_state_round_trips_as_empty(tmp_path) -> None:
    """A row whose ``transport_state`` is an explicit empty JSON object
    must load as an empty dict (no implicit reconstruction from
    sibling columns now that the per-plugin typed columns are gone)."""
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-empty",
        backend="codex",
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
    storage.connection.execute(
        "UPDATE sessions SET transport_state = '{}' WHERE id = ?",
        ("session-empty",),
    )
    storage.connection.commit()
    loaded = storage.get_session("session-empty")
    assert loaded is not None
    assert loaded.transport_state == {}


def test_append_event_stamps_envelope_version(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-evt",
        backend="codex",
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
    persisted = storage.append_event(
        EventRecord(
            session_id="session-evt",
            ts=now,
            kind=EventKind.AGENT_OUTPUT,
            text="hello",
            metadata={"item_id": "msg-1"},
            sequence=1,
        )
    )
    assert persisted.metadata["version"] == 1
    assert persisted.metadata["item_id"] == "msg-1"

    explicit = storage.append_event(
        EventRecord(
            session_id="session-evt",
            ts=now,
            kind=EventKind.AGENT_OUTPUT,
            text="world",
            metadata={"version": 2, "item_id": "msg-2"},
            sequence=2,
        )
    )
    # Existing version isn't clobbered — replay paths can opt into a
    # newer schema without storage rewriting them.
    assert explicit.metadata["version"] == 2
