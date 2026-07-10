"""Storage-layer tests for the durable per-turn token-usage ledger.

Covers the correctness bar from the RFC: a distinct provider record counts
exactly once; a duplicate delivery / source replay does not inflate; a
correction replaces a row's values without adding a turn; two distinct turns
with identical values both count; the aggregate survives reload; coverage
seeds once; deletion prunes ledger rows; and a legacy DB predating the column
decodes tolerantly.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from waypoint.schemas import (
    SessionRecord,
    SessionSource,
    SessionStatus,
    TokenUsageInit,
    TokenUsageRecord,
)
from waypoint.storage import Storage


def _make_session(storage: Storage, session_id: str = "s1") -> datetime:
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend="tmux",
            source=SessionSource.MANAGED,
            transport="tmux",
            title="t",
            cwd="/tmp",
            status=SessionStatus.IDLE,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )
    return now


def _init(observed_from: datetime) -> TokenUsageInit:
    return TokenUsageInit(
        coverage="entire_waypoint_session", observed_from=observed_from
    )


def _record(
    record_id: str,
    observed_at: datetime,
    *,
    totals: dict[str, int],
    display: int | None = None,
) -> TokenUsageRecord:
    return TokenUsageRecord(
        record_id=record_id,
        source="tmux",
        observed_at=observed_at,
        totals=totals,
        display_total_tokens=display,
    )


def test_single_record_seeds_aggregate(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage)
    agg = storage.record_token_usage(
        "s1",
        _record(
            "m1", now, totals={"input_tokens": 100, "output_tokens": 10}, display=110
        ),
        init=_init(now),
    )
    assert agg.tracked_turns == 1
    assert agg.totals == {"input_tokens": 100, "output_tokens": 10}
    assert agg.display_total_tokens == 110
    assert agg.coverage == "entire_waypoint_session"
    assert agg.observed_from == now


def test_duplicate_delivery_does_not_inflate(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage)
    rec = _record("m1", now, totals={"input_tokens": 100}, display=100)
    storage.record_token_usage("s1", rec, init=_init(now))
    agg = storage.record_token_usage("s1", rec, init=_init(now))
    assert agg.tracked_turns == 1
    assert agg.totals == {"input_tokens": 100}
    assert agg.display_total_tokens == 100


def test_two_distinct_equal_valued_turns_both_count(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage)
    storage.record_token_usage(
        "s1",
        _record("m1", now, totals={"input_tokens": 100}, display=100),
        init=_init(now),
    )
    agg = storage.record_token_usage(
        "s1",
        _record("m2", now, totals={"input_tokens": 100}, display=100),
        init=_init(now),
    )
    assert agg.tracked_turns == 2
    assert agg.totals == {"input_tokens": 200}
    assert agg.display_total_tokens == 200


def test_correction_replaces_without_new_turn(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage)
    storage.record_token_usage(
        "s1",
        _record("m1", now, totals={"input_tokens": 100}, display=100),
        init=_init(now),
    )
    storage.record_token_usage(
        "s1",
        _record("m2", now, totals={"input_tokens": 100}, display=100),
        init=_init(now),
    )
    # Provider revises m1.
    agg = storage.record_token_usage(
        "s1",
        _record("m1", now, totals={"input_tokens": 250}, display=250),
        init=_init(now),
    )
    assert agg.tracked_turns == 2
    assert agg.totals == {"input_tokens": 350}
    assert agg.display_total_tokens == 350


def test_display_total_none_is_non_destructive(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage)
    storage.record_token_usage(
        "s1",
        _record("m1", now, totals={"input_tokens": 100}, display=100),
        init=_init(now),
    )
    # A turn without a display total makes the synthesized grand total unavailable.
    agg = storage.record_token_usage(
        "s1", _record("m2", now, totals={"input_tokens": 50}), init=_init(now)
    )
    assert agg.tracked_turns == 2
    assert agg.display_total_tokens is None
    # Correcting m2 to carry a display total restores the sum (no scan needed).
    agg = storage.record_token_usage(
        "s1",
        _record("m2", now, totals={"input_tokens": 50}, display=50),
        init=_init(now),
    )
    assert agg.display_total_tokens == 150


def test_updated_at_never_regresses(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage)
    later = now + timedelta(minutes=5)
    earlier = now + timedelta(minutes=1)
    storage.record_token_usage(
        "s1", _record("m2", later, totals={"input_tokens": 100}), init=_init(now)
    )
    # An out-of-order correction for an older turn must not move updated_at back.
    agg = storage.record_token_usage(
        "s1", _record("m1", earlier, totals={"input_tokens": 20}), init=_init(now)
    )
    assert agg.updated_at == later
    assert agg.complete_through == later


def test_aggregate_survives_reload(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    storage = Storage(db)
    now = _make_session(storage)
    storage.record_token_usage(
        "s1",
        _record("m1", now, totals={"input_tokens": 100}, display=100),
        init=_init(now),
    )
    storage.close()
    reopened = Storage(db)
    loaded = reopened.get_session("s1")
    assert loaded is not None and loaded.session_token_usage is not None
    assert loaded.session_token_usage.tracked_turns == 1
    assert loaded.session_token_usage.display_total_tokens == 100
    assert loaded.session_token_usage.totals == {"input_tokens": 100}


def test_rebuild_matches_incremental(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage)
    storage.record_token_usage(
        "s1",
        _record("m1", now, totals={"input_tokens": 100}, display=100),
        init=_init(now),
    )
    storage.record_token_usage(
        "s1",
        _record("m2", now, totals={"input_tokens": 200}, display=200),
        init=_init(now),
    )
    reloaded = storage.get_session("s1")
    assert reloaded is not None and reloaded.session_token_usage is not None
    incremental = reloaded.session_token_usage
    rebuilt = storage.rebuild_aggregate_from_ledger("s1")
    assert rebuilt is not None
    assert rebuilt.tracked_turns == incremental.tracked_turns == 2
    assert rebuilt.totals == incremental.totals
    assert rebuilt.display_total_tokens == incremental.display_total_tokens
    assert rebuilt.coverage == incremental.coverage


def test_delete_session_prunes_ledger(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    storage = Storage(db)
    now = _make_session(storage)
    storage.record_token_usage(
        "s1", _record("m1", now, totals={"input_tokens": 100}), init=_init(now)
    )
    storage.delete_session("s1")
    raw = sqlite3.connect(str(db))
    count = raw.execute(
        "SELECT COUNT(*) FROM session_token_usage_records WHERE session_id = 's1'"
    ).fetchone()[0]
    assert count == 0


def test_legacy_db_without_column_decodes(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite"
    storage = Storage(db)
    _make_session(storage)
    storage.close()
    # Simulate a DB predating the column by nulling it, then reopen.
    raw = sqlite3.connect(str(db))
    raw.execute("UPDATE sessions SET session_token_usage = NULL WHERE id = 's1'")
    raw.commit()
    raw.close()
    reopened = Storage(db)
    loaded = reopened.get_session("s1")
    assert loaded is not None
    assert loaded.session_token_usage is None
