"""Deterministic PR1 insights (CONTRACT.md §4/§7, plan §2.5).

Two rule types ship in PR1 — the ones that need no tool-outcome data:

- **near-limit / context pressure**: fires when a live provider-limit or
  context-window snapshot crosses ``settings.telemetry_context_thresholds``
  (70/90/100). Emitted as two independently dismissible ``type`` values —
  ``context_pressure`` for context occupancy, ``near_limit`` for provider
  limits — sharing the same threshold set and severity mapping.
- **token volume change**: fires only when the selected range's token volume
  moves >=25% AND >=10k tokens vs. the immediately preceding equal-length
  range, with >=10 tracked (meter-covered) agent turns and >=80% meter
  coverage in *both* ranges. Below any gate -> no insight (silence, never a
  hedged or low-confidence one).

A dismissed ``(signature, range_key)`` is omitted from the response entirely,
never merely marked read (CONTRACT.md §4).
"""

from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.aggregate import (
    agent_turn_rows,
    alerting_context,
    alerting_limits,
    fold_tokens,
    grand_total,
    ledger_rows_for_sessions,
    range_key,
    severity_for_percent,
)
from waypoint.telemetry.api_models import Insight, InsightClickThrough
from waypoint.telemetry.facts import TelemetryFilter, TelemetryRange

_VOLUME_CHANGE_MIN_PERCENT = 25.0
_VOLUME_CHANGE_MIN_TOKENS = 10_000
_VOLUME_CHANGE_MIN_TRACKED_TURNS = 10
_VOLUME_CHANGE_MIN_METER_COVERAGE_PERCENT = 80.0


def compute_insights(
    storage: Storage, settings: Settings, rng: TelemetryRange, flt: TelemetryFilter
) -> list[Insight]:
    candidates = [
        insight
        for insight in (
            _context_pressure_insight(storage, settings, rng, flt),
            _near_limit_insight(storage, settings, rng, flt),
            _token_volume_change_insight(storage, rng, flt),
        )
        if insight is not None
    ]
    dismissed = storage.telemetry.dismissed_insights(range_key(rng))
    return [insight for insight in candidates if insight.signature not in dismissed]


def _context_pressure_insight(
    storage: Storage, settings: Settings, rng: TelemetryRange, flt: TelemetryFilter
) -> Insight | None:
    alerts = alerting_context(storage, settings, flt)
    if not alerts:
        return None
    thresholds = settings.telemetry_context_thresholds
    worst = max(alerts, key=lambda a: a.percent or 0.0)
    assert worst.percent is not None  # alerting_context only keeps non-None percents
    severity = severity_for_percent(worst.percent, thresholds)
    if severity is None:
        return None
    return Insight(
        signature="context_pressure",
        type="context_pressure",
        statement=(
            f"Context usage is at {worst.percent:.0f}% for {len(alerts)} session(s), "
            f"including {worst.session_id} — at or above the {thresholds[0]}% threshold."
        ),
        metrics={
            "sessions_above_threshold": len(alerts),
            "worst_percent": worst.percent,
            "worst_session_id": worst.session_id,
            "thresholds": list(thresholds),
        },
        range=rng,
        filters=flt,
        click_through=InsightClickThrough(endpoint="/api/telemetry/health", params={}),
        severity=severity,
    )


def _near_limit_insight(
    storage: Storage, settings: Settings, rng: TelemetryRange, flt: TelemetryFilter
) -> Insight | None:
    alerts = alerting_limits(storage, settings, flt)
    if not alerts:
        return None
    thresholds = settings.telemetry_context_thresholds
    worst = max(alerts, key=lambda a: a.used_percent)
    severity = severity_for_percent(worst.used_percent, thresholds)
    if severity is None:
        return None
    label = worst.label or worst.window_id
    return Insight(
        signature="near_limit",
        type="near_limit",
        statement=(
            f"{worst.backend} {label} usage is at {worst.used_percent:.0f}% "
            f"— at or above the {thresholds[0]}% threshold."
        ),
        metrics={
            "limits_above_threshold": len(alerts),
            "worst_percent": worst.used_percent,
            "worst_backend": worst.backend,
            "worst_window_id": worst.window_id,
            "thresholds": list(thresholds),
        },
        range=rng,
        filters=flt,
        click_through=InsightClickThrough(endpoint="/api/telemetry/health", params={}),
        severity=severity,
    )


def _token_volume_change_insight(
    storage: Storage, rng: TelemetryRange, flt: TelemetryFilter
) -> Insight | None:
    duration = rng.end - rng.start
    previous_rng = TelemetryRange(start=rng.start - duration, end=rng.start, tz=rng.tz)

    current_rows = agent_turn_rows(storage, rng, flt)
    previous_rows = agent_turn_rows(storage, previous_rng, flt)
    ledger = ledger_rows_for_sessions(
        storage,
        {row["session_id"] for row in current_rows}
        | {row["session_id"] for row in previous_rows},
    )

    cur_totals, cur_display, cur_tracked, cur_total = fold_tokens(current_rows, ledger)
    prev_totals, prev_display, prev_tracked, prev_total = fold_tokens(
        previous_rows, ledger
    )

    cur_coverage = (100.0 * cur_tracked / cur_total) if cur_total else 0.0
    prev_coverage = (100.0 * prev_tracked / prev_total) if prev_total else 0.0
    if (
        cur_tracked < _VOLUME_CHANGE_MIN_TRACKED_TURNS
        or prev_tracked < _VOLUME_CHANGE_MIN_TRACKED_TURNS
        or cur_coverage < _VOLUME_CHANGE_MIN_METER_COVERAGE_PERCENT
        or prev_coverage < _VOLUME_CHANGE_MIN_METER_COVERAGE_PERCENT
    ):
        return None

    current_total = grand_total(cur_totals, cur_display)
    previous_total = grand_total(prev_totals, prev_display)
    diff = current_total - previous_total
    if abs(diff) < _VOLUME_CHANGE_MIN_TOKENS:
        return None

    percent_change: float | None
    if previous_total == 0:
        # An unambiguous jump off a zero baseline — nothing to divide by, but
        # the absolute-tokens gate above already guards against noise.
        percent_change = None
    else:
        percent_change = 100.0 * diff / previous_total
        if abs(percent_change) < _VOLUME_CHANGE_MIN_PERCENT:
            return None

    direction = "increased" if diff > 0 else "decreased"
    percent_text = f"{abs(percent_change):.0f}%" if percent_change is not None else "∞%"
    return Insight(
        signature="token_volume_change",
        type="token_volume_change",
        statement=(
            f"Token volume {direction} {percent_text} ({abs(diff):,} tokens) vs. the "
            "immediately preceding equal-length range."
        ),
        metrics={
            "current_total": current_total,
            "previous_total": previous_total,
            "diff": diff,
            "percent_change": percent_change,
            "current_tracked_turns": cur_tracked,
            "previous_tracked_turns": prev_tracked,
            "current_meter_coverage_percent": cur_coverage,
            "previous_meter_coverage_percent": prev_coverage,
        },
        range=rng,
        filters=flt,
        click_through=InsightClickThrough(endpoint="/api/telemetry/tokens", params={}),
        severity="info",
    )
