from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

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


def _make_session(storage: Storage, session_id: str, title: str) -> None:
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
            source=SessionSource.MANAGED,
            title=title,
            cwd="/tmp",
            status=SessionStatus.EXITED,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )


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


def test_session_round_trip_persists_launch_mode(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="session-lm",
        backend="codex",
        source=SessionSource.MANAGED,
        title="Codex via tmux",
        cwd="/tmp",
        launch_mode=LaunchMode.TMUX_WRAPPER,
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )
    storage.create_session(session)

    loaded = storage.get_session("session-lm")
    assert loaded is not None
    assert loaded.launch_mode == LaunchMode.TMUX_WRAPPER


def test_session_round_trip_persists_spawner_session_id(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    session = SessionRecord(
        id="child-1",
        backend="claude_code",
        source=SessionSource.MANAGED,
        title="Child",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        spawner_session_id="parent-1",
    )
    storage.create_session(session)

    loaded = storage.get_session("child-1")
    assert loaded is not None
    assert loaded.spawner_session_id == "parent-1"

    updated = storage.update_session("child-1", spawner_session_id="parent-2")
    assert updated.spawner_session_id == "parent-2"


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


def test_board_append_log_is_ordered_and_keyless(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    first = storage.add_board_entry("topic:plan", "hello", author_session_id="s1")
    second = storage.add_board_entry("topic:plan", "world", author_session_id="s2")
    assert first.key is None
    assert second.id > first.id
    entries = storage.list_board_entries("topic:plan")
    assert [e.text for e in entries] == ["hello", "world"]
    assert [e.author_session_id for e in entries] == ["s1", "s2"]


def test_board_keyed_entry_upserts_in_place(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    created = storage.add_board_entry(
        "team:p1", "draft", key="status", metadata={"v": 1}
    )
    updated = storage.add_board_entry(
        "team:p1", "final", key="status", metadata={"v": 2}
    )
    # Same cell, stable id, latest value wins.
    assert updated.id == created.id
    rows = storage.list_board_entries("team:p1", key="status")
    assert len(rows) == 1
    assert rows[0].text == "final"
    assert rows[0].metadata == {"v": 2}


def test_board_keyed_and_keyless_coexist_per_channel(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    storage.add_board_entry("topic:x", "log-a")
    storage.add_board_entry("topic:x", "log-b")
    storage.add_board_entry("topic:x", "val", key="k1")
    storage.add_board_entry("topic:x", "val2", key="k2")
    assert len(storage.list_board_entries("topic:x")) == 4
    keyed = storage.list_board_entries("topic:x", key="k1")
    assert [e.text for e in keyed] == ["val"]


def test_board_list_entries_since_cursor(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    a = storage.add_board_entry("topic:c", "a")
    storage.add_board_entry("topic:c", "b")
    storage.add_board_entry("topic:c", "c")
    fresh = storage.list_board_entries("topic:c", since=a.id)
    assert [e.text for e in fresh] == ["b", "c"]


def test_board_read_channel_caps_log_and_keeps_all_cells(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    for i in range(5):
        storage.add_board_entry("topic:x", f"log-{i}")
    storage.add_board_entry("topic:x", "v1", key="k1")
    storage.add_board_entry("topic:x", "v2", key="k2")

    entries, log_total = storage.read_board_channel("topic:x", log_limit=3)
    cells = [e for e in entries if e.key]
    log = [e for e in entries if not e.key]
    assert log_total == 5
    # Every cell is kept; only the newest 3 log rows, oldest-first.
    assert {e.key for e in cells} == {"k1", "k2"}
    assert [e.text for e in log] == ["log-2", "log-3", "log-4"]


def test_board_read_channel_pages_older_with_before(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    ids = [storage.add_board_entry("topic:x", f"log-{i}").id for i in range(5)]
    storage.add_board_entry("topic:x", "v1", key="k1")

    first, log_total = storage.read_board_channel("topic:x", log_limit=2)
    assert log_total == 5
    first_log = [e for e in first if not e.key]
    assert [e.text for e in first_log] == ["log-3", "log-4"]

    # Page older than the oldest log row returned; cells omitted on older pages.
    older, _ = storage.read_board_channel(
        "topic:x", log_limit=2, before=first_log[0].id
    )
    assert [e for e in older if e.key] == []
    assert [e.text for e in older] == ["log-1", "log-2"]
    assert first_log[0].id == ids[3]


def test_board_list_channels_summarizes(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    storage.add_board_entry("topic:a", "1")
    storage.add_board_entry("topic:a", "2")
    storage.add_board_entry("topic:b", "1")
    channels = {c.channel: c for c in storage.list_board_channels()}
    assert channels["topic:a"].entry_count == 2
    assert channels["topic:b"].entry_count == 1


def test_board_clear_channel_removes_only_that_channel(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    storage.add_board_entry("topic:a", "1")
    storage.add_board_entry("topic:a", "2")
    storage.add_board_entry("topic:b", "1")
    removed = storage.clear_board_channel("topic:a")
    assert removed == 2
    assert storage.list_board_entries("topic:a") == []
    assert len(storage.list_board_entries("topic:b")) == 1


def test_board_clear_keeps_the_channel_registered(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    storage.add_board_entry("topic:a", "1", key="k1")
    storage.add_board_entry("topic:a", "2")
    storage.clear_board_channel("topic:a")
    channels = {c.channel: c for c in storage.list_board_channels()}
    # The emptied channel survives with zero entries.
    assert "topic:a" in channels
    assert channels["topic:a"].entry_count == 0


def test_board_delete_channel_removes_it_from_the_list(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    storage.add_board_entry("topic:a", "1")
    storage.add_board_entry("topic:b", "1")
    removed = storage.delete_board_channel("topic:a")
    assert removed == 1
    channels = {c.channel for c in storage.list_board_channels()}
    assert channels == {"topic:b"}
    assert storage.list_board_entries("topic:a") == []


def test_board_prune_for_session_drops_keyed_cells_keeps_log(tmp_path) -> None:
    # Keyed cells authored by s1 are pruned; keyless log posts survive.
    storage = Storage(tmp_path / "waypoint.db")
    log_post = storage.add_board_entry("topic:a", "log-mine", author_session_id="s1")
    cell = storage.add_board_entry(
        "topic:a", "cell-mine", key="k", author_session_id="s1"
    )
    storage.add_board_entry("topic:a", "theirs", author_session_id="s2")
    storage.add_board_entry("topic:a", "anon")
    removed = storage.prune_board_for_session("s1")
    assert removed == 1  # only the keyed cell
    remaining = storage.list_board_entries("topic:a")
    ids = [e.id for e in remaining]
    assert log_post.id in ids
    assert cell.id not in ids


def test_board_delete_entry_removes_one_post(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    a = storage.add_board_entry("topic:a", "keep")
    b = storage.add_board_entry("topic:a", "drop")
    cell = storage.add_board_entry("topic:a", "v", key="k")
    assert storage.delete_board_entry("topic:a", b.id) is True
    assert storage.delete_board_entry("topic:a", cell.id) is True
    remaining = storage.list_board_entries("topic:a")
    assert [e.id for e in remaining] == [a.id]


def test_board_delete_entry_wrong_channel_is_noop(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    entry = storage.add_board_entry("topic:a", "hi")
    assert storage.delete_board_entry("topic:b", entry.id) is False
    assert len(storage.list_board_entries("topic:a")) == 1


def test_board_update_entry_edits_text_and_metadata(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    entry = storage.add_board_entry("topic:a", "old", metadata={"v": 1})
    updated = storage.update_board_entry("topic:a", entry.id, "new", metadata={"v": 2})
    assert updated is not None
    assert updated.text == "new"
    assert updated.metadata == {"v": 2}
    # The original post time is preserved; an edit stamp is added.
    assert updated.created_at == entry.created_at
    assert updated.edited_at is not None


def test_board_update_entry_missing_returns_none(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    entry = storage.add_board_entry("topic:a", "hi")
    assert storage.update_board_entry("topic:a", 99999, "x") is None
    # Wrong channel is also a no-op.
    assert storage.update_board_entry("topic:b", entry.id, "x") is None
    assert storage.list_board_entries("topic:a")[0].text == "hi"


def test_storage_legacy_db_gets_launch_mode_column(tmp_path) -> None:
    """A pre-launch_mode SQLite file gains the column with default 'auto'."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            backend TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'managed',
            title TEXT NOT NULL,
            cwd TEXT NOT NULL,
            launch_target_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_event_at TEXT NOT NULL,
            raw_log_path TEXT NOT NULL,
            structured_log_path TEXT NOT NULL,
            transport_state TEXT NOT NULL DEFAULT '{}',
            permission_mode TEXT,
            model TEXT,
            effort TEXT,
            transport TEXT
        );
        CREATE TABLE scheduled_sessions (
            id TEXT PRIMARY KEY,
            backend TEXT NOT NULL,
            cwd TEXT NOT NULL,
            launch_target_id TEXT,
            title TEXT,
            args TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            scheduled_for TEXT NOT NULL,
            spawned_session_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_error TEXT
        );
        CREATE TABLE events (
            session_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            text TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (session_id, sequence)
        );
        """)
    conn.commit()
    conn.close()

    Storage(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(sessions)")}
        assert "launch_mode" in cols
        sched_cols = {
            row[1]: row for row in conn.execute("PRAGMA table_info(scheduled_sessions)")
        }
        assert "launch_mode" in sched_cols
        # Inserting a row without specifying launch_mode should pick up the default.
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO sessions (id, backend, title, cwd, status, created_at, "
            "updated_at, last_event_at, raw_log_path, structured_log_path, transport) "
            "VALUES (?, 'codex', 't', '/tmp', 'idle', ?, ?, ?, '/tmp/raw.log', "
            "'/tmp/events.jsonl', 'codex_app_server')",
            ("s1", now, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT launch_mode FROM sessions WHERE id = 's1'"
        ).fetchone()
        assert row[0] == LaunchMode.AUTO.value
    finally:
        conn.close()


# ── board history / author_label / keep_last ────────────────────────────────


def test_board_author_label_round_trips(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    entry = storage.add_board_entry(
        "topic:a", "hello", author_session_id="s1", author_label="My Session"
    )
    assert entry.author_label == "My Session"
    loaded = storage.list_board_entries("topic:a")
    assert loaded[0].author_label == "My Session"


def test_board_author_label_survives_session_delete(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _make_session(storage, "s1", "Worker One")
    entry = storage.add_board_entry(
        "topic:a", "done", author_session_id="s1", author_label="Worker One"
    )
    storage.delete_session("s1")
    # Log post survives; author_label still readable.
    remaining = storage.list_board_entries("topic:a")
    assert len(remaining) == 1
    assert remaining[0].id == entry.id
    assert remaining[0].author_label == "Worker One"


def test_board_clear_keep_last_retains_n_newest_log_posts(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    ids = [storage.add_board_entry("topic:a", f"log-{i}").id for i in range(5)]
    # Also add a keyed cell (always dropped by clear).
    storage.add_board_entry("topic:a", "val", key="k1")
    removed = storage.clear_board_channel("topic:a", keep_last=3)
    remaining = storage.list_board_entries("topic:a")
    # Cell is gone; 2 oldest log posts dropped; 3 newest kept.
    assert all(e.key is None for e in remaining)
    remaining_ids = [e.id for e in remaining]
    assert ids[0] not in remaining_ids
    assert ids[1] not in remaining_ids
    assert ids[2] in remaining_ids
    assert ids[3] in remaining_ids
    assert ids[4] in remaining_ids
    assert removed == 3  # 2 old log + 1 cell


def test_board_clear_keep_last_fewer_posts_than_n_only_drops_cells(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    log_id = storage.add_board_entry("topic:a", "only-post").id
    storage.add_board_entry("topic:a", "cell", key="k")
    removed = storage.clear_board_channel("topic:a", keep_last=10)
    remaining = storage.list_board_entries("topic:a")
    assert len(remaining) == 1
    assert remaining[0].id == log_id
    assert removed == 1  # only the cell


def test_board_regression_log_post_survives_session_delete_and_list(tmp_path) -> None:
    # Regression for the original bug: a keyless "done" log post must survive
    # session delete, and list_board_entries must surface it.
    storage = Storage(tmp_path / "waypoint.db")
    _make_session(storage, "worker", "worker-session")
    log_post = storage.add_board_entry(
        "job:test",
        "task 1 done",
        author_session_id="worker",
        author_label="worker-session",
    )
    storage.add_board_entry(
        "job:test", "running", key="status", author_session_id="worker"
    )
    storage.prune_board_for_session("worker")
    storage.delete_session("worker")
    entries = storage.list_board_entries("job:test")
    assert any(e.id == log_post.id for e in entries)
    log_entries = [e for e in entries if e.key is None]
    assert len(log_entries) == 1
    assert log_entries[0].text == "task 1 done"


def test_init_creates_performance_indexes(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    names = {
        row["name"]
        for row in storage.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    assert {
        "idx_sessions_spawner",
        "idx_board_author",
        "idx_scheduled_status",
    } <= names


def test_update_session_returns_updated_record(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    _make_session(storage, "session-upd", "before")
    updated = storage.update_session("session-upd", title="after")
    assert updated.title == "after"
    reloaded = storage.get_session("session-upd")
    assert reloaded is not None
    assert reloaded.title == "after"


def test_update_session_raises_keyerror_for_missing(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    with pytest.raises(KeyError):
        storage.update_session("does-not-exist", title="x")


def test_update_session_legacy_path_without_returning(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise the fallback for SQLite < 3.35 that lacks UPDATE ... RETURNING.
    monkeypatch.setattr("waypoint.storage._SUPPORTS_RETURNING", False)
    storage = Storage(tmp_path / "waypoint.db")
    _make_session(storage, "session-legacy", "before")

    updated = storage.update_session("session-legacy", title="after")
    assert updated.title == "after"
    reloaded = storage.get_session("session-legacy")
    assert reloaded is not None
    assert reloaded.title == "after"

    with pytest.raises(KeyError):
        storage.update_session("missing-legacy", title="x")


# ── maintenance tests ───────────────────────────────────────────────────────


def test_db_stats(tmp_path) -> None:
    storage, now = _seed_session(tmp_path)
    _append(
        storage, "sess", sequence=1, kind=EventKind.AGENT_OUTPUT, text="hello", ts=now
    )

    stats = storage.db_stats()
    assert "events" in stats
    assert stats["events"]["row_count"] == 1
    assert stats["events_by_kind"] == {EventKind.AGENT_OUTPUT: 1}
    assert stats["events_by_session"] == {"sess": 1}
    assert "fs_footprint" in stats
    assert "db_size_bytes" in stats["fs_footprint"]


def test_scan_orphan_session_dirs(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    # Live session
    _make_session(storage, "live-1", "Live")
    (sessions_dir / "live-1").mkdir()

    # Orphan session
    (sessions_dir / "orphan-1").mkdir()
    (sessions_dir / "orphan-2").mkdir()

    orphans = storage.scan_orphan_session_dirs(sessions_dir)
    orphan_names = {p.name for p in orphans}
    assert orphan_names == {"orphan-1", "orphan-2"}


def test_delete_events_for_filters(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)

    # Create two sessions with different transports/statuses
    _make_session(storage, "sess-1", "Target")
    storage.update_session("sess-1", transport="tmux", status=SessionStatus.EXITED)
    _make_session(storage, "sess-2", "Keep")
    storage.update_session(
        "sess-2", transport="claude_tty", status=SessionStatus.RUNNING
    )

    # Append events
    _append(
        storage,
        "sess-1",
        sequence=1,
        kind=EventKind.AGENT_OUTPUT,
        text="delete-this",
        ts=now,
    )
    _append(
        storage,
        "sess-1",
        sequence=2,
        kind=EventKind.USER_INPUT,
        text="keep-this",
        ts=now,
    )
    _append(
        storage,
        "sess-2",
        sequence=1,
        kind=EventKind.AGENT_OUTPUT,
        text="keep-this-too",
        ts=now,
    )

    count = storage.delete_events_for(
        transports=["tmux"],
        statuses=[SessionStatus.EXITED],
    )
    assert count == 1

    remaining = [e.text for e in storage.list_events("sess-1")] + [
        e.text for e in storage.list_events("sess-2")
    ]
    assert set(remaining) == {"keep-this", "keep-this-too"}


def test_delete_events_for_dry_run(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    now = datetime.now(UTC)
    _make_session(storage, "sess-1", "Target")
    storage.update_session("sess-1", transport="tmux", status=SessionStatus.EXITED)
    _append(
        storage,
        "sess-1",
        sequence=1,
        kind=EventKind.AGENT_OUTPUT,
        text="target",
        ts=now,
    )

    # Dry run should return count but not delete
    count = storage.delete_events_for(transports=["tmux"], dry_run=True)
    assert count == 1
    assert len(storage.list_events("sess-1")) == 1

    # Actual run should delete
    count2 = storage.delete_events_for(transports=["tmux"], dry_run=False)
    assert count2 == 1
    assert len(storage.list_events("sess-1")) == 0


def test_vacuum_runs_without_error(tmp_path) -> None:
    storage = Storage(tmp_path / "waypoint.db")
    storage.vacuum()  # Should not raise
