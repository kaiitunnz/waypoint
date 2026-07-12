"""Deterministic instance-insight gate tests (PRD FR-3)."""

from datetime import UTC, datetime
from typing import Any

from waypoint.telemetry.api_models import Insight
from waypoint.telemetry.instance.insights import (
    VACUUM_MIN_FREE_BYTES,
    compute_instance_insights,
)
from waypoint.telemetry.instance.model import (
    CategoryFootprint,
    DatabaseReclaim,
    InstanceCounts,
    InstanceSnapshot,
    RedundantLogCandidate,
    StorageCategory,
)

_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _snapshot(**kw: Any) -> InstanceSnapshot:
    return InstanceSnapshot(observed_at=_NOW, tz="UTC", utc_offset_minutes=0, **kw)


def _types(insights: list[Insight]) -> set[str]:
    return {i.type for i in insights}


def test_no_insight_when_all_gates_unmet() -> None:
    snap = _snapshot(database=DatabaseReclaim(measured=True, page_count=100))
    assert compute_instance_insights(snap) == []


def test_orphan_insight_fires_on_one_orphan() -> None:
    snap = _snapshot(
        counts=InstanceCounts(orphan_dir_count=2),
        categories=[
            CategoryFootprint(
                category=StorageCategory.ORPHAN_SESSIONS, bytes=5 * 1024 * 1024
            )
        ],
    )
    insights = compute_instance_insights(snap)
    assert _types(insights) == {"orphan_data"}
    orphan = insights[0]
    assert orphan.severity == "warning"
    assert orphan.metrics["orphan_dir_count"] == 2
    assert orphan.observed_at == _NOW
    assert "prune-orphans" in (orphan.safety_note or "")


def test_orphan_insight_silent_at_zero() -> None:
    snap = _snapshot(counts=InstanceCounts(orphan_dir_count=0))
    assert "orphan_data" not in _types(compute_instance_insights(snap))


def test_redundant_log_fires_when_non_orphan_candidate_exists() -> None:
    snap = _snapshot(
        redundant_logs=RedundantLogCandidate(
            bytes=1000, count=3, running_excluded_count=1
        )
    )
    insights = compute_instance_insights(snap)
    assert "redundant_logs" in _types(insights)
    card = next(i for i in insights if i.type == "redundant_logs")
    assert card.metrics["candidate_count"] == 3
    assert card.metrics["running_excluded_count"] == 1


def test_redundant_log_defers_to_orphan_when_all_overlap() -> None:
    # Every candidate is inside an orphan directory: orphan card owns them, so
    # the redundant-log card must not offer a duplicate recommendation.
    snap = _snapshot(
        counts=InstanceCounts(orphan_dir_count=1),
        categories=[
            CategoryFootprint(category=StorageCategory.ORPHAN_SESSIONS, bytes=1000)
        ],
        redundant_logs=RedundantLogCandidate(
            bytes=1000, count=2, orphan_overlap_bytes=1000, orphan_overlap_count=2
        ),
    )
    insights = compute_instance_insights(snap)
    assert "redundant_logs" not in _types(insights)
    assert "orphan_data" in _types(insights)


def test_vacuum_fires_only_above_both_thresholds() -> None:
    # >=100 MiB free AND >=20% free pages.
    page_size = 4096
    page_count = 100_000  # ~390 MiB file
    freelist = 30_000  # 30% free, ~117 MiB
    snap = _snapshot(
        database=DatabaseReclaim(
            measured=True,
            page_size=page_size,
            page_count=page_count,
            freelist_count=freelist,
            free_bytes=page_size * freelist,
            free_percent=freelist / page_count,
        )
    )
    insights = compute_instance_insights(snap)
    assert "database_vacuum" in _types(insights)
    card = next(i for i in insights if i.type == "database_vacuum")
    assert card.metrics["freelist_count"] == freelist


def test_vacuum_silent_below_percent_gate() -> None:
    page_size = 4096
    # 100+ MiB free but only 10% free pages -> silent.
    page_count = 400_000
    freelist = 40_000  # 10%
    assert page_size * freelist >= VACUUM_MIN_FREE_BYTES
    snap = _snapshot(
        database=DatabaseReclaim(
            measured=True,
            page_size=page_size,
            page_count=page_count,
            freelist_count=freelist,
            free_bytes=page_size * freelist,
            free_percent=freelist / page_count,
        )
    )
    assert "database_vacuum" not in _types(compute_instance_insights(snap))


def test_vacuum_silent_below_bytes_gate() -> None:
    page_size = 4096
    # 50% free pages but only ~8 MiB free -> silent (bytes gate).
    page_count = 4_000
    freelist = 2_000
    snap = _snapshot(
        database=DatabaseReclaim(
            measured=True,
            page_size=page_size,
            page_count=page_count,
            freelist_count=freelist,
            free_bytes=page_size * freelist,
            free_percent=freelist / page_count,
        )
    )
    assert "database_vacuum" not in _types(compute_instance_insights(snap))


def test_vacuum_silent_when_unmeasured() -> None:
    snap = _snapshot(database=DatabaseReclaim(measured=False))
    assert "database_vacuum" not in _types(compute_instance_insights(snap))


def test_dismissal_suppresses_only_that_signature() -> None:
    snap = _snapshot(
        counts=InstanceCounts(orphan_dir_count=1),
        categories=[
            CategoryFootprint(category=StorageCategory.ORPHAN_SESSIONS, bytes=1024)
        ],
    )
    sig = compute_instance_insights(snap)[0].signature
    assert compute_instance_insights(snap, dismissed={sig}) == []


def test_orphan_signature_changes_on_material_size_increase() -> None:
    small = _snapshot(
        counts=InstanceCounts(orphan_dir_count=1),
        categories=[
            CategoryFootprint(
                category=StorageCategory.ORPHAN_SESSIONS, bytes=2 * 1024 * 1024
            )
        ],
    )
    big = _snapshot(
        counts=InstanceCounts(orphan_dir_count=1),
        categories=[
            CategoryFootprint(
                category=StorageCategory.ORPHAN_SESSIONS, bytes=64 * 1024 * 1024
            )
        ],
    )
    sig_small = compute_instance_insights(small)[0].signature
    # A dismissal of the small condition must not suppress the grown one.
    grown = compute_instance_insights(big, dismissed={sig_small})
    assert grown and grown[0].type == "orphan_data"
