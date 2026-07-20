from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from waypoint.recurrence import (
    MISSED_RUN_GRACE_SECONDS,
    RecurrenceError,
    next_occurrence_after,
    next_occurrences,
    resolve_recurrence_base,
    validate_cron,
    validate_timezone,
)

NY = ZoneInfo("America/New_York")
SG = ZoneInfo("Asia/Singapore")


def test_validate_cron_accepts_five_fields() -> None:
    validate_cron("0 9 * * 1-5")
    validate_cron("*/15 * * * *")
    validate_cron("30 8 1,15 * *")


@pytest.mark.parametrize(
    "expr",
    [
        "0 9 * * ",  # four fields
        "0 9 * * 1-5 *",  # six fields
        "@daily",  # alias
        "0 0 30 2 *",  # impossible (Feb 30)
        "99 9 * * *",  # out of range
        "not a cron",
    ],
)
def test_validate_cron_rejects(expr: str) -> None:
    with pytest.raises(RecurrenceError):
        # ``next_occurrence_after`` also exercises the non-advancing guard for
        # the impossible expression, which ``validate_cron`` alone does not.
        validate_cron(expr)
        next_occurrence_after(expr, "UTC", datetime.now(UTC))


def test_validate_timezone() -> None:
    assert validate_timezone("Asia/Singapore") == SG
    with pytest.raises(RecurrenceError):
        validate_timezone("Not/AZone")
    with pytest.raises(RecurrenceError):
        validate_timezone("+08:00")


def test_next_occurrence_is_utc_and_strictly_after() -> None:
    after = datetime(2025, 7, 18, 12, 0, tzinfo=UTC)
    nxt = next_occurrence_after("0 9 * * *", "Asia/Singapore", after)
    assert nxt.tzinfo == UTC
    assert nxt > after
    # 09:00 SGT == 01:00 UTC, next day.
    assert nxt == datetime(2025, 7, 19, 1, 0, tzinfo=UTC)


def test_weekdays_skips_weekend() -> None:
    # 2025-07-18 is a Friday; next weekday 09:00 SGT is Monday 2025-07-21.
    after = datetime(2025, 7, 18, 12, 0, tzinfo=UTC)
    occ = next_occurrences("0 9 * * 1-5", "Asia/Singapore", after, 3)
    locals_ = [o.astimezone(SG) for o in occ]
    assert [d.strftime("%A %H:%M") for d in locals_] == [
        "Monday 09:00",
        "Tuesday 09:00",
        "Wednesday 09:00",
    ]


def test_dst_spring_forward_nonexistent_time_skipped() -> None:
    # US spring-forward 2025-03-09: 02:00 -> 03:00, so 02:30 does not exist.
    after = datetime(2025, 3, 8, 12, 0, tzinfo=UTC)
    occ = next_occurrences("30 2 * * *", "America/New_York", after, 3)
    locals_ = [o.astimezone(NY) for o in occ]
    # 2025-03-09 is skipped entirely; the series resumes 2025-03-10.
    assert [d.date().isoformat() for d in locals_] == [
        "2025-03-10",
        "2025-03-11",
        "2025-03-12",
    ]
    assert all(d.hour == 2 and d.minute == 30 for d in locals_)


def test_dst_fall_back_repeated_time_runs_once_at_earlier() -> None:
    # US fall-back 2025-11-02: 02:00 -> 01:00, so 01:30 occurs twice.
    after = datetime(2025, 11, 1, 12, 0, tzinfo=UTC)
    occ = next_occurrences("30 1 * * *", "America/New_York", after, 2)
    # The transition day yields a single occurrence at the earlier (EDT/-04:00)
    # instant, not both.
    assert occ[0] == datetime(2025, 11, 2, 5, 30, tzinfo=UTC)
    offset = occ[0].astimezone(NY).utcoffset()
    assert offset is not None and offset.total_seconds() == -4 * 3600
    # Following day is the ordinary EST occurrence.
    assert occ[1] == datetime(2025, 11, 3, 6, 30, tzinfo=UTC)


def test_grace_constant_is_named() -> None:
    assert MISSED_RUN_GRACE_SECONDS == 60.0


def test_resolve_base_uses_now_without_start() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    assert resolve_recurrence_base(now, "UTC", None) == now


def test_resolve_base_ignores_past_start() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    assert resolve_recurrence_base(now, "UTC", "2020-01-01T00:00") == now


def test_future_start_makes_first_occurrence_at_or_after_it() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    # Daily 09:00 SGT starting Fri 2026-07-24 → first run is 2026-07-24 09:00 SGT.
    base = resolve_recurrence_base(now, "Asia/Singapore", "2026-07-24T09:00")
    first = next_occurrence_after("0 9 * * *", "Asia/Singapore", base)
    assert first == datetime(2026, 7, 24, 1, 0, tzinfo=UTC)  # 09:00 SGT


def test_start_interpreted_in_recurrence_timezone() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    # 09:00 wall-clock resolves against the zone, not UTC/browser.
    base = resolve_recurrence_base(now, "America/New_York", "2026-07-24T09:00")
    first = next_occurrence_after("0 9 * * *", "America/New_York", base)
    assert first == datetime(2026, 7, 24, 13, 0, tzinfo=UTC)  # 09:00 EDT


def test_invalid_start_raises() -> None:
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    with pytest.raises(RecurrenceError):
        resolve_recurrence_base(now, "UTC", "not-a-date")
