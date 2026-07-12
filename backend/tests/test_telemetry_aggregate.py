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
    assert totals == {
        "fresh_input": 100,
        "cache_read": 0,
        "cache_write": 0,
        "output": 0,
        "reasoning": 0,
    }
    assert display_total == 100  # unify_tokens's sum is always safe now
    assert tracked == 1
    assert total == 2


def test_fold_tokens_unifies_mixed_backends_without_double_counting(
    tmp_path: Path,
) -> None:
    """Regression: OpenCode's ``display_total_tokens=None`` used to poison a
    mixed-backend group's ``display_total`` to ``None``, and Codex's
    overlapping ``inputTokens``/``cachedInputTokens`` would double-count if
    ever summed verbatim. ``unify_tokens`` removes the dependency on the
    (sometimes-absent) declared total entirely, so the fold is always a safe,
    non-double-counted sum across every backend in the group."""
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    _seed_agent_turn(
        storage,
        "s1",
        fact_id="claude-turn",
        occurred_at=now,
        source="claude_code",
        totals={
            "input_tokens": 50,
            "cache_read_tokens": 10,
            "cache_creation_tokens": 5,
            "output_tokens": 20,
        },
    )
    _seed_agent_turn(
        storage,
        "s1",
        fact_id="codex-turn",
        occurred_at=now,
        source="codex",
        totals={
            "input_tokens": 100,  # TOTAL, includes cached_input_tokens
            "cached_input_tokens": 30,
            "output_tokens": 40,  # TOTAL, includes reasoning_output_tokens
            "reasoning_output_tokens": 10,
        },
    )
    _seed_agent_turn(
        storage,
        "s1",
        fact_id="opencode-turn",
        occurred_at=now,
        source="opencode",
        totals={
            "input_tokens": 60,
            "cache_read_tokens": 5,
            "cache_write_tokens": 2,
            "output_tokens": 25,  # TOTAL, includes reasoning_tokens
            "reasoning_tokens": 8,
        },
        display_total=None,  # OpenCode never declares one
    )

    rows = aggregate.agent_turn_rows(storage, _full_range(), TelemetryFilter())
    ledger = aggregate.ledger_rows_for_sessions(storage, {"s1"})
    totals, display_total, tracked, total = aggregate.fold_tokens(rows, ledger)

    assert totals == {
        "fresh_input": 50 + 70 + 60,
        "cache_read": 10 + 30 + 5,
        "cache_write": 5 + 0 + 2,
        "output": 20 + 30 + 17,
        "reasoning": 0 + 10 + 8,
    }
    # display_total is new-work only: cache_read (45) is excluded from the
    # 317 each-backend-unified grand total.
    provider_totals = 85 + 140 + 92
    assert display_total == provider_totals - 45
    assert display_total == sum(v for k, v in totals.items() if k != "cache_read")
    assert tracked == 3
    assert total == 3


def test_fold_tokens_display_total_excludes_cache_read(tmp_path: Path) -> None:
    """A cache-read-heavy turn's ``display_total`` is the small new-work
    number, not the cache-read-inflated grand total — cache reads are the
    same prior context re-sent every turn, not new work."""
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    _seed_agent_turn(
        storage,
        "s1",
        fact_id="t1",
        occurred_at=now,
        source="claude_code",
        totals={
            "input_tokens": 100,
            "cache_read_tokens": 579_000_000,
            "cache_creation_tokens": 50,
            "output_tokens": 200,
        },
    )

    rows = aggregate.agent_turn_rows(storage, _full_range(), TelemetryFilter())
    ledger = aggregate.ledger_rows_for_sessions(storage, {"s1"})
    totals, display_total, _tracked, _total = aggregate.fold_tokens(rows, ledger)

    assert totals["cache_read"] == 579_000_000
    # New work only: fresh_input + cache_write + output + reasoning.
    assert display_total == 100 + 50 + 200 + 0


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
    assert overview.tokens.totals == {
        "fresh_input": 100,
        "cache_read": 0,
        "cache_write": 0,
        "output": 20,
        "reasoning": 0,
    }
    assert overview.tokens.display_total == 120
    assert overview.tokens.cached_read_tokens == 0
    assert overview.tokens.safe_total is True
    assert overview.tokens.meter_coverage_percent == 100.0


def test_build_overview_reports_cached_read_tokens_standalone(tmp_path: Path) -> None:
    """The overview's ``display_total`` stays the small new-work number while
    ``cached_read_tokens`` carries the (much larger) cache-read volume
    separately (FR: #2, iteration 4)."""
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "s1")
    _seed_agent_turn(
        storage,
        "s1",
        fact_id="turn-1",
        occurred_at=now,
        source="claude_code",
        totals={
            "input_tokens": 500,
            "cache_read_tokens": 579_000_000,
            "output_tokens": 100,
        },
    )

    overview = aggregate.build_overview(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    assert overview.tokens.cached_read_tokens == 579_000_000
    assert overview.tokens.display_total == 600
    assert overview.tokens.display_total is not None
    assert overview.tokens.display_total < overview.tokens.cached_read_tokens


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


def test_build_overview_active_now_uses_live_status_for_current_range(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    _make_session(storage, "running", status=SessionStatus.RUNNING)
    _make_session(storage, "exited", status=SessionStatus.EXITED)

    # rng.end covers the live present -> the cheap live-status shortcut applies,
    # even though no lifecycle facts were seeded to back it.
    overview = aggregate.build_overview(
        storage, _settings(), _full_range(), TelemetryFilter()
    )
    assert overview.sessions.active_now == 1


def test_build_overview_active_now_reconstructs_from_facts_for_historical_range(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1", status=SessionStatus.EXITED)
    # Live status says EXITED, but at the historical instant below the
    # session's last known transition was RUNNING — active_now for a
    # wholly-past range must reflect *that*, not today's live status.
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="s1:running",
            source="runtime",
            session_id="s1",
            occurred_at=now - timedelta(days=10),
            dims=_dims(),
            transition=LifecycleTransition.RUNNING,
        )
    )
    historical_rng = TelemetryRange(
        start=now - timedelta(days=11), end=now - timedelta(days=9), tz="UTC"
    )
    overview = aggregate.build_overview(
        storage, _settings(), historical_rng, TelemetryFilter()
    )
    assert overview.sessions.active_now == 1

    # A range ending before the RUNNING transition was even recorded sees no
    # active sessions yet (facts, not live status, govern the past).
    earlier_rng = TelemetryRange(
        start=now - timedelta(days=20), end=now - timedelta(days=15), tz="UTC"
    )
    earlier_overview = aggregate.build_overview(
        storage, _settings(), earlier_rng, TelemetryFilter()
    )
    assert earlier_overview.sessions.active_now == 0


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
    assert overview.tokens.display_total == 0
    assert overview.tokens.safe_total is True
    assert overview.turns.user == 0
    assert overview.tool_calls == 0


def test_current_context_only_includes_active_sessions(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "live", status=SessionStatus.IDLE)
    _make_session(storage, "gone", status=SessionStatus.EXITED)
    for sid in ("live", "gone"):
        storage.telemetry.ingest_fact(
            ContextSnapshotFact(
                fact_id=f"{sid}:bucket",
                source="codex",
                session_id=sid,
                occurred_at=now - timedelta(minutes=1),
                dims=_dims(),
                used_tokens=1000,
                window_tokens=10000,
                occupancy_percent=10.0,
            )
        )
    views = aggregate.current_context_snapshots(storage, TelemetryFilter(), now)
    # The exited session holds no live context window and must not surface,
    # even though it recorded a recent snapshot before exiting.
    assert [v.session_id for v in views] == ["live"]


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
    current = aggregate.current_limit_snapshots(
        storage, TelemetryFilter(), now, _settings()
    )
    assert len(current) == 1
    assert current[0].stale is True
    # A stale snapshot never drives an alert/insight even above threshold.
    assert aggregate.alerting_limits(storage, _settings(), TelemetryFilter()) == []


def test_account_label_hidden_unless_local_labels_setting_is_on(
    tmp_path: Path,
) -> None:
    """FR-9: account_key is always the pseudonym; account_label is only ever
    surfaced by the API when telemetry_local_labels opts in (default off)."""
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
            account_key="acct_deadbeef",
            account_label="noppanat@u.nus.edu · plan: pro",
            window_id="5h",
            used_percent=42.0,
        )
    )

    default_settings = Settings(telemetry_context_thresholds=(70, 90, 100))
    assert default_settings.telemetry_local_labels is False
    hidden = aggregate.build_health(
        storage, default_settings, _full_range(), TelemetryFilter()
    )
    assert len(hidden.limits.current) == 1
    assert hidden.limits.current[0].account_key == "acct_deadbeef"
    assert hidden.limits.current[0].account_label is None
    assert hidden.limits.series[0].account_label is None

    labels_on = Settings(
        telemetry_context_thresholds=(70, 90, 100), telemetry_local_labels=True
    )
    visible = aggregate.build_health(
        storage, labels_on, _full_range(), TelemetryFilter()
    )
    assert visible.limits.current[0].account_label == "noppanat@u.nus.edu · plan: pro"
    assert visible.limits.series[0].account_label == "noppanat@u.nus.edu · plan: pro"


def test_profile_label_shown_regardless_of_local_labels_setting(
    tmp_path: Path,
) -> None:
    """Unlike ``account_label``, ``profile_label`` is a user-chosen local name
    (never the raw OAuth email/org) so it's always surfaced, default off or on."""
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
            account_key="acct_deadbeef",
            profile_label="nus",
            window_id="5h",
            used_percent=42.0,
        )
    )

    default_settings = Settings(telemetry_context_thresholds=(70, 90, 100))
    assert default_settings.telemetry_local_labels is False
    visible = aggregate.build_health(
        storage, default_settings, _full_range(), TelemetryFilter()
    )
    assert visible.limits.current[0].profile_label == "nus"
    assert visible.limits.series[0].profile_label == "nus"


def test_profile_label_picks_most_common_across_account_group(
    tmp_path: Path,
) -> None:
    """When an account groups sessions launched under different profiles, the
    account's display label is the most common one seen, not the latest."""
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    for i, label in enumerate(["nus", "nus", "work"]):
        storage.telemetry.ingest_fact(
            LimitSnapshotFact(
                fact_id=f"limit-{i}",
                source="codex",
                session_id="s1",
                occurred_at=now - timedelta(minutes=len(["nus", "nus", "work"]) - i),
                dims=_dims(),
                account_key="acct_deadbeef",
                profile_label=label,
                window_id="5h",
                used_percent=42.0,
            )
        )

    current = aggregate.current_limit_snapshots(
        storage, TelemetryFilter(), now, _settings()
    )
    assert len(current) == 1
    assert current[0].profile_label == "nus"


def test_profile_label_defaults_when_absent(tmp_path: Path) -> None:
    """A limit fact with no profile_label (e.g. pre-migration data) surfaces
    the humanized 'Default' rather than ``None``."""
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
            account_key="acct_deadbeef",
            window_id="5h",
            used_percent=42.0,
        )
    )

    current = aggregate.current_limit_snapshots(
        storage, TelemetryFilter(), now, _settings()
    )
    assert len(current) == 1
    assert current[0].profile_label == "Default"


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
    # One row per host-tz calendar day the range touches (derived, not a fixed
    # count — running near host-tz midnight makes ``now + 1h`` cross into an
    # extra day, which is correct: that day is partially in range).
    expected_days = aggregate.day_range(rng)
    assert [d.day for d in activity.daily] == expected_days
    assert sum(d.sessions_created for d in activity.daily) == 1
    quiet_days = [d for d in activity.daily if d.sessions_created == 0]
    assert len(quiet_days) == len(expected_days) - 1  # only the created day is busy
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


def test_token_volume_change_gates_on_new_work_total_not_cache_read(
    tmp_path: Path,
) -> None:
    """A huge cache-read swing between the two ranges must not, by itself,
    fire the insight — only a change in the new-work total counts (#2,
    iteration 4)."""
    storage = Storage(tmp_path / "db.sqlite")
    now = datetime.now(UTC)
    _make_session(storage, "s1")
    rng = TelemetryRange(start=now - timedelta(hours=2), end=now, tz="UTC")
    current_times = [now - timedelta(minutes=100 - 5 * i) for i in range(10)]
    previous_times = [now - timedelta(minutes=220 - 5 * i) for i in range(10)]
    # Identical new-work (fresh_input=2000/turn) in both ranges; only the
    # cache-read volume swings wildly (100k -> 900k per turn).
    for i, occurred_at in enumerate(previous_times):
        _seed_agent_turn(
            storage,
            "s1",
            fact_id=f"prev:{i}",
            occurred_at=occurred_at,
            totals={"input_tokens": 2000, "cache_read_tokens": 100_000},
        )
    for i, occurred_at in enumerate(current_times):
        _seed_agent_turn(
            storage,
            "s1",
            fact_id=f"cur:{i}",
            occurred_at=occurred_at,
            totals={"input_tokens": 2000, "cache_read_tokens": 900_000},
        )

    insights = telemetry_insights.compute_insights(
        storage, _settings(), rng, TelemetryFilter()
    )
    assert [i for i in insights if i.type == "token_volume_change"] == []


# ── fix-api regression tests (review findings 1-3) ────────────────────────


def test_overview_tag_filter_keeps_session_counts_consistent_with_tokens(
    tmp_path: Path,
) -> None:
    """Finding 1: query_rollup can't represent a tag filter, so the fact-scan
    fallback must be used for lifecycle/turn counts too — not just tokens."""
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "tagged")
    _make_session(storage, "untagged")

    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="tagged:created",
            source="runtime",
            session_id="tagged",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.CREATED,
        ),
        tags={"role": "lead"},
    )
    storage.telemetry.ingest_fact(
        SessionLifecycleFact(
            fact_id="untagged:created",
            source="runtime",
            session_id="untagged",
            occurred_at=now,
            dims=_dims(),
            transition=LifecycleTransition.CREATED,
        )
    )
    storage.record_token_usage(
        "tagged",
        TokenUsageRecord(
            record_id="tagged:turn",
            source="codex",
            observed_at=now,
            totals={"input_tokens": 500},
            display_total_tokens=500,
        ),
        init=TokenUsageInit(coverage="entire_waypoint_session", observed_from=now),
    )
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="tagged:turn",
            source="codex",
            session_id="tagged",
            occurred_at=now,
            dims=_dims(),
            turn_kind=TurnKind.AGENT,
        ),
        tags={"role": "lead"},
    )
    storage.record_token_usage(
        "untagged",
        TokenUsageRecord(
            record_id="untagged:turn",
            source="codex",
            observed_at=now,
            totals={"input_tokens": 900},
            display_total_tokens=900,
        ),
        init=TokenUsageInit(coverage="entire_waypoint_session", observed_from=now),
    )
    storage.telemetry.ingest_fact(
        TurnFact(
            fact_id="untagged:turn",
            source="codex",
            session_id="untagged",
            occurred_at=now,
            dims=_dims(),
            turn_kind=TurnKind.AGENT,
        )
    )

    overview = aggregate.build_overview(
        storage, _settings(), _full_range(), TelemetryFilter(tags=["role:lead"])
    )
    # Before the fix, sessions/turns came from the (tag-blind) rollup and
    # would include BOTH sessions while tokens (always fact-derived) included
    # only the tagged one — an internally inconsistent response.
    assert overview.sessions.created == 1
    assert overview.turns.agent == 1
    assert overview.tokens.totals == {
        "fresh_input": 500,
        "cache_read": 0,
        "cache_write": 0,
        "output": 0,
        "reasoning": 0,
    }
    assert overview.tokens.display_total == 500


def test_transitive_descendants_scope_tokens_and_drilldown(tmp_path: Path) -> None:
    """Finding 3: exclude-descendants means the parent's own facts only;
    include-descendants must resolve the full transitive descendant set."""
    storage = Storage(tmp_path / "db.sqlite")
    now = _make_session(storage, "parent")
    _make_session(storage, "child", spawner_session_id="parent")
    _make_session(storage, "grandchild", spawner_session_id="child")

    spawner_by_session = {"parent": None, "child": "parent", "grandchild": "child"}
    for session_id, tokens in (("parent", 100), ("child", 200), ("grandchild", 300)):
        spawner = spawner_by_session[session_id]
        dims = _dims(spawner_session_id=spawner, is_child=spawner is not None)
        storage.record_token_usage(
            session_id,
            TokenUsageRecord(
                record_id=f"{session_id}:turn",
                source="codex",
                observed_at=now,
                totals={"input_tokens": tokens},
                display_total_tokens=tokens,
            ),
            init=TokenUsageInit(coverage="entire_waypoint_session", observed_from=now),
        )
        storage.telemetry.ingest_fact(
            TurnFact(
                fact_id=f"{session_id}:turn",
                source="codex",
                session_id=session_id,
                occurred_at=now,
                dims=dims,
                turn_kind=TurnKind.AGENT,
            )
        )
        storage.telemetry.ingest_fact(
            ToolCallFact(
                fact_id=f"{session_id}:tool",
                source="codex",
                session_id=session_id,
                occurred_at=now,
                dims=dims,
                tool_name="Read",
                outcome=ToolOutcome.SUCCEEDED,
            )
        )

    include_all = aggregate.build_tokens(
        storage,
        _full_range(),
        TelemetryFilter(parent_session_id="parent", include_descendants=True),
        "session",
    )
    assert {g.key for g in include_all.groups} == {"parent", "child", "grandchild"}

    own_only = aggregate.build_tokens(
        storage,
        _full_range(),
        TelemetryFilter(parent_session_id="parent", include_descendants=False),
        "session",
    )
    assert {g.key for g in own_only.groups} == {"parent"}

    include_from_child = aggregate.build_tokens(
        storage,
        _full_range(),
        TelemetryFilter(parent_session_id="child", include_descendants=True),
        "session",
    )
    assert {g.key for g in include_from_child.groups} == {"child", "grandchild"}

    drilldown_all = aggregate.build_drilldown(
        storage,
        _full_range(),
        TelemetryFilter(parent_session_id="parent", include_descendants=True),
        TelemetryFactKind.TOOL_CALL,
        1,
        10,
    )
    assert drilldown_all.total == 3

    drilldown_own = aggregate.build_drilldown(
        storage,
        _full_range(),
        TelemetryFilter(parent_session_id="parent", include_descendants=False),
        TelemetryFactKind.TOOL_CALL,
        1,
        10,
    )
    assert drilldown_own.total == 1
    assert drilldown_own.items[0].session_id == "parent"
