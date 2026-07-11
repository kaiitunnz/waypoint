"""Shaper: turns ``TelemetryStore`` queries into the PR1 API DTOs.

Analogous to ``usage_dashboard.py`` but reading the telemetry fact/rollup
tables instead of live session snapshots. Token totals are computed by
walking ``AGENT`` ``TurnFact`` rows and joining back to the per-turn ledger
(``session_token_usage_records``) exactly like ``TelemetryStore``'s own
rollup recompute does (``_agent_turn_tokens``) — the rollup itself doesn't
persist enough detail (which individual turns lacked a ledger match) to
derive meter coverage, so the fact-level walk is authoritative here and also
naturally supports the arbitrary (non-day-aligned) windows insights compare.

Coverage vocabulary (``entire``/``tracked_since``/``partial``): ``entire``
when every matching agent turn has a ledger record (or there are none to
miss); ``tracked_since`` when the one-time history backfill hasn't completed
yet (older gaps are a backfill boundary, not a real loss); ``partial``
otherwise (backfill is done and turns are still missing ledger rows — a
genuine data gap).
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from waypoint.schemas import SessionRecord, SessionStatus
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.api_models import (
    ActivityDaily,
    ActivityHeatmapCell,
    ContextSeriesPoint,
    ContextSnapshotView,
    DrilldownItem,
    LimitSeries,
    LimitSeriesPoint,
    LimitSnapshotView,
    SessionCounts,
    TelemetryActivity,
    TelemetryAlerts,
    TelemetryCoverageInfo,
    TelemetryDeleteCounts,
    TelemetryDeleteResponse,
    TelemetryDrilldown,
    TelemetryHealth,
    TelemetryHealthContext,
    TelemetryHealthLimits,
    TelemetryOverview,
    TelemetrySettingsResponse,
    TelemetryTokens,
    TokenCoverage,
    TokenGroup,
    TokenGroupBy,
    TokenSeriesPoint,
    TokenTotals,
    TurnCounts,
)
from waypoint.telemetry.facts import (
    ACTIVE_TRANSITIONS,
    LifecycleTransition,
    TelemetryFactKind,
    TelemetryFilter,
    TelemetryRange,
    TurnKind,
)
from waypoint.telemetry.store import _day_bounds_utc, _day_key, _parse_tag_term
from waypoint.telemetry.tokens import unify_tokens

# A snapshot is "current" (CONTRACT.md §4) for 15 minutes or until its
# provider-declared reset, whichever is sooner.
_SNAPSHOT_FRESH_WINDOW = timedelta(minutes=15)

_ACTIVE_STATUSES = frozenset(
    {
        SessionStatus.STARTING,
        SessionStatus.RUNNING,
        SessionStatus.IDLE,
        SessionStatus.WAITING_INPUT,
    }
)

_LIMIT_CARD_HIDDEN_REASON = (
    "provider rate limits are account-wide, not session-attributable; clear "
    "the model/repo/tag/source/transport/parent filter to view them"
)

ALL_TIME_RANGE = TelemetryRange(
    start=datetime(1970, 1, 1, tzinfo=UTC),
    end=datetime(2100, 1, 1, tzinfo=UTC),
    tz="UTC",
)


# ── shared range/day/hour helpers ─────────────────────────────────────────


def day_range(rng: TelemetryRange) -> list[str]:
    """Every host-tz calendar day with at least one instant inside ``[start, end)``."""
    days: list[str] = []
    current = date.fromisoformat(_day_key(rng.start))
    while True:
        day_str = current.isoformat()
        start_iso, _ = _day_bounds_utc(day_str)
        if datetime.fromisoformat(start_iso) >= rng.end:
            break
        days.append(day_str)
        current += timedelta(days=1)
    return days


def hour_range(rng: TelemetryRange) -> list[datetime]:
    """Hourly bucket starts (UTC) covering ``[start, end)`` — gaps stay explicit nulls."""
    start = rng.start.replace(minute=0, second=0, microsecond=0)
    buckets: list[datetime] = []
    cursor = start
    while cursor < rng.end:
        buckets.append(cursor)
        cursor += timedelta(hours=1)
    return buckets


def range_key(rng: TelemetryRange) -> str:
    """A stable key for insight dismissal — identical resolved ranges collapse together."""
    return f"{rng.start.isoformat()}|{rng.end.isoformat()}"


# ── token totals (facts + ledger join) ────────────────────────────────────


def agent_turn_rows(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> list[dict[str, Any]]:
    return [
        row
        for row in storage.telemetry.query_facts(TelemetryFactKind.TURN, rng, flt)
        if row["turn_kind"] == TurnKind.AGENT
    ]


def ledger_rows_for_sessions(
    storage: Storage, session_ids: set[str]
) -> dict[tuple[str, str, str], dict[str, Any]]:
    if not session_ids:
        return {}
    placeholders = ", ".join("?" for _ in session_ids)
    rows = storage.connection.execute(
        "SELECT session_id, source, record_id, usage_json "
        f"FROM session_token_usage_records WHERE session_id IN ({placeholders})",
        list(session_ids),
    ).fetchall()
    ledger: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        try:
            usage = json.loads(row["usage_json"])
        except json.JSONDecodeError:
            continue
        if isinstance(usage, dict):
            ledger[(row["session_id"], row["source"], row["record_id"])] = usage
    return ledger


def fold_tokens(
    rows: list[dict[str, Any]], ledger: dict[tuple[str, str, str], dict[str, Any]]
) -> tuple[dict[str, int], int | None, int, int]:
    """Fold a set of AGENT ``TurnFact`` rows into ``(totals, display_total, tracked, total)``.

    Each tracked row's raw ledger totals are mapped through
    ``unify_tokens(row.source, ...)`` before folding, onto the 5 disjoint
    buckets (``waypoint.schemas.TOKEN_USAGE_CATEGORIES``); because those
    buckets never overlap for any backend, ``display_total`` is simply their
    sum and is always safe — no backend-declared total to trust or gate on.
    ``tracked`` is the number of turns with a resolvable ledger record;
    ``total`` is ``len(rows)``.
    """
    totals: dict[str, int] = {}
    tracked = 0
    for row in rows:
        usage = ledger.get((row["session_id"], row["source"], row["fact_id"]))
        if usage is None:
            continue
        tracked += 1
        unified = unify_tokens(row["source"], usage.get("totals") or {})
        for category, amount in unified.items():
            totals[category] = totals.get(category, 0) + amount
    display_total = sum(totals.values())
    return totals, display_total, tracked, len(rows)


def grand_total(totals: dict[str, int], display_total: int | None) -> int:
    """A single comparable token quantity for insight gating (CONTRACT.md §4/§7).

    ``display_total`` (``fold_tokens``'s unconditional sum of the unified
    buckets) is always the safe grand total now; the ``None`` branch is dead
    but kept so ``TokenTotals.display_total``'s optional type still type-checks.
    """
    return display_total if display_total is not None else sum(totals.values())


def coverage_label(
    storage: Storage, meter_coverage_percent: float | None
) -> TokenCoverage:
    if meter_coverage_percent is None or meter_coverage_percent >= 100.0:
        return "entire"
    if storage.telemetry.get_meta("backfill_done") != "true":
        return "tracked_since"
    return "partial"


# ── current + series snapshots (context/limit health) ────────────────────


def _is_stale(
    occurred_at: datetime, now: datetime, resets_at: datetime | None = None
) -> bool:
    if now - occurred_at > _SNAPSHOT_FRESH_WINDOW:
        return True
    return resets_at is not None and now >= resets_at


def current_context_snapshots(
    storage: Storage, flt: TelemetryFilter, now: datetime
) -> list[ContextSnapshotView]:
    # "Current" context occupancy is a property of sessions that are still
    # live: an exited/errored session holds no context window (FR-6). Restrict
    # to sessions whose latest status is active so the panel shows the handful
    # of running sessions, not every session that ever recorded a snapshot.
    active_ids = {
        session.id
        for session in storage.list_sessions()
        if session.status in _ACTIVE_STATUSES
    }
    if not active_ids:
        return []
    rows = storage.telemetry.query_facts(
        TelemetryFactKind.CONTEXT_SNAPSHOT, ALL_TIME_RANGE, flt
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["session_id"] not in active_ids:
            continue
        existing = latest.get(row["session_id"])
        if existing is None or row["occurred_at"] > existing["occurred_at"]:
            latest[row["session_id"]] = row
    views = []
    for session_id, row in latest.items():
        occurred_at = datetime.fromisoformat(row["occurred_at"])
        views.append(
            ContextSnapshotView(
                session_id=session_id,
                used=row["used_tokens"],
                window=row["window_tokens"],
                percent=row["occupancy_percent"],
                stale=_is_stale(occurred_at, now),
                updated_at=occurred_at,
            )
        )
    return sorted(views, key=lambda v: v.session_id)


def _is_real_account(account_key: str) -> bool:
    """A verified account, not the per-session pseudonym fallback.

    Provider limits are account-scoped (FR-6); a session with no verified
    account gets a ``session:<id>`` placeholder key at ingest, which must not
    surface as its own account row and fragment the limit view.
    """
    return not account_key.startswith("session:")


def current_limit_snapshots(
    storage: Storage, flt: TelemetryFilter, now: datetime, settings: Settings
) -> list[LimitSnapshotView]:
    rows = storage.telemetry.query_facts(
        TelemetryFactKind.LIMIT_SNAPSHOT, ALL_TIME_RANGE, flt
    )
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not _is_real_account(row["account_key"]):
            continue
        key = (row["backend"], row["account_key"], row["window_id"])
        existing = latest.get(key)
        if existing is None or row["occurred_at"] > existing["occurred_at"]:
            latest[key] = row
    views = []
    for (backend, account_key, window_id), row in latest.items():
        occurred_at = datetime.fromisoformat(row["occurred_at"])
        resets_at = (
            datetime.fromisoformat(row["resets_at"]) if row["resets_at"] else None
        )
        views.append(
            LimitSnapshotView(
                backend=backend,
                account_key=account_key,
                account_label=(
                    row["account_label"] if settings.telemetry_local_labels else None
                ),
                window_id=window_id,
                label=row["window_label"],
                used_percent=row["used_percent"],
                resets_at=resets_at,
                stale=_is_stale(occurred_at, now, resets_at),
                updated_at=occurred_at,
            )
        )
    return sorted(views, key=lambda v: (v.backend, v.account_key, v.window_id))


def severity_for_percent(
    percent: float, thresholds: tuple[int, int, int]
) -> str | None:
    low, high, critical = thresholds
    if percent >= critical:
        return "critical"
    if percent >= high:
        return "warning"
    if percent >= low:
        return "info"
    return None


def alerting_context(
    storage: Storage, settings: Settings, flt: TelemetryFilter
) -> list[ContextSnapshotView]:
    low = settings.telemetry_context_thresholds[0]
    now = datetime.now(UTC)
    return [
        snapshot
        for snapshot in current_context_snapshots(storage, flt, now)
        if not snapshot.stale
        and snapshot.percent is not None
        and snapshot.percent >= low
    ]


def alerting_limits(
    storage: Storage, settings: Settings, flt: TelemetryFilter
) -> list[LimitSnapshotView]:
    if flt.has_session_scoping():
        return []
    low = settings.telemetry_context_thresholds[0]
    now = datetime.now(UTC)
    return [
        snapshot
        for snapshot in current_limit_snapshots(storage, flt, now, settings)
        if not snapshot.stale and snapshot.used_percent >= low
    ]


# ── live session filtering (point-in-time counts) ─────────────────────────


def _descendant_session_ids(sessions: list[SessionRecord], root_id: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for session in sessions:
        if session.spawner_session_id is not None:
            children.setdefault(session.spawner_session_id, []).append(session.id)
    found: set[str] = set()
    queue = list(children.get(root_id, []))
    while queue:
        current = queue.pop()
        if current in found or current == root_id:
            continue
        found.add(current)
        queue.extend(children.get(current, []))
    return found


def _session_matches_filter(
    session: SessionRecord, flt: TelemetryFilter, descendant_ids: set[str]
) -> bool:
    if flt.backends and session.backend not in flt.backends:
        return False
    if flt.repos and (session.repo_name or "") not in flt.repos:
        return False
    if flt.sources and session.source not in flt.sources:
        return False
    if flt.transports and session.transport not in flt.transports:
        return False
    if flt.parent_scope == "top_level" and session.spawner_session_id is not None:
        return False
    if flt.parent_scope == "children" and session.spawner_session_id is None:
        return False
    if flt.parent_session_id:
        if flt.include_descendants:
            if session.id != flt.parent_session_id and session.id not in descendant_ids:
                return False
        # Excluding descendants means the parent's OWN session only — not
        # "only its direct children" (that would be backwards).
        elif session.id != flt.parent_session_id:
            return False
    for term in flt.tags:
        parsed = _parse_tag_term(term)
        if parsed is None:
            continue
        key, value = parsed
        if session.tags.get(key) != value:
            return False
    return True


def count_active_sessions(
    storage: Storage, flt: TelemetryFilter, rng: TelemetryRange
) -> int:
    """Point-in-time count of sessions active at ``rng.end`` matching ``flt`` (FR-3).

    "Active now" means the state at the range's end instant, not something
    summed over the range. When ``rng.end`` covers the live present, the
    cheap live-``SessionRecord.status`` shortcut is exact and avoids a fact
    scan. For a wholly historical range (``rng.end`` in the past) live status
    reflects the session's state *now*, not at ``rng.end``, so it must instead
    be reconstructed from the latest ``SessionLifecycleFact.transition``
    recorded before that instant.
    """
    if rng.end >= datetime.now(UTC):
        return _count_active_sessions_live(storage, flt)
    return _count_active_sessions_historical(storage, flt, rng.end)


def _count_active_sessions_live(storage: Storage, flt: TelemetryFilter) -> int:
    sessions = storage.list_sessions()
    descendant_ids = (
        _descendant_session_ids(sessions, flt.parent_session_id)
        if flt.parent_session_id
        else set()
    )
    return sum(
        1
        for session in sessions
        if session.status in _ACTIVE_STATUSES
        and _session_matches_filter(session, flt, descendant_ids)
    )


def _count_active_sessions_historical(
    storage: Storage, flt: TelemetryFilter, at: datetime
) -> int:
    as_of_range = TelemetryRange(
        start=ALL_TIME_RANGE.start, end=at, tz=ALL_TIME_RANGE.tz
    )
    rows = storage.telemetry.query_facts(
        TelemetryFactKind.SESSION_LIFECYCLE, as_of_range, flt
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = latest.get(row["session_id"])
        if existing is None or row["occurred_at"] > existing["occurred_at"]:
            latest[row["session_id"]] = row
    return sum(1 for row in latest.values() if row["transition"] in ACTIVE_TRANSITIONS)


# ── /overview ─────────────────────────────────────────────────────────────


def _needs_fact_scan_for_session_counts(flt: TelemetryFilter) -> bool:
    """Whether lifecycle/turn/tool counts must come from a fact scan, not the rollup.

    ``query_rollup`` is keyed on ``(day, backend, model, repo, source,
    transport, is_child)`` — it has no tag or parent-session dimension. Under
    a ``flt.tags`` or ``flt.parent_session_id`` filter, using it anyway would
    silently mix UNFILTERED session/turn/tool counts with the (correctly)
    FILTERED fact-derived token totals in the same response. Fact scans
    respect every ``TelemetryFilter`` dimension via ``_filter_clause``, so
    fall back to them whenever the rollup can't represent the active filter.
    """
    return bool(flt.tags) or flt.parent_session_id is not None


def _lifecycle_turn_tool_totals_from_rollup(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> tuple[dict[str, int], int, int, int]:
    lifecycle: dict[str, int] = {}
    turns_user = turns_agent = tool_calls = 0
    for row in storage.telemetry.query_rollup(rng, flt):
        metrics = json.loads(row["metrics_json"])
        for transition, count in (metrics.get("lifecycle") or {}).items():
            lifecycle[transition] = lifecycle.get(transition, 0) + count
        turns_user += metrics.get("turns_user", 0)
        turns_agent += metrics.get("turns_agent", 0)
        tool_calls += metrics.get("tool_calls", 0)
    return lifecycle, turns_user, turns_agent, tool_calls


def _lifecycle_turn_tool_totals_from_facts(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> tuple[dict[str, int], int, int, int]:
    lifecycle: dict[str, int] = {}
    for row in storage.telemetry.query_facts(
        TelemetryFactKind.SESSION_LIFECYCLE, rng, flt
    ):
        lifecycle[row["transition"]] = lifecycle.get(row["transition"], 0) + 1
    turns_user = turns_agent = 0
    for row in storage.telemetry.query_facts(TelemetryFactKind.TURN, rng, flt):
        if row["turn_kind"] == TurnKind.USER:
            turns_user += 1
        elif row["turn_kind"] == TurnKind.AGENT:
            turns_agent += 1
    tool_calls = storage.telemetry.count_facts(TelemetryFactKind.TOOL_CALL, rng, flt)
    return lifecycle, turns_user, turns_agent, tool_calls


def session_counts_totals(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> tuple[dict[str, int], int, int, int]:
    """Lifecycle/turn/tool totals for ``rng``/``flt`` (``lifecycle, user, agent, tool_calls``).

    Uses the daily rollup when it can represent every active filter
    dimension; falls back to a direct fact scan under a tag or
    parent-session filter, since the rollup has neither dimension (see
    ``_needs_fact_scan_for_session_counts``). Note: a ``model=`` filter
    always zeroes the lifecycle bucket either way — lifecycle facts carry no
    ``model_at_turn`` (lifecycle isn't model-attributable), so they never
    match a concrete model value in either the rollup or a fact scan.
    """
    if _needs_fact_scan_for_session_counts(flt):
        return _lifecycle_turn_tool_totals_from_facts(storage, rng, flt)
    return _lifecycle_turn_tool_totals_from_rollup(storage, rng, flt)


def build_overview(
    storage: Storage, settings: Settings, rng: TelemetryRange, flt: TelemetryFilter
) -> TelemetryOverview:
    """Shape the ``/api/telemetry/overview`` response.

    Note: for a sub-day custom range, session/turn/tool counts sourced from
    the daily rollup cover the WHOLE calendar day(s) touching ``rng`` (the
    rollup's own granularity — see ``query_rollup``), while the token totals
    below are always fact-derived and reflect the exact sub-range instant
    window. The two can diverge for hour-granularity queries; this is
    expected, not a bug (the fact-scan fallback above doesn't have this gap).
    """
    lifecycle_totals, turns_user, turns_agent, tool_calls = session_counts_totals(
        storage, rng, flt
    )

    rows = agent_turn_rows(storage, rng, flt)
    ledger = ledger_rows_for_sessions(storage, {row["session_id"] for row in rows})
    totals, display_total, tracked, total = fold_tokens(rows, ledger)
    meter_pct = (100.0 * tracked / total) if total else None

    hidden = flt.has_session_scoping()
    return TelemetryOverview(
        range=rng,
        filters_echo=flt,
        tokens=TokenTotals(
            totals=totals,
            display_total=display_total,
            safe_total=display_total is not None,
            coverage=coverage_label(storage, meter_pct),
            meter_coverage_percent=meter_pct,
        ),
        sessions=SessionCounts(
            created=lifecycle_totals.get("created", 0),
            exited=lifecycle_totals.get("exited", 0),
            interrupted=lifecycle_totals.get("interrupted", 0),
            error=lifecycle_totals.get("error", 0),
            active_now=count_active_sessions(storage, flt, rng),
        ),
        turns=TurnCounts(user=turns_user, agent=turns_agent),
        tool_calls=tool_calls,
        alerts=TelemetryAlerts(
            context=alerting_context(storage, settings, flt),
            limits=alerting_limits(storage, settings, flt),
        ),
        limit_card_hidden=hidden,
        limit_card_hidden_reason=_LIMIT_CARD_HIDDEN_REASON if hidden else None,
    )


# ── /tokens ───────────────────────────────────────────────────────────────


def _group_key_and_label(
    storage: Storage, group_by: TokenGroupBy
) -> tuple[Callable[[dict[str, Any]], str], Callable[[str], str]]:
    if group_by == "time":
        return (lambda row: _day_key(datetime.fromisoformat(row["occurred_at"]))), (
            lambda key: key
        )
    if group_by == "backend":
        return (lambda row: row["backend"]), (lambda key: key)
    if group_by == "model":
        return (lambda row: row["model_at_turn"] or ""), (
            lambda key: key or "(no model)"
        )
    if group_by == "repo":
        return (lambda row: row["repo_name"] or ""), (lambda key: key or "(no repo)")

    def _session_label(session_id: str) -> str:
        session = storage.get_session(session_id)
        return session.title if session is not None else session_id

    return (lambda row: row["session_id"]), _session_label


def build_tokens(
    storage: Storage,
    rng: TelemetryRange,
    flt: TelemetryFilter,
    group_by: TokenGroupBy = "time",
) -> TelemetryTokens:
    rows = agent_turn_rows(storage, rng, flt)
    ledger = ledger_rows_for_sessions(storage, {row["session_id"] for row in rows})

    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(
            _day_key(datetime.fromisoformat(row["occurred_at"])), []
        ).append(row)
    series = []
    for day in day_range(rng):
        totals, display_total, _tracked, _total = fold_tokens(
            by_day.get(day, []), ledger
        )
        start_iso, _ = _day_bounds_utc(day)
        series.append(
            TokenSeriesPoint(
                bucket_start=datetime.fromisoformat(start_iso),
                totals=totals,
                display_total=display_total,
            )
        )

    key_fn, label_fn = _group_key_and_label(storage, group_by)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(key_fn(row), []).append(row)
    groups = []
    for key in sorted(grouped):
        totals, display_total, tracked, total = fold_tokens(grouped[key], ledger)
        meter_pct = (100.0 * tracked / total) if total else None
        groups.append(
            TokenGroup(
                key=key,
                label=label_fn(key),
                totals=totals,
                display_total=display_total,
                coverage=coverage_label(storage, meter_pct),
            )
        )

    return TelemetryTokens(
        range=rng, filters_echo=flt, series=series, group_by=group_by, groups=groups
    )


# ── /activity ─────────────────────────────────────────────────────────────


def _empty_activity_bucket() -> dict[str, int]:
    return {"user_turns": 0, "agent_turns": 0, "tool_calls": 0, "sessions_created": 0}


def _activity_daily_from_rollup(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> dict[str, dict[str, int]]:
    by_day: dict[str, dict[str, int]] = {}
    for row in storage.telemetry.query_rollup(rng, flt):
        metrics = json.loads(row["metrics_json"])
        bucket = by_day.setdefault(row["day"], _empty_activity_bucket())
        bucket["user_turns"] += metrics.get("turns_user", 0)
        bucket["agent_turns"] += metrics.get("turns_agent", 0)
        bucket["tool_calls"] += metrics.get("tool_calls", 0)
        bucket["sessions_created"] += (metrics.get("lifecycle") or {}).get("created", 0)
    return by_day


def _activity_daily_from_facts(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> dict[str, dict[str, int]]:
    by_day: dict[str, dict[str, int]] = {}

    def bucket_for(occurred_at: str) -> dict[str, int]:
        day = _day_key(datetime.fromisoformat(occurred_at))
        return by_day.setdefault(day, _empty_activity_bucket())

    for row in storage.telemetry.query_facts(
        TelemetryFactKind.SESSION_LIFECYCLE, rng, flt
    ):
        if row["transition"] == LifecycleTransition.CREATED:
            bucket_for(row["occurred_at"])["sessions_created"] += 1
    for row in storage.telemetry.query_facts(TelemetryFactKind.TURN, rng, flt):
        bucket = bucket_for(row["occurred_at"])
        if row["turn_kind"] == TurnKind.USER:
            bucket["user_turns"] += 1
        elif row["turn_kind"] == TurnKind.AGENT:
            bucket["agent_turns"] += 1
    for row in storage.telemetry.query_facts(TelemetryFactKind.TOOL_CALL, rng, flt):
        bucket_for(row["occurred_at"])["tool_calls"] += 1
    return by_day


def build_activity(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> TelemetryActivity:
    by_day = (
        _activity_daily_from_facts(storage, rng, flt)
        if _needs_fact_scan_for_session_counts(flt)
        else _activity_daily_from_rollup(storage, rng, flt)
    )

    daily = [ActivityDaily(day=day, **by_day.get(day, {})) for day in day_range(rng)]

    heatmap_counts: dict[tuple[int, int], int] = {}
    for kind in (TelemetryFactKind.TURN, TelemetryFactKind.TOOL_CALL):
        for row in storage.telemetry.query_facts(kind, rng, flt):
            occurred = datetime.fromisoformat(row["occurred_at"]).astimezone()
            key = (occurred.weekday(), occurred.hour)
            heatmap_counts[key] = heatmap_counts.get(key, 0) + 1
    heatmap = [
        ActivityHeatmapCell(dow=dow, hour=hour, count=count)
        for (dow, hour), count in sorted(heatmap_counts.items())
    ]

    return TelemetryActivity(range=rng, filters_echo=flt, daily=daily, heatmap=heatmap)


# ── /health ───────────────────────────────────────────────────────────────


def _context_series(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> list[ContextSeriesPoint]:
    peaks: dict[datetime, float] = {}
    for row in storage.telemetry.query_facts(
        TelemetryFactKind.CONTEXT_SNAPSHOT, rng, flt
    ):
        percent = row["occupancy_percent"]
        if percent is None:
            continue
        bucket = datetime.fromisoformat(row["occurred_at"]).replace(
            minute=0, second=0, microsecond=0
        )
        peaks[bucket] = max(peaks.get(bucket, percent), percent)
    return [
        ContextSeriesPoint(bucket_start=bucket, peak_percent=peaks.get(bucket))
        for bucket in hour_range(rng)
    ]


def _limits_series(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter, settings: Settings
) -> list[LimitSeries]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in storage.telemetry.query_facts(
        TelemetryFactKind.LIMIT_SNAPSHOT, rng, flt
    ):
        if not _is_real_account(row["account_key"]):
            continue
        key = (row["backend"], row["account_key"], row["window_id"])
        groups.setdefault(key, []).append(row)

    buckets = hour_range(rng)
    result = []
    for (backend, account_key, window_id), group_rows in groups.items():
        label = None
        account_label = None
        best_per_bucket: dict[datetime, tuple[datetime, float]] = {}
        for row in group_rows:
            occurred = datetime.fromisoformat(row["occurred_at"])
            bucket = occurred.replace(minute=0, second=0, microsecond=0)
            existing = best_per_bucket.get(bucket)
            if existing is None or occurred > existing[0]:
                best_per_bucket[bucket] = (occurred, row["used_percent"])
            if row["window_label"]:
                label = row["window_label"]
            if row["account_label"]:
                account_label = row["account_label"]
        points = [
            LimitSeriesPoint(
                bucket_start=bucket,
                used_percent=(
                    best_per_bucket[bucket][1] if bucket in best_per_bucket else None
                ),
            )
            for bucket in buckets
        ]
        result.append(
            LimitSeries(
                backend=backend,
                account_key=account_key,
                account_label=(
                    account_label if settings.telemetry_local_labels else None
                ),
                window_id=window_id,
                label=label,
                points=points,
            )
        )
    return sorted(
        result,
        key=lambda series: (series.backend, series.account_key, series.window_id),
    )


def build_health(
    storage: Storage, settings: Settings, rng: TelemetryRange, flt: TelemetryFilter
) -> TelemetryHealth:
    now = datetime.now(UTC)
    hidden = flt.has_session_scoping()
    limits_current = (
        [] if hidden else current_limit_snapshots(storage, flt, now, settings)
    )
    limits_series = [] if hidden else _limits_series(storage, rng, flt, settings)
    return TelemetryHealth(
        range=rng,
        filters_echo=flt,
        context=TelemetryHealthContext(
            current=current_context_snapshots(storage, flt, now),
            series=_context_series(storage, rng, flt),
        ),
        limits=TelemetryHealthLimits(
            current=limits_current,
            series=limits_series,
            hidden=hidden,
            hidden_reason=_LIMIT_CARD_HIDDEN_REASON if hidden else None,
        ),
    )


# ── /drilldown ────────────────────────────────────────────────────────────


def _drilldown_label(row: dict[str, Any]) -> str:
    kind = row["kind"]
    if kind == TelemetryFactKind.SESSION_LIFECYCLE:
        return f"session {row['transition']}"
    if kind == TelemetryFactKind.TURN:
        return f"{row['turn_kind']} turn"
    if kind == TelemetryFactKind.TOOL_CALL:
        return f"{row['tool_name']} ({row['outcome']})"
    if kind == TelemetryFactKind.CONTEXT_SNAPSHOT:
        percent = row["occupancy_percent"]
        return f"context {percent:.0f}%" if percent is not None else "context snapshot"
    if kind == TelemetryFactKind.LIMIT_SNAPSHOT:
        label = row["window_label"] or row["window_id"]
        return f"{label} {row['used_percent']:.0f}%"
    return str(kind)


def _drilldown_item(row: dict[str, Any]) -> DrilldownItem:
    return DrilldownItem(
        session_id=row["session_id"],
        kind=row["kind"],
        fact_id=row["fact_id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        label=_drilldown_label(row),
        backend=row["backend"],
        model=row["model_at_turn"],
        repo_name=row["repo_name"],
        transition=row["transition"],
        turn_kind=row["turn_kind"],
        tool_name=row["tool_name"],
        tool_category=row["tool_category"],
        outcome=row["outcome"],
        duration_ms=row["duration_ms"],
        used_tokens=row["used_tokens"],
        window_tokens=row["window_tokens"],
        occupancy_percent=row["occupancy_percent"],
        account_key=row["account_key"],
        window_id=row["window_id"],
        used_percent=row["used_percent"],
    )


def build_drilldown(
    storage: Storage,
    rng: TelemetryRange,
    flt: TelemetryFilter,
    kind: TelemetryFactKind,
    page: int,
    page_size: int,
) -> TelemetryDrilldown:
    offset = (page - 1) * page_size
    # Newest-first: the first page should surface the most recent facts (with
    # their resolved outcomes) rather than the oldest, which skew toward
    # unpaired/unknown early history.
    rows = storage.telemetry.query_facts(
        kind, rng, flt, limit=page_size, offset=offset, descending=True
    )
    total = storage.telemetry.count_facts(kind, rng, flt)
    return TelemetryDrilldown(
        range=rng,
        filters_echo=flt,
        items=[_drilldown_item(row) for row in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


# ── /settings + DELETE /api/telemetry ─────────────────────────────────────

PRIVACY_STATEMENT = (
    "Telemetry stores only counts, timestamps, model ids, normalized tool names, "
    "and occupancy/limit percentages — never raw prompts, tool inputs/outputs, "
    "filenames, or paths. Repo names are basenames; tool names are bare "
    "identifiers. Nothing leaves this Waypoint instance."
)


def build_settings(storage: Storage, settings: Settings) -> TelemetrySettingsResponse:
    backfill_through_raw = storage.telemetry.get_meta("backfill_through")
    return TelemetrySettingsResponse(
        retention_days_facts=settings.telemetry_retention_days,
        retention_months_rollups=settings.telemetry_rollup_retention_months,
        coverage=TelemetryCoverageInfo(
            backfill_done=storage.telemetry.get_meta("backfill_done") == "true",
            backfill_through=(
                datetime.fromisoformat(backfill_through_raw)
                if backfill_through_raw
                else None
            ),
        ),
        privacy_statement=PRIVACY_STATEMENT,
        external_export=False,
        content_capture=False,
        nl_enabled=False,
    )


def delete_all(storage: Storage) -> TelemetryDeleteResponse:
    """Delete every retained telemetry fact/rollup/dismissal. Transcripts untouched."""
    far_future = datetime.now(UTC) + timedelta(days=1)
    removed = storage.telemetry.prune(
        facts_before=far_future, rollups_before=far_future
    )
    storage.connection.execute("DELETE FROM telemetry_insight_dismissal")
    storage.connection.commit()
    return TelemetryDeleteResponse(
        removed=TelemetryDeleteCounts(
            facts=removed["facts"], rollups=removed["rollups"]
        ),
        transcripts_unaffected=True,
    )
