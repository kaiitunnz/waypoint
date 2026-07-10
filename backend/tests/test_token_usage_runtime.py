"""Runtime-level tests for token-usage ingestion and coverage semantics.

Verifies that the runtime computes honest coverage (entire vs tracked-since),
that ingestion is purely additive and resilient (missing identity / vanished
session are no-ops that never raise), and that the generic import chokepoint
stamps the adoption marker that drives tracked-since coverage.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    SessionRecord,
    SessionSource,
    SessionStatus,
    TokenUsageRecord,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _make_runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    registry = MagicMock()
    registry.all.return_value = []
    return SessionRuntime(settings, storage, registry=registry)


def _session(
    runtime: SessionRuntime,
    tmp_path: Path,
    *,
    source: SessionSource = SessionSource.MANAGED,
    transport_state: dict | None = None,
    session_id: str = "s1",
) -> SessionRecord:
    now = datetime.now(UTC)
    raw = tmp_path / f"{session_id}.log"
    raw.touch()
    session = SessionRecord(
        id=session_id,
        backend="tmux",
        source=source,
        transport="tmux",
        title="t",
        cwd=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(raw),
        structured_log_path=str(tmp_path / f"{session_id}.jsonl"),
        transport_state=transport_state or {},
    )
    runtime.storage.create_session(session)
    return session


def _record(observed_at: datetime, record_id: str = "m1") -> TokenUsageRecord:
    return TokenUsageRecord(
        record_id=record_id,
        source="tmux",
        observed_at=observed_at,
        totals={"input_tokens": 100},
        display_total_tokens=100,
    )


def test_fresh_managed_session_is_entire(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    session = _session(runtime, tmp_path)
    init = runtime._token_usage_init(session, datetime.now(UTC))
    assert init.coverage == "entire_waypoint_session"
    assert init.observed_from == session.created_at


def test_fresh_assistant_session_is_entire(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    session = _session(runtime, tmp_path, source=SessionSource.ASSISTANT)
    init = runtime._token_usage_init(session, datetime.now(UTC))
    assert init.coverage == "entire_waypoint_session"


def test_adopted_thread_session_is_tracked_since(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    later = datetime.now(UTC) + timedelta(hours=1)
    session = _session(runtime, tmp_path, transport_state={"adopted_thread": True})
    init = runtime._token_usage_init(session, later)
    assert init.coverage == "tracked_since"
    assert init.observed_from == later


def test_attached_tmux_session_is_tracked_since(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    session = _session(runtime, tmp_path, source=SessionSource.ATTACHED_TMUX)
    init = runtime._token_usage_init(session, datetime.now(UTC))
    assert init.coverage == "tracked_since"


def test_pretracked_session_is_tracked_since(tmp_path: Path) -> None:
    # A managed session that predates the ledger (migration-stamped) must not
    # claim the whole session even though it is otherwise from-birth.
    runtime = _make_runtime(tmp_path)
    session = _session(runtime, tmp_path, transport_state={"pretracked_tokens": True})
    init = runtime._token_usage_init(session, datetime.now(UTC))
    assert init.coverage == "tracked_since"


async def test_publish_persists_and_marks_dirty(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    session = _session(runtime, tmp_path)
    now = datetime.now(UTC)
    await runtime.publish_token_usage_record(session.id, _record(now))
    reloaded = runtime.storage.get_session(session.id)
    assert reloaded is not None and reloaded.session_token_usage is not None
    assert reloaded.session_token_usage.tracked_turns == 1
    assert reloaded.session_token_usage.coverage == "entire_waypoint_session"
    assert session.id in runtime._dirty_session_states


async def test_publish_without_identity_is_noop(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    session = _session(runtime, tmp_path)
    now = datetime.now(UTC)
    await runtime.publish_token_usage_record(session.id, _record(now, record_id=""))
    reloaded = runtime.storage.get_session(session.id)
    assert reloaded is not None
    assert reloaded.session_token_usage is None


async def test_publish_for_missing_session_is_noop(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    # Never raises even though the session does not exist.
    await runtime.publish_token_usage_record("ghost", _record(datetime.now(UTC)))


async def test_seed_thread_history_stamps_adoption_marker(tmp_path: Path) -> None:
    runtime = _make_runtime(tmp_path)
    session = _session(runtime, tmp_path)

    async def _reader() -> list:
        return []

    await runtime.seed_thread_history(session.id, _reader, enabled=False)
    reloaded = runtime.storage.get_session(session.id)
    assert reloaded is not None
    assert reloaded.transport_state.get("adopted_thread") is True
    # After adoption, coverage for the first record is tracked-since.
    init = runtime._token_usage_init(reloaded, datetime.now(UTC))
    assert init.coverage == "tracked_since"
