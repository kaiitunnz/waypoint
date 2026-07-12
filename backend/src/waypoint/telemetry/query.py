"""Shared range/filter parsing for every ``/api/telemetry/*`` endpoint.

A single parser (``parse_range_filter``) reads the query params directly off
the request instead of each endpoint redeclaring the same dozen ``Query()``
parameters (CONTRACT.md §4). Day/range boundaries reuse the store's host-tz
day helpers so a "today" or "7d" preset lines up exactly with the daily
rollup buckets ``TelemetryStore`` maintains (both derive the calendar day the
same way), rather than drifting apart under two independent tz calculations.
"""

import calendar
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status

from waypoint.settings import Settings
from waypoint.telemetry.facts import TelemetryFilter, TelemetryRange
from waypoint.telemetry.store import _day_bounds_utc, _day_key

_VALID_PRESETS = ("today", "7d", "30d", "custom")
_VALID_SCOPES = ("all", "top_level", "children")


def host_tz_name() -> str:
    """The abbreviated name of the process's local timezone (e.g. ``UTC``, ``ICT``).

    Waypoint has no separate configured display timezone (CONTRACT.md §1c);
    this mirrors the store's ``_day_key`` which resolves calendar days
    against the same local system tz via naive ``astimezone()``.
    """
    return datetime.now().astimezone().tzname() or "UTC"


def host_utc_offset_minutes() -> int:
    """The host's current UTC offset in minutes east of UTC (e.g. Singapore = 480).

    A deterministic numeric companion to ``host_tz_name()`` (a ``tzname()``
    abbreviation that isn't a valid JS ``timeZone``): the frontend shifts each
    range instant by this offset and formats in UTC so the rendered calendar
    day matches the host-tz day the range actually covers.
    """
    offset = datetime.now().astimezone().utcoffset()
    return round(offset.total_seconds() / 60) if offset is not None else 0


def subtract_calendar_months(moment: datetime, months: int) -> datetime:
    """``moment`` shifted back ``months`` calendar months, clamping the day.

    Calendar-correct rollup retention: a naive ``months * 31`` days
    over-retains (it treats every month as its longest). Clamps the
    day-of-month so e.g. Mar 31 minus one month is Feb 28/29, never an invalid
    date.
    """
    month_index = moment.year * 12 + (moment.month - 1) - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    last_day = calendar.monthrange(year, month)[1]
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no")


def _parse_instant(raw: str, *, end_of_day: bool) -> datetime:
    """Parse a query-param date/datetime into a UTC instant.

    A bare ``YYYY-MM-DD`` date is interpreted as a host-tz calendar day
    boundary (its start, or its exclusive end-of-day when ``end_of_day``),
    matching the store's day-bucket alignment. A full ISO 8601 datetime is
    used as-is (naive values are assumed UTC).
    """
    try:
        if len(raw) == 10:
            day = raw
            start_iso, end_iso = _day_bounds_utc(day)
            return datetime.fromisoformat(end_iso if end_of_day else start_iso)
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid date/time: {raw!r}",
        ) from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def resolve_preset_range(preset: str, tz: str) -> TelemetryRange:
    now = datetime.now(UTC)
    today = _day_key(now)
    _, end = _day_bounds_utc(today)
    if preset == "today":
        start, _ = _day_bounds_utc(today)
    elif preset == "7d":
        start_day = _day_key(now - timedelta(days=6))
        start, _ = _day_bounds_utc(start_day)
    elif preset == "30d":
        start_day = _day_key(now - timedelta(days=29))
        start, _ = _day_bounds_utc(start_day)
    else:  # pragma: no cover - guarded by the caller
        raise ValueError(preset)
    return TelemetryRange(
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        tz=tz,
        utc_offset_minutes=host_utc_offset_minutes(),
    )


def parse_range_filter(
    request: Request, settings: Settings
) -> tuple[TelemetryRange, TelemetryFilter]:
    """Resolve the effective ``(TelemetryRange, TelemetryFilter)`` for a request.

    ``settings`` is accepted (per CONTRACT.md §4's ``parse_range_filter(request,
    settings)`` signature) for a future host-tz setting; today the host has no
    configured display timezone so range boundaries derive from the local
    system tz regardless.
    """
    del settings
    params = request.query_params
    tz = host_tz_name()

    preset = params.get("preset")
    start_raw = params.get("start")
    end_raw = params.get("end")

    if preset is not None and preset not in _VALID_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown preset: {preset!r}",
        )

    if preset in ("today", "7d", "30d"):
        rng = resolve_preset_range(preset, tz)
    elif preset == "custom" or start_raw is not None or end_raw is not None:
        if start_raw is None or end_raw is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="a custom range requires both 'start' and 'end'",
            )
        start = _parse_instant(start_raw, end_of_day=False)
        end = _parse_instant(end_raw, end_of_day=True)
        if end <= start:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'end' must be after 'start'",
            )
        rng = TelemetryRange(
            start=start, end=end, tz=tz, utc_offset_minutes=host_utc_offset_minutes()
        )
    else:
        rng = resolve_preset_range("7d", tz)

    scope = params.get("scope", "all")
    if scope not in _VALID_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"unknown scope: {scope!r}"
        )

    flt = TelemetryFilter.model_validate(
        {
            "backends": params.getlist("backend"),
            "models": params.getlist("model"),
            "repos": params.getlist("repo"),
            "tags": params.getlist("tag"),
            "sources": params.getlist("source"),
            "transports": params.getlist("transport"),
            "parent_scope": scope,
            "parent_session_id": params.get("parent"),
            "include_descendants": _parse_bool(params.get("descendants"), default=True),
        }
    )
    return rng, flt
