"""NL contract tests for instance health & capacity (PRD FR-5).

Proves the server-rendered claim-template model: only whitelisted aggregates
reach the summarizer, model-chosen numbers/routes/free-form prose never become a
grounded claim, invalid template/evidence ids are rejected, recommendations gate
on the deterministic insight, and the usage-prose channel never carries instance
fields.
"""

from datetime import UTC, datetime
from typing import Any

from waypoint.telemetry.instance.insights import compute_instance_insights
from waypoint.telemetry.instance.model import (
    CategoryFootprint,
    DatabaseReclaim,
    DataQuality,
    InstanceCounts,
    InstanceSnapshot,
    RedundantLogCandidate,
    StorageCategory,
)
from waypoint.telemetry.instance.nl import (
    build_instance_nl_aggregate,
    render_instance_bullets,
)
from waypoint.telemetry.summarizer import assert_no_path_like_strings

_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


def _snapshot(**kw: Any) -> InstanceSnapshot:
    return InstanceSnapshot(observed_at=_NOW, tz="UTC", utc_offset_minutes=0, **kw)


def _aggregate(snap: InstanceSnapshot):
    insights = compute_instance_insights(snap)
    return build_instance_nl_aggregate(snap, insights)


def test_valid_selection_renders_server_text_with_aggregate_numbers() -> None:
    snap = _snapshot(
        total_bytes=1024 * 1024 * 3,
        categories=[
            CategoryFootprint(
                category=StorageCategory.LIVE_SESSIONS, bytes=1024 * 1024 * 3
            )
        ],
    )
    agg = _aggregate(snap)
    bullets = render_instance_bullets(
        [{"template_id": "total_footprint", "evidence_ids": ["total_bytes"]}], agg
    )
    assert len(bullets) == 1
    assert "3.0 MiB" in bullets[0].text
    assert bullets[0].evidence[0].metric == "total_bytes"


def test_unknown_template_and_evidence_are_dropped() -> None:
    agg = _aggregate(_snapshot(total_bytes=1024))
    # unknown template id -> dropped entirely
    assert render_instance_bullets([{"template_id": "make_stuff_up"}], agg) == []
    # evidence id outside the template allowlist -> not attached
    bullets = render_instance_bullets(
        [{"template_id": "total_footprint", "evidence_ids": ["orphan_bytes"]}], agg
    )
    assert bullets and bullets[0].evidence == []


def test_model_supplied_numbers_and_prose_are_ignored() -> None:
    agg = _aggregate(_snapshot(total_bytes=1024))  # 1.0 KiB
    bullets = render_instance_bullets(
        [
            {
                "template_id": "total_footprint",
                "evidence_ids": ["total_bytes"],
                "text": "TOTAL IS 999 TB!!!",  # ignored
                "value": "999 TB",  # ignored
            }
        ],
        agg,
    )
    assert bullets and "999" not in bullets[0].text
    assert "1.0 KiB" in bullets[0].text


def test_recommendation_gated_on_fired_insight() -> None:
    # No orphan insight fired -> orphan_review template is refused.
    agg = _aggregate(_snapshot(counts=InstanceCounts(orphan_dir_count=0)))
    assert render_instance_bullets([{"template_id": "orphan_review"}], agg) == []

    # Orphan insight fires -> orphan_review permitted.
    snap = _snapshot(
        counts=InstanceCounts(orphan_dir_count=2),
        categories=[
            CategoryFootprint(
                category=StorageCategory.ORPHAN_SESSIONS, bytes=1024 * 1024
            )
        ],
    )
    agg2 = _aggregate(snap)
    bullets = render_instance_bullets(
        [{"template_id": "orphan_review", "evidence_ids": ["orphan_dir_count"]}], agg2
    )
    assert bullets and "2 orphaned" in bullets[0].text


def test_vacuum_claim_requires_vacuum_insight() -> None:
    # Free pages present but below the gate -> no vacuum insight -> refused.
    snap = _snapshot(
        database=DatabaseReclaim(
            measured=True,
            page_size=4096,
            page_count=1000,
            freelist_count=100,
            free_bytes=4096 * 100,
            free_percent=0.1,
        )
    )
    agg = _aggregate(snap)
    assert render_instance_bullets([{"template_id": "vacuum_candidate"}], agg) == []


def test_unavailable_snapshot_omits_all_claims() -> None:
    snap = _snapshot(data_quality=DataQuality.UNAVAILABLE, total_bytes=0)
    agg = _aggregate(snap)
    assert (
        render_instance_bullets(
            [{"template_id": "total_footprint", "evidence_ids": ["total_bytes"]}], agg
        )
        == []
    )


def test_duplicate_template_selection_deduped() -> None:
    agg = _aggregate(_snapshot(total_bytes=2048))
    bullets = render_instance_bullets(
        [
            {"template_id": "total_footprint"},
            {"template_id": "total_footprint"},
        ],
        agg,
    )
    assert len(bullets) == 1


def test_aggregate_is_path_free() -> None:
    snap = _snapshot(
        total_bytes=5000,
        redundant_logs=RedundantLogCandidate(bytes=100, count=1),
        categories=[
            CategoryFootprint(category=StorageCategory.LIVE_SESSIONS, bytes=5000)
        ],
    )
    agg = _aggregate(snap)
    # The serialized aggregate must contain no filesystem path (raises if it does).
    assert_no_path_like_strings(agg.model_dump(mode="json"))


def test_usage_prose_payload_has_no_instance_fields(tmp_path) -> None:
    from waypoint.settings import Settings
    from waypoint.storage import Storage
    from waypoint.telemetry.facts import TelemetryFilter
    from waypoint.telemetry.query import host_tz_name, resolve_preset_range
    from waypoint.telemetry.summarizer import build_nl_request

    settings = Settings(data_dir=tmp_path / "d", telemetry_enabled=True)
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    try:
        rng = resolve_preset_range("7d", host_tz_name())
        request = build_nl_request(storage, settings, rng, TelemetryFilter())
        assert set(request.aggregates) == {"overview", "tokens", "activity", "health"}
        serialized = request.model_dump_json()
        # The usage-prose payload never carries instance category identifiers.
        assert "orphan_sessions" not in serialized
        assert "sqlite_companions" not in serialized
    finally:
        storage.close()
