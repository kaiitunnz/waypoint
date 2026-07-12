"""Tests for ``TelemetryStore`` (CONTRACT.md §1/§7).

Covers dedup/revision-replace on ``ingest_fact``, that the recompute-on-write
rollup path agrees with a full ``rebuild_rollups_from_facts``, that
``Storage.delete_session`` cascades to telemetry facts/tags/rollups without
touching another session's transcript, and that retention pruning drops old
facts while the longer-lived daily rollup survives.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.storage import Storage
from waypoint.telemetry.facts import (
    ContextSnapshotFact,
    FactDimensions,
    LifecycleTransition,
    SessionLifecycleFact,
    TelemetryFactKind,
    TelemetryFilter,
    TelemetryRange,
    ToolCallFact,
    ToolOutcome,
    TurnFact,
    TurnKind,
)


def _dims(**overrides: object) -> FactDimensions:
    fields: dict[str, object] = {
        "backend": "codex",
        "repo_name": "waypoint",
        "source": SessionSource.MANAGED,
        "transport": "tmux",
        "spawner_session_id": None,
        "is_child": False,
    }
    fields.update(overrides)
    return FactDimensions.model_validate(fields)


def _make_session(storage: Storage, session_id: str) -> datetime:
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend="codex",
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


def _lifecycle_fact(
    session_id: str,
    transition: LifecycleTransition,
    *,
    occurred_at: datetime,
    revision: int = 0,
) -> SessionLifecycleFact:
    return SessionLifecycleFact(
        fact_id=f"{session_id}:{transition}",
        source="runtime",
        session_id=session_id,
        occurred_at=occurred_at,
        revision=revision,
        dims=_dims(),
        transition=transition,
    )


def _rollup_row(
    storage: Storage, day: str, backend: str = "codex", model: str = ""
) -> dict[str, Any] | None:
    row = storage.connection.execute(
        """
        SELECT metrics_json FROM telemetry_daily_rollup
        WHERE day = ? AND backend = ? AND model = ?
        """,
        (day, backend, model),
    ).fetchone()
    return json.loads(row["metrics_json"]) if row is not None else None


def _require_rollup(
    storage: Storage, day: str, backend: str = "codex", model: str = ""
) -> dict[str, Any]:
    metrics = _rollup_row(storage, day, backend, model)
    assert metrics is not None
    return metrics


def test_new_fact_writes_and_replay_is_ignored(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    fact = _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now)

    assert storage.telemetry.ingest_fact(fact) is True
    # Same revision replayed (retransmission) is a no-op, not a duplicate count.
    assert storage.telemetry.ingest_fact(fact) is False

    rows = storage.connection.execute(
        "SELECT COUNT(*) AS n FROM telemetry_facts WHERE session_id = ?", ("s1",)
    ).fetchone()
    assert rows["n"] == 1


def test_lower_or_equal_revision_is_ignored_higher_replaces(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    v0 = _lifecycle_fact(
        "s1", LifecycleTransition.STARTING, occurred_at=now, revision=0
    )
    assert storage.telemetry.ingest_fact(v0) is True

    # Same identity, same revision replayed (retransmission) — ignored.
    replay = SessionLifecycleFact(
        fact_id=v0.fact_id,
        source="runtime",
        session_id="s1",
        occurred_at=now,
        revision=0,
        dims=_dims(),
        transition=LifecycleTransition.STARTING,
    )
    assert storage.telemetry.ingest_fact(replay) is False

    revised = SessionLifecycleFact(
        fact_id=v0.fact_id,
        source="runtime",
        session_id="s1",
        occurred_at=now,
        revision=1,
        dims=_dims(),
        transition=LifecycleTransition.RUNNING,
    )
    assert storage.telemetry.ingest_fact(revised) is True

    row = storage.connection.execute(
        "SELECT transition, revision FROM telemetry_facts WHERE fact_id = ?",
        (v0.fact_id,),
    ).fetchone()
    assert row["transition"] == LifecycleTransition.RUNNING
    assert row["revision"] == 1


def test_tags_are_rewritten_on_revision_replace(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    fact = _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now)
    storage.telemetry.ingest_fact(fact, tags={"role": "lead"})

    revised = SessionLifecycleFact(
        fact_id=fact.fact_id,
        source="runtime",
        session_id="s1",
        occurred_at=now,
        revision=1,
        dims=_dims(),
        transition=LifecycleTransition.CREATED,
    )
    storage.telemetry.ingest_fact(revised, tags={"role": "worker"})

    rows = storage.connection.execute(
        "SELECT key, value FROM telemetry_fact_tag WHERE fact_id = ?", (fact.fact_id,)
    ).fetchall()
    assert [(r["key"], r["value"]) for r in rows] == [("role", "worker")]


def test_rollup_delta_matches_rebuild(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    day = now.astimezone().date().isoformat()

    storage.telemetry.ingest_fact(
        _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now)
    )
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:running",
            source="runtime",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.RUNNING,
        )
    )
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="s1:user:1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            turn_kind=TurnKind.USER,
        )
    )

    # ``model_at_turn`` left unset so this fact stays in the same (model="")
    # rollup bucket as the other facts above — model-dimension splitting is
    # covered separately in test_rollup_splits_by_model.
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="turn-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            turn_kind=TurnKind.AGENT,
        )
    )
    storage.telemetry.ingest_fact(
        ToolCallFact(
            fact_id="tool-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            tool_name="Read",
            outcome=ToolOutcome.SUCCEEDED,
        )
    )

    delta_metrics = _rollup_row(storage, day)
    assert delta_metrics is not None
    assert delta_metrics["turns_user"] == 1
    assert delta_metrics["turns_agent"] == 1
    assert delta_metrics["tool_calls"] == 1
    assert delta_metrics["lifecycle"] == {"created": 1, "running": 1}
    # The rollup carries only these four read-by-production keys; the dead
    # token/outcome/active_denom keys were dropped (#7).
    assert set(delta_metrics) == {
        "turns_user",
        "turns_agent",
        "tool_calls",
        "lifecycle",
    }

    storage.telemetry.rebuild_rollups_from_facts()
    rebuilt_metrics = _rollup_row(storage, day)
    assert rebuilt_metrics == delta_metrics


def _table_exists(storage: Storage, name: str) -> bool:
    row = storage.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def test_init_schema_drops_dead_rollup_session_table(tmp_path: Path) -> None:
    # A database that predates #7 may still carry telemetry_rollup_session;
    # re-running init_schema (every boot) must shed it.
    storage = Storage(tmp_path / "db.sqlite")
    assert not _table_exists(storage, "telemetry_rollup_session")

    storage.connection.execute(
        "CREATE TABLE telemetry_rollup_session (day TEXT PRIMARY KEY)"
    )
    storage.connection.commit()
    assert _table_exists(storage, "telemetry_rollup_session")

    storage.telemetry.init_schema()
    assert not _table_exists(storage, "telemetry_rollup_session")


def test_rollup_splits_by_model(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    day = now.astimezone().date().isoformat()

    # No model — lands in the default ("") bucket.
    storage.telemetry.ingest_fact(
        _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now)
    )
    # A turn with a concrete model_at_turn is a distinct rollup key (FR-2
    # token-by-model), not folded into the model-less bucket.
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="s1:agent:1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            turn_kind=TurnKind.AGENT,
            model_at_turn="gpt-5-codex",
        )
    )

    assert _require_rollup(storage, day, model="")["lifecycle"] == {"created": 1}
    assert _require_rollup(storage, day, model="")["turns_agent"] == 0
    by_model = _require_rollup(storage, day, model="gpt-5-codex")
    assert by_model["turns_agent"] == 1
    assert by_model["lifecycle"] == {}


def test_rollup_revision_replace_moves_lifecycle_count(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    day = now.astimezone().date().isoformat()

    fact = _lifecycle_fact(
        "s1", LifecycleTransition.STARTING, occurred_at=now, revision=0
    )
    storage.telemetry.ingest_fact(fact)
    assert _require_rollup(storage, day)["lifecycle"] == {"starting": 1}

    revised = SessionLifecycleFact(
        fact_id=fact.fact_id,
        source="runtime",
        session_id="s1",
        occurred_at=now,
        revision=1,
        dims=_dims(),
        transition=LifecycleTransition.RUNNING,
    )
    storage.telemetry.ingest_fact(revised)

    metrics = _require_rollup(storage, day)
    # The old transition's count is gone, not double-counted alongside the new one.
    assert metrics["lifecycle"] == {"running": 1}


def test_partial_facts_excluded_from_rollup(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    day = now.astimezone().date().isoformat()

    storage.telemetry.ingest_fact(
        ContextSnapshotFact(
            fact_id="ctx-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            partial=True,
            dims=_dims(),
            used_tokens=100,
            window_tokens=1000,
            occupancy_percent=10.0,
        )
    )
    assert _rollup_row(storage, day) is None


def test_delete_session_cascades_without_touching_other_sessions(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now_a = _make_session(storage, "a")
    now_b = _make_session(storage, "b")
    day = now_a.astimezone().date().isoformat()

    storage.telemetry.ingest_fact(
        _lifecycle_fact("a", LifecycleTransition.CREATED, occurred_at=now_a),
        tags={"k": "v"},
    )
    storage.telemetry.ingest_fact(
        _lifecycle_fact("b", LifecycleTransition.CREATED, occurred_at=now_b)
    )
    storage.append_event(
        EventRecord(
            session_id="b",
            ts=now_b,
            kind=EventKind.USER_INPUT,
            text="hello",
            sequence=1,
        )
    )

    storage.delete_session("a")

    remaining = storage.connection.execute(
        "SELECT session_id FROM telemetry_facts"
    ).fetchall()
    assert [r["session_id"] for r in remaining] == ["b"]
    tags = storage.connection.execute("SELECT * FROM telemetry_fact_tag").fetchall()
    assert tags == []

    metrics = _require_rollup(storage, day)
    assert metrics["lifecycle"] == {"created": 1}

    # Session "b"'s transcript is untouched by "a"'s deletion.
    b_events = storage.list_events("b")
    assert len(b_events) == 1
    assert b_events[0].text == "hello"
    assert storage.get_session("b") is not None


def test_prune_drops_old_facts_but_rollup_outlives_them(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    _make_session(storage, "s1")
    day = old.astimezone().date().isoformat()

    storage.telemetry.ingest_fact(
        _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=old)
    )
    assert _rollup_row(storage, day) is not None

    removed = storage.telemetry.prune(
        facts_before=now - timedelta(days=90),
        rollups_before=now - timedelta(days=400 * 13 // 12),
    )
    assert removed["facts"] == 1
    assert removed["rollups"] == 0

    facts_left = storage.connection.execute(
        "SELECT COUNT(*) AS n FROM telemetry_facts"
    ).fetchone()
    assert facts_left["n"] == 0
    # The daily rollup (13-month retention) survives the 90-day fact prune.
    assert _rollup_row(storage, day) is not None

    removed_again = storage.telemetry.prune(
        facts_before=now, rollups_before=now + timedelta(days=1)
    )
    assert removed_again["rollups"] == 1
    assert _rollup_row(storage, day) is None


def test_get_set_meta_round_trip(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    assert storage.telemetry.get_meta("backfill_done") is None
    storage.telemetry.set_meta("backfill_done", "true")
    assert storage.telemetry.get_meta("backfill_done") == "true"
    storage.telemetry.set_meta("backfill_done", "false")
    assert storage.telemetry.get_meta("backfill_done") == "false"


def test_ingest_facts_batches_and_returns_written_count(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    fact_a = _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now)
    fact_b = _lifecycle_fact("s1", LifecycleTransition.STARTING, occurred_at=now)
    written = storage.telemetry.ingest_facts([(fact_a, {}), (fact_b, {}), (fact_a, {})])
    assert written == 2


def _same_day_batch(now: datetime) -> list[tuple[Any, dict[str, str]]]:
    """Five distinct facts sharing one (day, dims, model="") rollup key."""
    return [
        (_lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now), {}),
        (_lifecycle_fact("s1", LifecycleTransition.RUNNING, occurred_at=now), {}),
        (
            TurnFact(
                fact_id="u1",
                source="codex",
                session_id="s1",
                occurred_at=now,
                dims=_dims(),
                turn_kind=TurnKind.USER,
            ),
            {},
        ),
        (
            TurnFact(
                fact_id="a1",
                source="codex",
                session_id="s1",
                occurred_at=now,
                dims=_dims(),
                turn_kind=TurnKind.AGENT,
            ),
            {},
        ),
        (
            ToolCallFact(
                fact_id="tool1",
                source="codex",
                session_id="s1",
                occurred_at=now,
                dims=_dims(),
                tool_name="Read",
                outcome=ToolOutcome.SUCCEEDED,
            ),
            {},
        ),
    ]


def test_ingest_facts_coalesces_recompute_and_matches_per_fact(tmp_path: Path) -> None:
    # A same-day/same-dims batch must recompute the shared rollup key exactly
    # ONCE (not once per fact), and yield a rollup byte-identical to feeding the
    # same facts through the singular ``ingest_fact`` path.
    batch = Storage(tmp_path / "batch.sqlite")
    now = _make_session(batch, "s1")
    day = now.astimezone().date().isoformat()
    facts = _same_day_batch(now)

    with patch.object(
        batch.telemetry,
        "_recompute_rollup_key",
        wraps=batch.telemetry._recompute_rollup_key,
    ) as spy:
        written = batch.telemetry.ingest_facts(facts)
    assert written == 5
    assert spy.call_count == 1
    batch_metrics = _require_rollup(batch, day)
    assert batch_metrics == {
        "turns_user": 1,
        "turns_agent": 1,
        "tool_calls": 1,
        "lifecycle": {"created": 1, "running": 1},
    }

    per_fact = Storage(tmp_path / "per_fact.sqlite")
    _make_session(per_fact, "s1")
    for fact, tags in _same_day_batch(now):
        per_fact.telemetry.ingest_fact(fact, tags=tags)
    assert _require_rollup(per_fact, day) == batch_metrics


def test_ingest_facts_in_batch_revision_moves_across_days_leaves_no_stale_row(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "s1")
    day1_at = datetime(2026, 3, 15, 12, tzinfo=UTC)
    day2_at = datetime(2026, 3, 17, 12, tzinfo=UTC)
    day1 = day1_at.astimezone().date().isoformat()
    day2 = day2_at.astimezone().date().isoformat()

    v0 = TurnFact(
        fact_id="a1",
        source="codex",
        session_id="s1",
        occurred_at=day1_at,
        dims=_dims(),
        turn_kind=TurnKind.AGENT,
        revision=0,
    )
    v1 = TurnFact(
        fact_id="a1",
        source="codex",
        session_id="s1",
        occurred_at=day2_at,
        dims=_dims(),
        turn_kind=TurnKind.AGENT,
        revision=1,
    )
    storage.telemetry.ingest_facts([(v0, {}), (v1, {})])

    # The fact moved to day2 in the same batch; day1's rollup key must be
    # recomputed too (from the batch union) so it is deleted, not left stale.
    assert _rollup_row(storage, day1) is None
    assert _require_rollup(storage, day2)["turns_agent"] == 1


class _RecordingConnection:
    """Proxy that records executed SQL; ``sqlite3.Connection.execute`` is a
    read-only C method and can't be patched directly."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.executed: list[str] = []

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
        self.executed.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _tag_deletes(recorder: _RecordingConnection) -> list[str]:
    return [sql for sql in recorder.executed if "DELETE FROM telemetry_fact_tag" in sql]


def test_new_fact_without_tags_skips_tag_delete(tmp_path: Path) -> None:
    # A brand-new untagged fact should not issue a tag-table DELETE (#7).
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    fact = _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now)

    recorder = _RecordingConnection(storage.telemetry._conn)
    storage.telemetry._conn = recorder  # type: ignore[assignment]
    storage.telemetry.ingest_fact(fact)
    assert _tag_deletes(recorder) == []

    # A tagged new fact still writes (and clears) its tags.
    recorder.executed.clear()
    tagged = _lifecycle_fact("s1", LifecycleTransition.STARTING, occurred_at=now)
    storage.telemetry.ingest_fact(tagged, tags={"role": "lead"})
    assert _tag_deletes(recorder)

    # A revision still clears prior tags even when untagged.
    recorder.executed.clear()
    revised = SessionLifecycleFact(
        fact_id=fact.fact_id,
        source="runtime",
        session_id="s1",
        occurred_at=now,
        revision=1,
        dims=_dims(),
        transition=LifecycleTransition.CREATED,
    )
    storage.telemetry.ingest_fact(revised)
    assert _tag_deletes(recorder)


def test_heatmap_counts_bucket_weekday_hour_and_total(tmp_path: Path) -> None:
    # Pins a KNOWN Sunday and Monday to explicit Monday=0 ``dow`` values to
    # catch the SQLite ``%w`` (Sunday=0) off-by-one, plus an hour bucket, a
    # quiet cell (omitted), and total-count == fact-count invariants.
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "s1")
    sunday = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)  # weekday()==6
    monday = datetime(2026, 3, 16, 14, 0, tzinfo=UTC)  # weekday()==0

    def _turn(fact_id: str, at: datetime) -> None:
        storage.telemetry.ingest_fact(
            TurnFact(
                fact_id=fact_id,
                source="codex",
                session_id="s1",
                occurred_at=at,
                dims=_dims(),
                turn_kind=TurnKind.AGENT,
            )
        )

    _turn("t-sun", sunday)
    _turn("t-mon-1", monday)
    _turn("t-mon-2", monday + timedelta(minutes=30))  # same host hour → count 2
    storage.telemetry.ingest_fact(
        ToolCallFact(
            fact_id="tool-mon",
            source="codex",
            session_id="s1",
            occurred_at=monday + timedelta(minutes=45),  # same cell → count 3
            dims=_dims(),
            tool_name="Read",
            outcome=ToolOutcome.SUCCEEDED,
        )
    )

    rng = TelemetryRange(
        start=datetime(2026, 3, 14, tzinfo=UTC),
        end=datetime(2026, 3, 18, tzinfo=UTC),
        tz="UTC",
        utc_offset_minutes=0,
    )
    cells = {
        (dow, hour): count
        for dow, hour, count in storage.telemetry.heatmap_counts(rng, TelemetryFilter())
    }

    assert cells[(6, 10)] == 1  # Sunday 10:00 → Monday=0 contract dow 6
    assert cells[(0, 14)] == 3  # Monday 14:xx: two turns + one tool_call
    assert (1, 0) not in cells  # a quiet cell is omitted, not zero-filled

    turn_count = storage.telemetry.count_facts(
        TelemetryFactKind.TURN, rng, TelemetryFilter()
    )
    tool_count = storage.telemetry.count_facts(
        TelemetryFactKind.TOOL_CALL, rng, TelemetryFilter()
    )
    assert sum(cells.values()) == turn_count + tool_count == 4


def test_heatmap_counts_applies_signed_host_offset(tmp_path: Path) -> None:
    # Exercises the signed offset shift (and cross-midnight) that the zero-offset
    # test above cannot: one UTC instant buckets into different host-tz cells
    # under a positive (east, day-crossing) vs a negative (west) offset.
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "s1")
    # Sunday 23:30 UTC (weekday()==6).
    at = datetime(2026, 3, 15, 23, 30, tzinfo=UTC)
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="t1",
            source="codex",
            session_id="s1",
            occurred_at=at,
            dims=_dims(),
            turn_kind=TurnKind.AGENT,
        )
    )
    window = dict(
        start=datetime(2026, 3, 14, tzinfo=UTC),
        end=datetime(2026, 3, 18, tzinfo=UTC),
        tz="x",
    )

    def _cells(offset: int) -> dict[tuple[int, int], int]:
        rng = TelemetryRange(**window, utc_offset_minutes=offset)
        return {
            (dow, hour): count
            for dow, hour, count in storage.telemetry.heatmap_counts(
                rng, TelemetryFilter()
            )
        }

    # +08:00 → local Mon 07:30 → Monday=0 dow 0, hour 7 (crossed midnight).
    assert _cells(480) == {(0, 7): 1}
    # -05:00 → local Sun 18:30 → dow 6, hour 18 (still Sunday).
    assert _cells(-300) == {(6, 18): 1}


def test_query_facts_and_count_facts(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        _lifecycle_fact("s1", LifecycleTransition.CREATED, occurred_at=now)
    )
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:running",
            source="runtime",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.RUNNING,
        )
    )
    rng = TelemetryRange(
        start=now - timedelta(hours=1), end=now + timedelta(hours=1), tz="UTC"
    )
    flt = TelemetryFilter()
    facts = storage.telemetry.query_facts(TelemetryFactKind.SESSION_LIFECYCLE, rng, flt)
    assert len(facts) == 2
    assert (
        storage.telemetry.count_facts(TelemetryFactKind.SESSION_LIFECYCLE, rng, flt)
        == 2
    )


def test_dismiss_insight_round_trip(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    assert storage.telemetry.dismissed_insights("2026-07") == set()
    storage.telemetry.dismiss_insight("near_limit:codex", "2026-07")
    assert storage.telemetry.dismissed_insights("2026-07") == {"near_limit:codex"}
