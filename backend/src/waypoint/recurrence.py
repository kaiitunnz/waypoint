"""Cron recurrence evaluation for scheduled sessions and messages.

Wraps ``croniter`` and ``zoneinfo`` behind the five-field grammar, timezone
validation, and this DST policy (wall-clock in the stored zone):

* A nonexistent local time during a forward transition (spring-forward) is
  skipped.
* A repeated local time during a backward transition (fall-back) runs once, at
  the earlier occurrence.

Occurrences are computed by iterating croniter in naive local time and
localizing each candidate with ``fold=0``; a candidate whose wall clock does
not survive a UTC round-trip is a spring-forward gap and is dropped.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, CroniterBadDateError, croniter

CRON_FIELD_COUNT = 5

# Seconds past its due time within which a recurring occurrence still fires;
# beyond this it is treated as a missed run (downtime) and skipped.
MISSED_RUN_GRACE_SECONDS = 60.0

# Bound on how far ahead we search for a next occurrence before treating the
# expression as non-advancing (e.g. an impossible day/month combination).
_MAX_SEARCH_ITERATIONS = 4 * 366


class RecurrenceError(ValueError):
    """A cron expression or timezone that Waypoint cannot schedule."""


def validate_timezone(timezone: str) -> ZoneInfo:
    """Return the :class:`ZoneInfo` for an IANA zone or raise ``RecurrenceError``."""
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RecurrenceError(
            f"invalid timezone: {timezone!r}; expected an IANA zone such as "
            "'Asia/Singapore' or 'America/New_York'"
        ) from exc


def validate_cron(expression: str) -> None:
    """Validate a five-field cron expression.

    Rejects anything but exactly five whitespace-separated fields (six/seven
    field, second-level, and ``@daily``-style aliases are unsupported), invalid
    field grammar, and expressions that never advance.
    """
    if expression.strip().startswith("@"):
        raise RecurrenceError(
            "cron aliases such as '@daily' are not supported; use five fields: "
            "minute hour day-of-month month day-of-week"
        )
    fields = expression.split()
    if len(fields) != CRON_FIELD_COUNT:
        raise RecurrenceError(
            f"cron must have exactly {CRON_FIELD_COUNT} fields "
            "(minute hour day-of-month month day-of-week), "
            f"got {len(fields)}"
        )
    try:
        croniter(expression)
    except (CroniterBadCronError, ValueError) as exc:
        raise RecurrenceError(f"invalid cron expression: {exc}") from exc


def _localize(naive: datetime, tz: ZoneInfo) -> datetime | None:
    """Attach ``tz`` to a naive local time, or ``None`` if it does not exist.

    ``fold=0`` selects the earlier instant for an ambiguous (fall-back) time. A
    nonexistent (spring-forward) time is detected by a UTC round-trip that no
    longer matches the requested wall clock.
    """
    aware = naive.replace(tzinfo=tz, fold=0)
    roundtrip = aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if roundtrip != naive:
        return None
    return aware


def next_occurrences(
    expression: str, timezone: str, after: datetime, count: int
) -> list[datetime]:
    """Return the next ``count`` UTC occurrences strictly after ``after``.

    Applies the module DST policy. Raises ``RecurrenceError`` for an invalid
    expression/timezone or one that cannot yield a finite next occurrence.
    """
    if count <= 0:
        return []
    validate_cron(expression)
    tz = validate_timezone(timezone)
    base_naive = after.astimezone(tz).replace(tzinfo=None)
    iterator = croniter(expression, base_naive)
    occurrences: list[datetime] = []
    iterations = 0
    while len(occurrences) < count:
        iterations += 1
        if iterations > _MAX_SEARCH_ITERATIONS:
            raise RecurrenceError(
                f"cron expression {expression!r} has no upcoming occurrence"
            )
        try:
            naive = iterator.get_next(datetime)
        except CroniterBadDateError as exc:
            raise RecurrenceError(
                f"cron expression {expression!r} has no upcoming occurrence"
            ) from exc
        aware = _localize(naive, tz)
        if aware is None:
            continue
        occurrences.append(aware.astimezone(UTC))
    return occurrences


def next_occurrence_after(expression: str, timezone: str, after: datetime) -> datetime:
    """Return the first UTC occurrence strictly after ``after``."""
    return next_occurrences(expression, timezone, after, 1)[0]
