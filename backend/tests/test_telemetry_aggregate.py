"""Unit tests for the ``telemetry/aggregate.py`` shaper (CONTRACT.md §4/§7).

Exercises aggregation math (token folding, meter coverage, rollup sums),
filter semantics including limit-card hiding, date/tz range boundaries,
empty ranges, and drill-down count/page consistency — all against
``TelemetryStore``/``Storage`` directly (no HTTP layer).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from waypoint.schemas import (
    SessionRecord,
    SessionSource,
    SessionStatus,
    TokenUsageInit,
    TokenUsageRecord,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry import aggregate
from waypoint.telemetry import insights as telemetry_insights
from waypoint.telemetry.facts import (
    ContextSnapshotFact,
    FactDimensions,
    LifecycleTransition,
    LimitSnapshotFact,
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


def _make_session(
    storage: Storage,
    session_id: str,
    *,
    backend: str = "codex",
    status: SessionStatus = SessionStatus.IDLE,
    repo_name: str | None = "waypoint",
    spawner_session_id: str | None = None,
) -> datetime:
    now = datetime.now(UTC)
    storage.create_session(
        SessionRecord(
            id=session_id,
            backend=backend,
            source=SessionSource.MANAGED,
            transport="tmux",
            title=session_id,
            cwd="/tmp",
            repo_name=repo_name,
            spawner_session_id=spawner_session_id,
            status=status,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path="/tmp/raw.log",
            structured_log_path="/tmp/events.jsonl",
        )
    )
    return now


def _seed_agent_turn(
    storage: Storage,
    session_id: str,
    *,
    fact_id: str,
    occurred_at: datetime,
    totals: dict[str, int] | None = None,
    display_total: int | None = None,
    model_at_turn: str | None = None,
    source: str = "codex",
) -> None:
    """A ``TurnFact`` (AGENT) plus its ledger row (or none, to leave it untracked)."""
    if totals is not None:
        storage.record_token_usage(
            session_id,
            TokenUsageRecord(
                record_id=fact_id,
                source=source,
                observed_at=occurred_at,
                totals=totals,
                display_total_tokens=display_total,
                model=model_at_turn,
            ),
            init=TokenUsageInit(
                coverage="entire_waypoint_session", observed_from=occurred_at
            ),
        )
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id=fact_id,
            source=source,
            session_id=session_id,
            occurred_at=occurred_at,
            dims=_dims(backend=source),
            turn_kind=TurnKind.AGENT,
            model_at_turn=model_at_turn,
        )
    )


def _settings() -> Settings:
    return Settings(telemetry_context_thresholds=(70, 90, 100))


def _full_range(hours: int = 48) -> TelemetryRange:
    now = datetime.now(UTC)
    return TelemetryRange(
        start=now - timedelta(hours=hours), end=now + timedelta(hours=1), tz="UTC"
    )


# ── token folding / meter coverage ────────────────────────────────────────


def test_fold_tokens_counts_tracked_vs_untracked_turns(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    _seed_agent_turn(
        storage, "s1", fact_id="t1", occurred_at=now, totals={"input_tokens": 100}
    )
    # No ledger row for t2 — an untracked agent turn (meter-coverage gap).
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="t2",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            turn_kind=TurnKind.AGENT,
        )
    )

    rows = aggregate.agent_turn_rows(storage, _full_range(), TelemetryFilter())
    assert len(rows) == 2
    ledger = aggregate.ledger_rows_for_sessions(storage, {"s1"})
    totals, display_total, tracked, total = aggregate.fold_tokens(rows, ledger)
    assert totals == {"input_tokens": 100}
    assert display_total is None  # t1 has no display_total_tokens
    assert tracked == 1
    assert total == 2


def test_coverage_label_entire_when_all_turns_tracked(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    assert aggregate.coverage_label(storage, 100.0) == "entire"
    assert aggregate.coverage_label(storage, None) == "entire"


def test_coverage_label_tracked_since_before_backfill_done(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    assert aggregate.coverage_label(storage, 50.0) == "tracked_since"


def test_coverage_label_partial_after_backfill_done(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    storage.telemetry.set_meta("backfill_done", "true")
    assert aggregate.coverage_label(storage, 50.0) == "partial"


# ── /overview ─────────────────────────────────────────────────────────────


def test_build_overview_sums_tokens_turns_and_lifecycle(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:created",
            source="runtime",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.CREATED,
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
    _seed_agent_turn(
        storage,
        "s1",
        fact_id="turn-1",
        occurred_at=now,
        totals={"input_tokens": 100, "output_tokens": 20},
        display_total=120,
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

    overview = aggregate.build_overview(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    assert overview.sessions.created == 1
    assert overview.turns.user == 1
    assert overview.turns.agent == 1
    assert overview.tool_calls == 1
    assert overview.tokens.totals == {"input_tokens": 100, "output_tokens": 20}
    assert overview.tokens.display_total == 120
    assert overview.tokens.safe_total is True
    assert overview.tokens.meter_coverage_percent == 100.0


def test_build_overview_hides_limit_card_when_session_scoped(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "s1")
    # No session-scoping filter -> limit card visible.
    unscoped = aggregate.build_overview(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    assert unscoped.limit_card_hidden is False
    assert unscoped.limit_card_hidden_reason is None

    scoped = aggregate.build_overview(
        storage, _settings(), _full_range(), TelemetryFilter(repos=["waypoint"])
    )
    assert scoped.limit_card_hidden is True
    assert scoped.limit_card_hidden_reason is not None
    assert scoped.alerts.limits == []


def test_build_overview_active_now_ignores_time_range(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "running", status=SessionStatus.RUNNING)
    _make_session(storage, "exited", status=SessionStatus.EXITED)

    # A range that predates both sessions entirely — active_now must still
    # reflect live status, not the (empty) fact window.
    empty_rng = TelemetryRange(
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2000, 1, 2, tzinfo=UTC),
        tz="UTC",
    )
    overview = aggregate.build_overview(
        storage, _settings(), empty_rng, TelemetryFilter()
    )
    assert overview.sessions.active_now == 1


def test_build_overview_empty_range_is_zero_not_error(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "s1")
    empty_rng = TelemetryRange(
        start=datetime(2000, 1, 1, tzinfo=UTC),
        end=datetime(2000, 1, 2, tzinfo=UTC),
        tz="UTC",
    )
    overview = aggregate.build_overview(
        storage, _settings(), empty_rng, TelemetryFilter()
    )
    assert overview.tokens.totals == {}
    assert overview.tokens.display_total is None
    assert overview.turns.user == 0
    assert overview.tool_calls == 0


# ── /health ───────────────────────────────────────────────────────────────


def test_health_context_series_gap_is_null_not_zero(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        ContextSnapshotFact(
            fact_id="ctx-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            used_tokens=1000,
            window_tokens=10000,
            occupancy_percent=10.0,
        )
    )
    rng = TelemetryRange(
        start=now - timedelta(hours=3), end=now + timedelta(hours=1), tz="UTC"
    )
    health = aggregate.build_health(storage, _settings(), rng, TelemetryFilter())
    populated_bucket = now.replace(minute=0, second=0, microsecond=0)
    by_bucket = {
        point.bucket_start: point.peak_percent for point in health.context.series
    }
    assert by_bucket[populated_bucket] == 10.0
    empty_buckets = [b for b in by_bucket if b != populated_bucket]
    assert empty_buckets  # the 3-hour window has other hourly buckets
    assert all(by_bucket[b] is None for b in empty_buckets)


def test_health_limits_hidden_when_session_scoped(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        LimitSnapshotFact(
            fact_id="limit-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            account_key="codex:acct",
            window_id="5h",
            used_percent=95.0,
        )
    )
    hidden = aggregate.build_health(
        storage, _settings(), _full_range(), TelemetryFilter(models=["gpt-5"])
    )
    assert hidden.limits.hidden is True
    assert hidden.limits.current == []
    assert hidden.limits.series == []

    visible = aggregate.build_health(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    assert visible.limits.hidden is False
    assert len(visible.limits.current) == 1
    assert visible.limits.current[0].used_percent == 95.0


def test_current_limit_snapshot_stale_after_15_minutes(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    old = now - timedelta(minutes=20)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        LimitSnapshotFact(
            fact_id="limit-1",
            source="codex",
            session_id="s1",
            occurred_at=old,
            dims=_dims(),
            account_key="codex:acct",
            window_id="5h",
            used_percent=95.0,
        )
    )
    current = aggregate.current_limit_snapshots(storage, TelemetryFilter(), now)
    assert len(current) == 1
    assert current[0].stale is True
    # A stale snapshot never drives an alert/insight even above threshold.
    assert aggregate.alerting_limits(storage, _settings(), TelemetryFilter()) == []


# ── /drilldown ────────────────────────────────────────────────────────────


def test_drilldown_pagination_and_total_are_consistent(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    for i in range(5):
        storage.telemetry.ingest_fact(
            ToolCallFact(
                fact_id=f"tool-{i}",
                source="codex",
                session_id="s1",
                occurred_at=now + timedelta(seconds=i),
                dims=_dims(),
                tool_name="Read",
                outcome=ToolOutcome.SUCCEEDED,
            )
        )
    page1 = aggregate.build_drilldown(
        storage, _full_range(), TelemetryFilter(), TelemetryFactKind.TOOL_CALL, 1, 2
    )
    page2 = aggregate.build_drilldown(
        storage, _full_range(), TelemetryFilter(), TelemetryFactKind.TOOL_CALL, 2, 2
    )
    page3 = aggregate.build_drilldown(
        storage, _full_range(), TelemetryFilter(), TelemetryFactKind.TOOL_CALL, 3, 2
    )
    assert page1.total == page2.total == page3.total == 5
    assert len(page1.items) == 2
    assert len(page2.items) == 2
    assert len(page3.items) == 1
    all_ids = {item.fact_id for item in page1.items + page2.items + page3.items}
    assert all_ids == {f"tool-{i}" for i in range(5)}


# ── /activity ─────────────────────────────────────────────────────────────


def test_activity_daily_is_zero_not_missing_for_quiet_days(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:created",
            source="runtime",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.CREATED,
        )
    )
    rng = TelemetryRange(
        start=now - timedelta(days=2), end=now + timedelta(hours=1), tz="UTC"
    )
    activity = aggregate.build_activity(storage, rng, TelemetryFilter())
    assert len(activity.daily) == 3  # today + 2 prior quiet days
    assert sum(d.sessions_created for d in activity.daily) == 1
    quiet_days = [d for d in activity.daily if d.sessions_created == 0]
    assert len(quiet_days) == 2
    for day in quiet_days:
        assert day.user_turns == 0
        assert day.tool_calls == 0


# ── range/day helpers ──────────────────────────────────────────────────────


def test_day_range_single_day_range_yields_one_day() -> None:
    day_start = (
        datetime(2026, 3, 5, tzinfo=UTC)
        .astimezone()
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    rng = TelemetryRange(
        start=day_start.astimezone(UTC),
        end=day_start.astimezone(UTC) + timedelta(days=1),
        tz="UTC",
    )
    days = aggregate.day_range(rng)
    assert len(days) == 1


def test_range_key_is_stable_for_identical_bounds() -> None:
    rng_a = TelemetryRange(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
        tz="UTC",
    )
    rng_b = TelemetryRange(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
        tz="UTC",
    )
    assert aggregate.range_key(rng_a) == aggregate.range_key(rng_b)


# ── settings / delete ──────────────────────────────────────────────────────


def test_build_settings_reflects_configured_retention(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    settings = Settings(
        telemetry_retention_days=42, telemetry_rollup_retention_months=6
    )
    view = aggregate.build_settings(storage, settings)
    assert view.retention_days_facts == 42
    assert view.retention_months_rollups == 6
    assert view.external_export is False
    assert view.content_capture is False


def test_delete_all_clears_facts_rollups_and_dismissals(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:created",
            source="runtime",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.CREATED,
        )
    )
    storage.telemetry.dismiss_insight("near_limit", "somerange")
    result = aggregate.delete_all(storage)
    assert result.removed.facts == 1
    facts_left = storage.connection.execute(
        "SELECT COUNT(*) AS n FROM telemetry_facts"
    ).fetchone()
    assert facts_left["n"] == 0
    assert storage.telemetry.dismissed_insights("somerange") == set()
    # Transcript/session record untouched by a telemetry-only delete.
    assert storage.get_session("s1") is not None


# ── insight gates (FR-8, plan §2.5) ────────────────────────────────────────


def test_context_pressure_insight_fires_above_threshold(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        ContextSnapshotFact(
            fact_id="ctx-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            used_tokens=8000,
            window_tokens=10000,
            occupancy_percent=80.0,
        )
    )
    insights = telemetry_insights.compute_insights(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    context_insights = [i for i in insights if i.type == "context_pressure"]
    assert len(context_insights) == 1
    assert context_insights[0].severity == "info"  # 80% is >= 70 but < 90
    assert context_insights[0].signature == "context_pressure"


def test_context_pressure_insight_omitted_below_threshold(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        ContextSnapshotFact(
            fact_id="ctx-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            used_tokens=1000,
            window_tokens=10000,
            occupancy_percent=10.0,
        )
    )
    insights = telemetry_insights.compute_insights(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    assert [i for i in insights if i.type == "context_pressure"] == []


def test_near_limit_insight_omitted_when_session_scoped(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        LimitSnapshotFact(
            fact_id="limit-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            account_key="codex:acct",
            window_id="5h",
            used_percent=99.0,
        )
    )
    unscoped = telemetry_insights.compute_insights(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    assert any(i.type == "near_limit" for i in unscoped)

    scoped = telemetry_insights.compute_insights(
        storage, _settings(), _full_range(), TelemetryFilter(repos=["waypoint"])
    )
    assert not any(i.type == "near_limit" for i in scoped)


def test_dismissed_insight_is_omitted(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    storage.telemetry.ingest_fact(
        ContextSnapshotFact(
            fact_id="ctx-1",
            source="codex",
            session_id="s1",
            occurred_at=now,
            dims=_dims(),
            used_tokens=9000,
            window_tokens=10000,
            occupancy_percent=90.0,
        )
    )
    rng = _full_range()
    before = telemetry_insights.compute_insights(
        storage, _settings(), rng, TelemetryFilter()
    )
    assert any(i.type == "context_pressure" for i in before)

    storage.telemetry.dismiss_insight("context_pressure", aggregate.range_key(rng))
    after = telemetry_insights.compute_insights(
        storage, _settings(), rng, TelemetryFilter()
    )
    assert not any(i.type == "context_pressure" for i in after)


def _seed_tracked_turns(
    storage: Storage,
    session_id: str,
    occurred_ats: list[datetime],
    *,
    per_turn_tokens: int,
    prefix: str,
) -> None:
    for i, occurred_at in enumerate(occurred_ats):
        _seed_agent_turn(
            storage,
            session_id,
            fact_id=f"{prefix}:{i}",
            occurred_at=occurred_at,
            totals={"input_tokens": per_turn_tokens},
            display_total=per_turn_tokens,
        )


def test_token_volume_change_fires_when_all_gates_met(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    rng = TelemetryRange(start=now - timedelta(hours=2), end=now, tz="UTC")
    current_times = [now - timedelta(minutes=100 - 5 * i) for i in range(10)]
    previous_times = [now - timedelta(minutes=220 - 5 * i) for i in range(10)]
    _seed_tracked_turns(
        storage, "s1", previous_times, per_turn_tokens=2000, prefix="prev"
    )
    _seed_tracked_turns(
        storage, "s1", current_times, per_turn_tokens=3200, prefix="cur"
    )

    insights = telemetry_insights.compute_insights(
        storage, _settings(), rng, TelemetryFilter()
    )
    volume_insights = [i for i in insights if i.type == "token_volume_change"]
    assert len(volume_insights) == 1
    metrics = volume_insights[0].metrics
    assert metrics["current_total"] == 32000
    assert metrics["previous_total"] == 20000
    assert metrics["diff"] == 12000


def test_token_volume_change_omitted_when_meter_coverage_too_low(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    rng = TelemetryRange(start=now - timedelta(hours=2), end=now, tz="UTC")
    current_times = [now - timedelta(minutes=100 - 5 * i) for i in range(10)]
    previous_times = [now - timedelta(minutes=220 - 5 * i) for i in range(10)]
    _seed_tracked_turns(
        storage, "s1", previous_times, per_turn_tokens=2000, prefix="prev"
    )
    _seed_tracked_turns(
        storage, "s1", current_times[:2], per_turn_tokens=3200, prefix="cur"
    )
    # 8 more AGENT turns with no ledger row — meter coverage in the current
    # range drops to 2/10 = 20%, well under the 80% gate.
    for i in range(8):
        storage.telemetry.ingest_fact(
            TurnFact(
                fact_id=f"untracked:{i}",
                source="codex",
                session_id="s1",
                occurred_at=current_times[2 + i],
                dims=_dims(),
                turn_kind=TurnKind.AGENT,
            )
        )

    insights = telemetry_insights.compute_insights(
        storage, _settings(), rng, TelemetryFilter()
    )
    assert [i for i in insights if i.type == "token_volume_change"] == []


def test_token_volume_change_omitted_below_percent_gate(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    rng = TelemetryRange(start=now - timedelta(hours=2), end=now, tz="UTC")
    current_times = [now - timedelta(minutes=100 - 5 * i) for i in range(10)]
    previous_times = [now - timedelta(minutes=220 - 5 * i) for i in range(10)]
    # Absolute diff clears 10k (15000) but relative change is only 10%.
    _seed_tracked_turns(
        storage, "s1", previous_times, per_turn_tokens=15000, prefix="prev"
    )
    _seed_tracked_turns(
        storage, "s1", current_times, per_turn_tokens=16500, prefix="cur"
    )

    insights = telemetry_insights.compute_insights(
        storage, _settings(), rng, TelemetryFilter()
    )
    assert [i for i in insights if i.type == "token_volume_change"] == []
