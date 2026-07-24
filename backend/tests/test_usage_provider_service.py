"""UsageProviderService + dashboard composition + telemetry ingest seam."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from waypoint.schemas import (
    ProviderRateLimitUsage,
    ProviderRefreshResult,
    ProviderUsageSnapshot,
    ProviderUsageStatus,
    SessionRateLimitUsage,
    SessionRecord,
    SessionStatus,
    UsageWindow,
)
from waypoint.storage import Storage
from waypoint.telemetry.aggregate import ALL_TIME_RANGE
from waypoint.telemetry.facts import TelemetryFactKind, TelemetryFilter
from waypoint.telemetry.ingest import TelemetryIngester
from waypoint.usage_dashboard import build_dashboard
from waypoint.usage_providers.service import UsageProviderService

pytestmark = pytest.mark.asyncio


class _FakeProvider:
    type = "lumid"

    def __init__(self, provider_id: str = "lumid", label: str = "Lumid") -> None:
        self.id = provider_id
        self.label = label
        self.refresh_interval_seconds = 300
        self.refresh_calls = 0
        self._snapshot = _snapshot(provider_id)

    async def refresh(self, *, force: bool) -> ProviderRefreshResult:
        self.refresh_calls += 1
        return ProviderRefreshResult(
            provider_id=self.id, ok_count=1, last_success_at=datetime.now(UTC)
        )

    def buckets(self) -> list[ProviderUsageSnapshot]:
        return [self._snapshot]

    def status(self) -> ProviderUsageStatus:
        return ProviderUsageStatus(
            provider_id=self.id,
            provider_type=self.type,
            provider_label=self.label,
            enabled=True,
            last_success_at=datetime.now(UTC),
        )

    async def aclose(self) -> None:
        return None


def _snapshot(
    provider_id: str = "lumid", email: str = "a@x.com"
) -> ProviderUsageSnapshot:
    now = datetime.now(UTC)
    return ProviderUsageSnapshot(
        provider_id=provider_id,
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
                ),
                UsageWindow(
                    id="lumid-seven-day",
                    label="7d",
                    used_percent=6.2,
                    used_tokens=1_240_000,
                    limit_tokens=20_000_000,
                ),
            ],
        ),
        observed_at=now,
        last_success_at=now,
    )


def _session_bucket_source() -> list[SessionRecord]:
    now = datetime.now(UTC)
    session = SessionRecord(
        id="sess-1",
        backend="claude_code",
        source="managed",
        title="sess-1",
        cwd="~/",
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/sess-1.raw",
        structured_log_path="/tmp/sess-1.json",
        rate_limit_usage=SessionRateLimitUsage(
            source="claude_code",
            updated_at=now,
            windows=[UsageWindow(id="5h", label="5h", used_percent=10.0)],
        ),
        verified_account_key="claude_code:org-1",
        verified_account_label="Org One",
    )
    return [session]


class _StubRegistry:
    def has_backend(self, backend_id: str) -> bool:
        return True

    def get(self, backend_id: str) -> object:
        return object()


async def test_dashboard_merges_session_and_provider_buckets() -> None:
    service = UsageProviderService([_FakeProvider()])
    dashboard = build_dashboard(_session_bucket_source(), _StubRegistry(), service)
    origins = [b.origin for b in dashboard.buckets]
    assert "session" in origins
    assert "provider" in origins
    # Provider buckets sort after session buckets.
    assert origins.index("session") < origins.index("provider")
    provider_bucket = next(b for b in dashboard.buckets if b.origin == "provider")
    assert provider_bucket.session_ids == []
    assert provider_bucket.provider_label == "Lumid"
    assert provider_bucket.account_label == "a@x.com"
    assert len(dashboard.providers) == 1
    assert dashboard.providers[0].provider_id == "lumid"


async def test_dashboard_without_provider_service_is_unchanged() -> None:
    dashboard = build_dashboard(_session_bucket_source(), _StubRegistry())
    assert all(b.origin == "session" for b in dashboard.buckets)
    assert dashboard.providers == []


async def test_provider_status_visible_without_bucket() -> None:
    class _NoBucketProvider(_FakeProvider):
        def buckets(self) -> list[ProviderUsageSnapshot]:
            return []

    service = UsageProviderService([_NoBucketProvider()])
    dashboard = build_dashboard([], _StubRegistry(), service)
    assert dashboard.buckets == []
    assert len(dashboard.providers) == 1  # health without an account


async def test_refresh_all_coalesces_and_isolates() -> None:
    good = _FakeProvider("good")

    class _Boom(_FakeProvider):
        async def refresh(self, *, force: bool) -> ProviderRefreshResult:
            raise RuntimeError("boom")

    service = UsageProviderService([good, _Boom("bad")])
    results = await service.refresh_all(force=True)
    # The failing provider is isolated; the good one still returns a result.
    assert [r.provider_id for r in results] == ["good"]


async def test_telemetry_hook_called_on_success(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "db.sqlite")
    ingester = TelemetryIngester(storage, _StubRegistry())
    service = UsageProviderService(
        [_FakeProvider()], telemetry_hook=ingester.ingest_provider_usage
    )
    await service.refresh_all(force=True)
    # Drain synchronously by invoking the private batch path is overkill; the
    # hook enqueues facts — flush by running the ingester's drain once.
    await ingester.start()
    await ingester.stop()
    rows = storage.telemetry.query_facts(
        TelemetryFactKind.LIMIT_SNAPSHOT, ALL_TIME_RANGE, TelemetryFilter()
    )
    assert rows
    assert all(row["session_attributable"] == 0 for row in rows)
    assert all(row["session_id"].startswith("provider:") for row in rows)
