"""Read/collection service behind the ``/api/telemetry/instance`` surface.

Serves the cached current snapshot immediately (labeled stale past a 5-minute
freshness window, unavailable past 24 hours) and never runs the multi-second
walk inline on a plain ``GET`` — an explicit refresh or the background/
maintenance path recomputes it off the request path (PRD FR-4). Also owns the
idempotent daily-history writer.
"""

import json
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.api_models import Insight
from waypoint.telemetry.instance.collect import collect_snapshot
from waypoint.telemetry.instance.insights import (
    INSTANCE_RANGE_KEY,
    compute_instance_insights,
)
from waypoint.telemetry.instance.model import (
    DataQuality,
    InstanceSnapshot,
)
from waypoint.telemetry.query import (
    host_tz_name,
    host_utc_offset_minutes,
    subtract_calendar_months,
)
from waypoint.telemetry.store import _day_key

# PRD FR-4: current cache freshness window and the stale-reuse ceiling.
CURRENT_MAX_AGE = timedelta(minutes=5)
STALE_CEILING = timedelta(hours=24)
# The maintenance-tick settle time — the first daily point is attempted on or
# after 00:05 host-local (except a genuine first-enablement point, written
# immediately).
DAILY_TICK_MINUTE = (0, 5)

_CLI_NOTE = (
    "Measured by Waypoint's read-only instance snapshot. `waypoint maintenance "
    "stats` reports a narrower subset and is the place to run the maintenance "
    "commands referenced here."
)


class InstanceHistoryPoint(BaseModel):
    day: str
    tz: str
    utc_offset_minutes: int
    observed_at: datetime
    data_quality: str
    total_bytes: int
    category_bytes: dict[str, int] = Field(default_factory=dict)


class TelemetryInstance(BaseModel):
    snapshot: InstanceSnapshot
    stale: bool = False
    stale_reason: str | None = None
    age_seconds: float | None = None
    refresh_due: bool = False
    insights: list[Insight] = Field(default_factory=list)
    history: list[InstanceHistoryPoint] = Field(default_factory=list)
    cli_note: str = _CLI_NOTE


def _unavailable_snapshot(now: datetime) -> InstanceSnapshot:
    return InstanceSnapshot(
        observed_at=now,
        tz=host_tz_name(),
        utc_offset_minutes=host_utc_offset_minutes(),
        data_quality=DataQuality.UNAVAILABLE,
        notes=["no current snapshot available"],
    )


def _load_current(storage: Storage) -> InstanceSnapshot | None:
    raw = storage.telemetry.get_instance_current()
    if raw is None:
        return None
    try:
        return InstanceSnapshot.model_validate_json(raw)
    except ValueError:
        return None


def _store_current(storage: Storage, snapshot: InstanceSnapshot) -> None:
    storage.telemetry.set_instance_current(snapshot.model_dump_json())


def _history(
    storage: Storage, settings: Settings, now: datetime
) -> list[InstanceHistoryPoint]:
    start = subtract_calendar_months(now, settings.telemetry_rollup_retention_months)
    rows = storage.telemetry.query_instance_history(
        start_day=_day_key(start), end_day=_day_key(now)
    )
    points: list[InstanceHistoryPoint] = []
    for row in rows:
        category_bytes: dict[str, int] = {}
        try:
            payload = json.loads(row["payload_json"])
            for cat in payload.get("categories", []):
                category_bytes[cat["category"]] = int(cat.get("bytes", 0))
        except (ValueError, KeyError, TypeError):
            category_bytes = {}
        points.append(
            InstanceHistoryPoint(
                day=row["day"],
                tz=row["tz"],
                utc_offset_minutes=int(row["utc_offset_minutes"]),
                observed_at=datetime.fromisoformat(row["observed_at"]),
                data_quality=row["data_quality"],
                total_bytes=int(row["total_bytes"]),
                category_bytes=category_bytes,
            )
        )
    return points


def compute_current_snapshot(
    storage: Storage, settings: Settings, *, now: datetime | None = None
) -> InstanceSnapshot:
    """Collect a fresh snapshot and refresh the current cache. Off the request path."""
    observed = now or datetime.now(UTC)
    snapshot = collect_snapshot(settings, now=observed)
    _store_current(storage, snapshot)
    return snapshot


def build_instance(
    storage: Storage,
    settings: Settings,
    *,
    refresh: bool = False,
    now: datetime | None = None,
) -> TelemetryInstance:
    now = now or datetime.now(UTC)

    if refresh:
        snapshot = compute_current_snapshot(storage, settings, now=now)
        stale, reason, refresh_due, age = False, None, False, 0.0
    else:
        cached = _load_current(storage)
        if cached is None:
            # First observation before any background/maintenance collection ran:
            # bootstrap synchronously this once, then the cache serves later reads.
            snapshot = compute_current_snapshot(storage, settings, now=now)
            stale, reason, refresh_due, age = False, None, False, 0.0
        else:
            age = max(0.0, (now - cached.observed_at).total_seconds())
            if age <= CURRENT_MAX_AGE.total_seconds():
                snapshot, stale, reason, refresh_due = cached, False, None, False
            elif age <= STALE_CEILING.total_seconds():
                snapshot = cached
                stale = True
                reason = "last observation is over 5 minutes old; revalidating"
                refresh_due = True
            else:
                snapshot = _unavailable_snapshot(now)
                stale = True
                reason = "last observation is over 24 hours old"
                refresh_due = True

    insights: list[Insight] = []
    if snapshot.data_quality != DataQuality.UNAVAILABLE:
        dismissed = storage.telemetry.dismissed_insights(INSTANCE_RANGE_KEY)
        insights = compute_instance_insights(snapshot, dismissed=dismissed)

    return TelemetryInstance(
        snapshot=snapshot,
        stale=stale,
        stale_reason=reason,
        age_seconds=age,
        refresh_due=refresh_due,
        insights=insights,
        history=_history(storage, settings, now),
    )


def record_instance_daily_if_due(
    storage: Storage,
    settings: Settings,
    *,
    now: datetime | None = None,
    snapshot: InstanceSnapshot | None = None,
) -> bool:
    """Write today's daily point if due (PRD FR-4).

    First-ever enablement writes immediately regardless of clock; otherwise the
    write waits until the host-local wall clock is at/after 00:05, retrying each
    later tick. A stored *complete* point for the date is never overwritten; a
    stored *partial* one is replaced. Also refreshes the current cache.
    """
    now = now or datetime.now(UTC)
    offset = host_utc_offset_minutes()
    day = _day_key(now)
    tz = host_tz_name()

    if storage.telemetry.instance_daily_quality(day, offset) == "complete":
        return False

    first_ever = storage.telemetry.instance_daily_count() == 0
    if not first_ever:
        local_now = now.astimezone()
        if (local_now.hour, local_now.minute) < DAILY_TICK_MINUTE:
            return False

    snap = snapshot or collect_snapshot(settings, now=now)
    _store_current(storage, snap)
    return storage.telemetry.upsert_instance_daily(
        day=day,
        utc_offset_minutes=offset,
        tz=tz,
        observed_at=snap.observed_at,
        data_quality=snap.data_quality.value,
        total_bytes=snap.total_bytes,
        payload_json=snap.model_dump_json(),
    )
