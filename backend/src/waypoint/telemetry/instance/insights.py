"""Deterministic instance-optimization insights (PRD FR-3).

Conservative, evidence-linked, and silent unless a measured gate is met — the
product never invents a recommendation merely because storage is nonzero. Each
insight recommends reviewing the existing maintenance workflow and never
executes anything. Dismissal reuses the shared ``telemetry_insight_dismissal``
table under a fixed ``instance`` range key; signatures bucket their driving
evidence so a materially changed condition yields a new signature and is not
suppressed by a stale dismissal.
"""

from waypoint.telemetry.api_models import Insight, InsightClickThrough
from waypoint.telemetry.facts import TelemetryFilter, TelemetryRange
from waypoint.telemetry.instance.model import (
    DataQuality,
    InstanceSnapshot,
    StorageCategory,
)

# PRD FR-3: a vacuum insight requires BOTH >=100 MiB free AND >=20% free pages.
VACUUM_MIN_FREE_BYTES = 100 * 1024 * 1024
VACUUM_MIN_FREE_PERCENT = 0.20

INSTANCE_RANGE_KEY = "instance"
_SIGNATURE_PREFIX = "instance:"
_ENDPOINT = "/api/telemetry/instance"


def is_instance_signature(signature: str) -> bool:
    return signature.startswith(_SIGNATURE_PREFIX)


def _mib(num_bytes: int) -> float:
    return num_bytes / (1024 * 1024)


def _size_bucket(num_bytes: int) -> int:
    """Doubling-granularity bucket over MiB, so a material size change re-fires."""
    return int(_mib(num_bytes)).bit_length()


def _instance_range(snapshot: InstanceSnapshot) -> TelemetryRange:
    return TelemetryRange(
        start=snapshot.observed_at,
        end=snapshot.observed_at,
        tz=snapshot.tz,
        utc_offset_minutes=snapshot.utc_offset_minutes,
    )


def compute_instance_insights(
    snapshot: InstanceSnapshot, *, dismissed: set[str] | None = None
) -> list[Insight]:
    dismissed = dismissed or set()
    candidates = [
        insight
        for insight in (
            _orphan_data_insight(snapshot),
            _redundant_logs_insight(snapshot),
            _database_vacuum_insight(snapshot),
        )
        if insight is not None
    ]
    return [c for c in candidates if c.signature not in dismissed]


def _orphan_data_insight(snapshot: InstanceSnapshot) -> Insight | None:
    count = snapshot.counts.orphan_dir_count
    if count < 1:
        return None
    orphan_bytes = snapshot.category(StorageCategory.ORPHAN_SESSIONS).bytes
    signature = f"{_SIGNATURE_PREFIX}orphan_data:{count}:{_size_bucket(orphan_bytes)}"
    return Insight(
        signature=signature,
        type="orphan_data",
        statement=(
            f"{count} orphaned session director"
            f"{'y' if count == 1 else 'ies'} consume "
            f"{_mib(orphan_bytes):.1f} MiB with no matching stored session."
        ),
        metrics={
            "orphan_dir_count": count,
            "orphan_bytes": orphan_bytes,
        },
        range=_instance_range(snapshot),
        filters=TelemetryFilter(),
        click_through=InsightClickThrough(
            endpoint=_ENDPOINT, params={"focus": "orphans"}
        ),
        severity="warning",
        observed_at=snapshot.observed_at,
        safety_note=(
            "Review with `waypoint maintenance prune-orphans` (dry-run by "
            "default); deletion is never automatic and requires explicit "
            "confirmation."
        ),
    )


def _redundant_logs_insight(snapshot: InstanceSnapshot) -> Insight | None:
    redundant = snapshot.redundant_logs
    if redundant.count < 1:
        return None
    # If every candidate is inside an orphan directory the orphan-pruning
    # insight owns those bytes and takes precedence — do not offer a duplicate
    # cleanup recommendation (PRD FR-2). Fire only when there is non-orphan
    # redundant-log to act on, but still report the overlap.
    actionable_bytes = redundant.bytes - redundant.orphan_overlap_bytes
    actionable_count = redundant.count - redundant.orphan_overlap_count
    if actionable_count < 1:
        return None
    signature = (
        f"{_SIGNATURE_PREFIX}redundant_logs:"
        f"{actionable_count}:{_size_bucket(actionable_bytes)}"
    )
    overlap_clause = ""
    if redundant.orphan_overlap_count:
        overlap_clause = (
            f" ({redundant.orphan_overlap_count} more are inside orphaned "
            "directories, handled by the orphan-data card)"
        )
    return Insight(
        signature=signature,
        type="redundant_logs",
        statement=(
            f"{actionable_count} inactive events.jsonl log"
            f"{'' if actionable_count == 1 else 's'} duplicate SQLite events "
            f"and can be cleared to reclaim {_mib(actionable_bytes):.1f} MiB"
            f"{overlap_clause}. Running-session logs are excluded."
        ),
        metrics={
            "candidate_count": actionable_count,
            "candidate_bytes": actionable_bytes,
            "running_excluded_count": redundant.running_excluded_count,
            "orphan_overlap_count": redundant.orphan_overlap_count,
            "orphan_overlap_bytes": redundant.orphan_overlap_bytes,
        },
        range=_instance_range(snapshot),
        filters=TelemetryFilter(),
        click_through=InsightClickThrough(endpoint=_ENDPOINT, params={"focus": "logs"}),
        severity="info",
        observed_at=snapshot.observed_at,
        safety_note=(
            "Clear with `waypoint maintenance clear-structured-logs` "
            "(dry-run by default); it excludes logs of RUNNING sessions."
        ),
    )


def _database_vacuum_insight(snapshot: InstanceSnapshot) -> Insight | None:
    reclaim = snapshot.database
    if not reclaim.measured:
        return None
    if (
        reclaim.free_bytes < VACUUM_MIN_FREE_BYTES
        or reclaim.free_percent < VACUUM_MIN_FREE_PERCENT
    ):
        return None
    pct_bucket = int(reclaim.free_percent * 100) // 5
    signature = (
        f"{_SIGNATURE_PREFIX}database_vacuum:"
        f"{_size_bucket(reclaim.free_bytes)}:{pct_bucket}"
    )
    return Insight(
        signature=signature,
        type="database_vacuum",
        statement=(
            f"The database has {_mib(reclaim.free_bytes):.0f} MiB of free pages "
            f"({reclaim.free_percent * 100:.0f}% of the file). A VACUUM may "
            "reclaim space, but it is an operator decision, not a guaranteed "
            "filesystem saving."
        ),
        metrics={
            "page_size": reclaim.page_size,
            "page_count": reclaim.page_count,
            "freelist_count": reclaim.freelist_count,
            "free_bytes": reclaim.free_bytes,
            "free_percent": reclaim.free_percent,
        },
        range=_instance_range(snapshot),
        filters=TelemetryFilter(),
        click_through=InsightClickThrough(
            endpoint=_ENDPOINT, params={"focus": "database"}
        ),
        severity="info",
        observed_at=snapshot.observed_at,
        safety_note=(
            "Run `waypoint maintenance vacuum` deliberately; VACUUM rewrites "
            "the database and does not guarantee the freed pages return to the "
            "filesystem."
        ),
    )


def snapshot_is_actionable(snapshot: InstanceSnapshot) -> bool:
    """Whether the snapshot is complete enough to trust insight gates."""
    return snapshot.data_quality != DataQuality.UNAVAILABLE
