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
InsightType = Literal["near_limit", "context_pressure", "token_volume_change"]
InsightSeverity = Literal["info", "warning", "critical"]


# ── shared sub-shapes ─────────────────────────────────────────────────────


class TokenTotals(BaseModel):
    totals: dict[str, int] = Field(default_factory=dict)
    # Only present when every contributing row supplied a safe, backend-declared
    # display total (CONTRACT.md §4: categories must not otherwise be summed).
    display_total: int | None = None
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
    display_total: int | None = None
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
