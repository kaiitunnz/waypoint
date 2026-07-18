from datetime import UTC, datetime, timedelta

from waypoint.notifications.render import intent_from_inbox_item
from waypoint.schemas import (
    EventKind,
    EventRecord,
    InboxItem,
    InboxMarkdownBlockInput,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.storage import Storage


def _storage(tmp_path) -> Storage:
    return Storage(tmp_path / "waypoint.db")


def _session(storage: Storage, session_id: str = "s1") -> None:
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
            source=SessionSource.MANAGED,
            title="Sess",
            cwd="/tmp",
            status=SessionStatus.RUNNING,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )


def _rows_for(item: InboxItem, channel_ids: list[str]) -> list[tuple[str, str, str]]:
    intent = intent_from_inbox_item(item)
    return [(cid, intent.dedupe_key, intent.model_dump_json()) for cid in channel_ids]


def test_create_inbox_item_with_notifications_atomic(tmp_path) -> None:
    storage = _storage(tmp_path)
    item = storage.create_inbox_item_with_notifications(
        from_session_id="",
        from_label=None,
        subject="Hello",
        blocks=[InboxMarkdownBlockInput(text="body")],
        make_deliveries=lambda created: _rows_for(
            created, ["telegram-a", "telegram-b"]
        ),
    )
    assert storage.get_inbox_item(item.id) is not None
    assert storage.count_deliveries_by_status() == {"queued": 2}


def test_unique_dedupe_suppresses_duplicates(tmp_path) -> None:
    storage = _storage(tmp_path)
    _session(storage)
    now = datetime.now(UTC)
    rows = [("chan", "event:s1:approval:r1", "{}")]
    for _ in range(3):
        event = EventRecord(
            session_id="s1",
            ts=now,
            kind=EventKind.APPROVAL_REQUEST,
            text="x",
            metadata={},
            sequence=storage.next_sequence("s1"),
        )
        storage.append_event_with_notifications(event, rows)
    assert storage.count_deliveries_by_status() == {"queued": 1}


def test_claim_lease_and_recovery(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.create_inbox_item_with_notifications(
        from_session_id="",
        from_label=None,
        subject="s",
        blocks=[],
        make_deliveries=lambda created: _rows_for(created, ["c1"]),
    )
    now = datetime.now(UTC)
    claimed = storage.claim_due_deliveries(now=now, limit=10, lease_seconds=120)
    assert len(claimed) == 1
    assert storage.count_deliveries_by_status() == {"sending": 1}
    # A second claim finds nothing (row is leased, not queued).
    assert storage.claim_due_deliveries(now=now, limit=10, lease_seconds=120) == []
    # Startup recovery returns in-flight rows to the queue.
    assert storage.recover_stale_deliveries(now) == 1
    assert storage.count_deliveries_by_status() == {"queued": 1}


def test_mark_sent_requeue_fail(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.create_inbox_item_with_notifications(
        from_session_id="",
        from_label=None,
        subject="s",
        blocks=[],
        make_deliveries=lambda created: _rows_for(created, ["c1", "c2", "c3"]),
    )
    now = datetime.now(UTC)
    claimed = storage.claim_due_deliveries(now=now, limit=10, lease_seconds=120)
    by_channel = {row["channel_id"]: row["id"] for row in claimed}
    storage.mark_delivery_sent(by_channel["c1"], sent_at=now)
    storage.requeue_delivery(
        by_channel["c2"],
        next_attempt_at=now + timedelta(seconds=30),
        attempts=1,
        last_error="boom",
    )
    storage.fail_delivery(by_channel["c3"], attempts=8, last_error="dead")
    assert storage.count_deliveries_by_status() == {"sent": 1, "queued": 1, "failed": 1}
    # The requeued row is not yet due.
    assert storage.claim_due_deliveries(now=now, limit=10, lease_seconds=120) == []
    later = now + timedelta(seconds=31)
    assert (
        len(storage.claim_due_deliveries(now=later, limit=10, lease_seconds=120)) == 1
    )


def test_delete_old_deliveries(tmp_path) -> None:
    storage = _storage(tmp_path)
    storage.create_inbox_item_with_notifications(
        from_session_id="",
        from_label=None,
        subject="s",
        blocks=[],
        make_deliveries=lambda created: _rows_for(created, ["c1"]),
    )
    now = datetime.now(UTC)
    claimed = storage.claim_due_deliveries(now=now, limit=10, lease_seconds=120)
    storage.fail_delivery(claimed[0]["id"], attempts=8, last_error="x")
    # Nothing older than the cutoff yet.
    assert storage.delete_old_deliveries(now - timedelta(days=30)) == 0
    # A future cutoff purges the terminal row.
    assert storage.delete_old_deliveries(now + timedelta(days=1)) == 1
    assert storage.count_deliveries_by_status() == {}
