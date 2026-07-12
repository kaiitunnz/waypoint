"""Response DTOs for the PR1 ``/api/telemetry/*`` endpoints (CONTRACT.md §4).

Every response echoes the resolved ``TelemetryRange``/``TelemetryFilter`` it
was computed against (``range``/``filters_echo``) so the frontend always
renders against the server's actual interpretation of the query, never its
own guess.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from waypoint.telemetry.facts import TelemetryFactKind, TelemetryFilter, TelemetryRange

TokenCoverage = Literal["entire", "tracked_since", "partial"]
TokenGroupBy = Literal["time", "backend", "model", "repo", "session"]
InsightType = Literal[
    "near_limit",
    "context_pressure",
    "token_volume_change",
    "orphan_data",
    "redundant_logs",
    "database_vacuum",
]
InsightSeverity = Literal["info", "warning", "critical"]


# ── shared sub-shapes ─────────────────────────────────────────────────────


class TokenTotals(BaseModel):
    totals: dict[str, int] = Field(default_factory=dict)
    # New-work total (fresh input + cache write + output + reasoning) — cache
    # reads are excluded (same prior context re-sent every turn, not new work)
    # and reported standalone via ``cached_read_tokens`` instead. Always safe to
    # sum: ``unify_tokens`` folds every backend onto disjoint buckets, so this
    # is always populated (the ``int | None`` type is retained only for wire
    # stability, never null in practice).
    display_total: int | None = None
    # Standalone cache-read total, already folded into ``totals["cache_read"]``
    # but broken out here so the UI can show it distinct from — never added to
    # — ``display_total``.
    cached_read_tokens: int = 0
    safe_total: bool = False
    coverage: TokenCoverage = "entire"
    meter_coverage_percent: float | None = None


class SessionCounts(BaseModel):
    created: int = 0
    exited: int = 0
    interrupted: int = 0
    error: int = 0
    active_now: int = 0


class TurnCounts(BaseModel):
    user: int = 0
    agent: int = 0


class ContextSnapshotView(BaseModel):
    session_id: str
    used: int
    window: int | None = None
    percent: float | None = None
    stale: bool = False
    updated_at: datetime


class LimitSnapshotView(BaseModel):
    backend: str
    account_key: str
    # Only populated when the ``telemetry_local_labels`` setting is on
    # (default off); ``None`` otherwise (FR-9 — ``account_key`` is always the
    # pseudonym, never a raw email/org).
    account_label: str | None = None
    # The user-chosen local profile name ("nus") or "Default" for a no-profile
    # session — never the raw OAuth email/org, so it's FR-9-safe and shown by
    # default (unlike ``account_label`` above, which needs local labels on).
    profile_label: str | None = None
    window_id: str
    label: str | None = None
    used_percent: float
    resets_at: datetime | None = None
    stale: bool = False
    updated_at: datetime


class TelemetryAlerts(BaseModel):
    context: list[ContextSnapshotView] = Field(default_factory=list)
    limits: list[LimitSnapshotView] = Field(default_factory=list)


# ── /overview ─────────────────────────────────────────────────────────────


class TelemetryOverview(BaseModel):
    range: TelemetryRange
    filters_echo: TelemetryFilter
    tokens: TokenTotals
    sessions: SessionCounts
    turns: TurnCounts
    tool_calls: int = 0
    alerts: TelemetryAlerts
    limit_card_hidden: bool = False
    limit_card_hidden_reason: str | None = None


# ── /tokens ───────────────────────────────────────────────────────────────


class TokenSeriesPoint(BaseModel):
    bucket_start: datetime
    totals: dict[str, int] = Field(default_factory=dict)
    display_total: int | None = None


class TokenGroup(BaseModel):
    key: str
    label: str
    totals: dict[str, int] = Field(default_factory=dict)
    # New-work total (see ``TokenTotals.display_total``) — excludes cache_read.
    display_total: int | None = None
    # See ``TokenTotals.cached_read_tokens``.
    cached_read_tokens: int = 0
    coverage: TokenCoverage = "entire"


class TelemetryTokens(BaseModel):
    range: TelemetryRange
    filters_echo: TelemetryFilter
    series: list[TokenSeriesPoint] = Field(default_factory=list)
    group_by: TokenGroupBy = "time"
    groups: list[TokenGroup] = Field(default_factory=list)


# ── /activity ─────────────────────────────────────────────────────────────


class ActivityDaily(BaseModel):
    day: str
    user_turns: int = 0
    agent_turns: int = 0
    tool_calls: int = 0
    sessions_created: int = 0


class ActivityHeatmapCell(BaseModel):
    dow: int
    hour: int
    count: int


class TelemetryActivity(BaseModel):
    range: TelemetryRange
    filters_echo: TelemetryFilter
    daily: list[ActivityDaily] = Field(default_factory=list)
    heatmap: list[ActivityHeatmapCell] = Field(default_factory=list)


# ── /health ───────────────────────────────────────────────────────────────


class ContextSeriesPoint(BaseModel):
    bucket_start: datetime
    peak_percent: float | None = None


class TelemetryHealthContext(BaseModel):
    current: list[ContextSnapshotView] = Field(default_factory=list)
    series: list[ContextSeriesPoint] = Field(default_factory=list)


class LimitSeriesPoint(BaseModel):
    bucket_start: datetime
    used_percent: float | None = None


class LimitSeries(BaseModel):
    backend: str
    account_key: str
    # See ``LimitSnapshotView.account_label`` — same local-labels gate.
    account_label: str | None = None
    # See ``LimitSnapshotView.profile_label`` — always shown, no gate.
    profile_label: str | None = None
    window_id: str
    label: str | None = None
    points: list[LimitSeriesPoint] = Field(default_factory=list)


class TelemetryHealthLimits(BaseModel):
    current: list[LimitSnapshotView] = Field(default_factory=list)
    series: list[LimitSeries] = Field(default_factory=list)
    hidden: bool = False
    hidden_reason: str | None = None


class TelemetryHealth(BaseModel):
    range: TelemetryRange
    filters_echo: TelemetryFilter
    context: TelemetryHealthContext
    limits: TelemetryHealthLimits


# ── /drilldown ────────────────────────────────────────────────────────────


class DrilldownItem(BaseModel):
    session_id: str
    kind: TelemetryFactKind
    fact_id: str
    occurred_at: datetime
    label: str
    backend: str | None = None
    model: str | None = None
    repo_name: str | None = None
    transition: str | None = None
    turn_kind: str | None = None
    tool_name: str | None = None
    tool_category: str | None = None
    outcome: str | None = None
    duration_ms: int | None = None
    used_tokens: int | None = None
    window_tokens: int | None = None
    occupancy_percent: float | None = None
    account_key: str | None = None
    window_id: str | None = None
    used_percent: float | None = None


class TelemetryDrilldown(BaseModel):
    range: TelemetryRange
    filters_echo: TelemetryFilter
    items: list[DrilldownItem] = Field(default_factory=list)
    page: int
    page_size: int
    total: int


# ── /insights ─────────────────────────────────────────────────────────────


class InsightClickThrough(BaseModel):
    endpoint: str
    params: dict[str, Any] = Field(default_factory=dict)


class Insight(BaseModel):
    signature: str
    type: InsightType
    statement: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    range: TelemetryRange
    filters: TelemetryFilter
    click_through: InsightClickThrough
    severity: InsightSeverity
    # Instance-health insights add an observation time and a safety note
    # describing why the candidate is safe/conditional and which maintenance
    # command or doc to consult (PRD FR-3/FR-6). Usage insights leave both null.
    observed_at: datetime | None = None
    safety_note: str | None = None


class TelemetryInsightsResponse(BaseModel):
    insights: list[Insight] = Field(default_factory=list)


class InsightDismissResponse(BaseModel):
    signature: str
    dismissed: bool = True


# ── /settings ─────────────────────────────────────────────────────────────


class TelemetryCoverageInfo(BaseModel):
    backfill_done: bool = False
    backfill_through: datetime | None = None


class TelemetrySettingsResponse(BaseModel):
    retention_days_facts: int
    retention_months_rollups: int
    coverage: TelemetryCoverageInfo
    privacy_statement: str
    external_export: bool = False
    content_capture: bool = False
    nl_enabled: bool = False


# ── DELETE /api/telemetry ─────────────────────────────────────────────────


class TelemetryDeleteCounts(BaseModel):
    facts: int
    rollups: int


class TelemetryDeleteResponse(BaseModel):
    removed: TelemetryDeleteCounts
    transcripts_unaffected: bool = True
