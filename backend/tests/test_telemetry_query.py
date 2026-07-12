"""Tests for the pure date helpers in ``telemetry.query`` (#10c)."""

from datetime import UTC, datetime

from waypoint.telemetry.query import subtract_calendar_months


def test_subtract_calendar_months_basic() -> None:
    assert subtract_calendar_months(datetime(2026, 7, 12, tzinfo=UTC), 1) == datetime(
        2026, 6, 12, tzinfo=UTC
    )


def test_subtract_calendar_months_crosses_year() -> None:
    assert subtract_calendar_months(datetime(2026, 2, 15, tzinfo=UTC), 13) == datetime(
        2025, 1, 15, tzinfo=UTC
    )


def test_subtract_calendar_months_clamps_day_of_month() -> None:
    # Mar 31 minus one month is Feb 28 (2026 is common), never invalid Feb 31.
    assert subtract_calendar_months(datetime(2026, 3, 31, tzinfo=UTC), 1) == datetime(
        2026, 2, 28, tzinfo=UTC
    )
    # Leap year clamps to Feb 29 instead.
    assert subtract_calendar_months(datetime(2024, 3, 31, tzinfo=UTC), 1) == datetime(
        2024, 2, 29, tzinfo=UTC
    )


def test_subtract_calendar_months_retains_less_than_naive_days() -> None:
    # The old formula was months * 31 days; the default 13-month retention
    # over-retained by treating every month as its longest.
    moment = datetime(2026, 7, 12, tzinfo=UTC)
    result = subtract_calendar_months(moment, 13)
    assert result == datetime(2025, 6, 12, tzinfo=UTC)
    assert (moment - result).days < 13 * 31
