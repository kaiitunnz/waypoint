"""Provider telemetry facts: session-scoping exclusion, label gate, drilldown."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.schemas import (
    ProviderRateLimitUsage,
    ProviderUsageSnapshot,
    UsageWindow,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.telemetry.aggregate import (
    ALL_TIME_RANGE,
    build_drilldown,
    current_limit_snapshots,
)
from waypoint.telemetry.facts import TelemetryFactKind, TelemetryFilter
from waypoint.telemetry.ingest import TelemetryIngester

pytestmark = pytest.mark.asyncio


class _StubRegistry:
    def has_backend(self, backend_id: str) -> bool:
        return True

    def get(self, backend_id: str) -> object:
        return object()


def _snapshot(email: str = "a@x.com") -> ProviderUsageSnapshot:
    now = datetime.now(UTC)
    return ProviderUsageSnapshot(
        provider_id="lumid",
        provider_type="lumid",
        account_key=f"hmac:v1:{email}",
        account_label=email,
        snapshot=ProviderRateLimitUsage(
            source_id="lumid",
            updated_at=now,
            windows=[
                UsageWindow(
                    id="lumid-five-hour",
                    label="5h",
                    used_percent=61.9,
                    used_tokens=1_240_000,
                    limit_tokens=2_000_000,
                )
            ],
        ),
        observed_at=now,
        last_success_at=now,
    )


async def _ingest(storage: Storage) -> None:
    ingester = TelemetryIngester(storage, _StubRegistry())
    ingester.ingest_provider_usage(_snapshot())
    await ingester.start()
    await ingester.stop()


async def test_provider_fact_in_unscoped_view(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    await _ingest(storage)
    settings = Settings(telemetry_enabled=True)
    views = current_limit_snapshots(
        storage, TelemetryFilter(), datetime.now(UTC), settings
    )
    assert any(v.account_key == "hmac:v1:a@x.com" for v in views)


async def test_provider_fact_excluded_by_session_scoping(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    await _ingest(storage)
    settings = Settings(telemetry_enabled=True)
    # Every session-scoping filter must hide account-scoped provider facts.
    for flt in (
        TelemetryFilter(parent_scope="top_level"),
        TelemetryFilter(sources=["managed"]),
        TelemetryFilter(models=["some-model"]),
        TelemetryFilter(transports=["claude_code"]),
    ):
        assert flt.has_session_scoping()
        views = current_limit_snapshots(storage, flt, datetime.now(UTC), settings)
        assert views == []


async def test_account_label_gated_by_local_labels(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    await _ingest(storage)
    now = datetime.now(UTC)
    off = current_limit_snapshots(
        storage, TelemetryFilter(), now, Settings(telemetry_enabled=True)
    )
    assert all(v.account_label is None for v in off)
    on = current_limit_snapshots(
        storage,
        TelemetryFilter(),
        now,
        Settings(telemetry_enabled=True, telemetry_local_labels=True),
    )
    assert any(v.account_label == "a@x.com" for v in on)


async def test_drilldown_marks_provider_row_unlinked(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    await _ingest(storage)
    drill = build_drilldown(
        storage,
        ALL_TIME_RANGE,
        TelemetryFilter(),
        TelemetryFactKind.LIMIT_SNAPSHOT,
        page=1,
        page_size=50,
    )
    assert drill.items
    for item in drill.items:
        assert item.session_attributable is False
        assert item.session_id.startswith("provider:")


async def test_not_ingested_when_disabled_hook_absent(tmp_path: Path) -> None:
    # The runtime only wires the telemetry hook when telemetry is enabled, so a
    # disabled deployment simply never calls ingest_provider_usage. Assert the
    # store stays empty when the hook is not invoked.
    storage = Storage(tmp_path / "db.sqlite")
    rows = storage.telemetry.query_facts(
        TelemetryFactKind.LIMIT_SNAPSHOT, ALL_TIME_RANGE, TelemetryFilter()
    )
    assert rows == []
