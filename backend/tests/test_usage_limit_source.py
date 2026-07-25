"""Runtime behavior for per-session usage-provider selection (ticket 1273).

Covers the source switch, the central origin-ownership guard, provider
projection, no-fallback on unavailability, and the non-restart settings path.
"""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    ProviderRateLimitUsage,
    ProviderRefreshResult,
    ProviderUsageSnapshot,
    ProviderUsageStatus,
    SessionRateLimitUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
    UsageLimitSourceUpdateRequest,
    UsageWindow,
)
from waypoint.settings import Settings
from waypoint.storage import Storage
from waypoint.usage_providers.service import UsageProviderService

pytestmark = pytest.mark.asyncio

_ACCOUNT_KEY = "hmac:v1:a@x.com"


class _FakeProvider:
    type = "lumid"

    def __init__(self, provider_id: str = "lumid", *, empty: bool = False) -> None:
        self.id = provider_id
        self.label = "Lumid"
        self.refresh_interval_seconds = 300
        self.refresh_calls = 0
        self._empty = empty

    def load_durable(self) -> None:
        return None

    async def refresh(self, *, force: bool) -> ProviderRefreshResult:
        self.refresh_calls += 1
        return ProviderRefreshResult(
            provider_id=self.id, ok_count=1, last_success_at=datetime.now(UTC)
        )

    def buckets(self) -> list[ProviderUsageSnapshot]:
        if self._empty:
            return []
        now = datetime.now(UTC)
        return [
            ProviderUsageSnapshot(
                provider_id=self.id,
                provider_type=self.type,
                account_key=_ACCOUNT_KEY,
                account_label="a@x.com",
                snapshot=ProviderRateLimitUsage(
                    source_id="lumid",
                    updated_at=now,
                    windows=[UsageWindow(id="5h", label="5h", used_percent=61.9)],
                ),
                observed_at=now,
                last_success_at=now,
            )
        ]

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


def _make_runtime(
    tmp_path, provider: _FakeProvider | None
) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    runtime = SessionRuntime(settings, storage)
    if provider is not None:
        runtime.usage_providers = UsageProviderService(
            [provider], observer=runtime._project_provider_sessions
        )
    return runtime, storage


def _session(storage: Storage, session_id: str = "sess", **overrides) -> SessionRecord:
    now = datetime.now(UTC)
    record = SessionRecord(
        id=session_id,
        backend="codex",
        source=SessionSource.MANAGED,
        title="Session",
        cwd="/tmp",
        status=SessionStatus.RUNNING,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=f"/tmp/{session_id}.raw",
        structured_log_path=f"/tmp/{session_id}.json",
        **overrides,
    )
    storage.create_session(record)
    return record


async def test_set_source_to_provider_projects_cached_snapshot(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    session = await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    assert session.usage_limit_source == "usage_provider"
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.origin == "usage_provider"
    assert session.rate_limit_usage.source == "lumid"
    assert "Lumid" in (session.rate_limit_usage.source_label or "")
    # No agent process refresh happened for a provider selection.
    assert provider.refresh_calls == 0


async def test_set_source_rejects_malformed_selection(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    with pytest.raises(HTTPException) as exc:
        await runtime.set_usage_limit_source(
            "sess",
            UsageLimitSourceUpdateRequest(
                usage_limit_source="usage_provider", usage_provider_id="lumid"
            ),
        )
    assert exc.value.status_code == 400


async def test_set_source_rejects_unavailable_account(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    with pytest.raises(HTTPException) as exc:
        await runtime.set_usage_limit_source(
            "sess",
            UsageLimitSourceUpdateRequest(
                usage_limit_source="usage_provider",
                usage_provider_id="lumid",
                usage_provider_account_key="hmac:v1:missing",
            ),
        )
    assert exc.value.status_code == 409


async def test_switch_back_to_plugin_clears_provider_projection(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    session = await runtime.set_usage_limit_source(
        "sess", UsageLimitSourceUpdateRequest(usage_limit_source="plugin")
    )
    assert session.usage_limit_source == "plugin"
    assert session.usage_provider_id is None
    # The provider projection was cleared; a plugin session with no resolver
    # produces no snapshot rather than showing the old provider readout.
    assert (
        session.rate_limit_usage is None or session.rate_limit_usage.origin == "plugin"
    )


async def test_origin_guard_drops_plugin_write_under_provider_selection(
    tmp_path,
) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    # An adapter publishes a native plugin-origin snapshot (dict, model_dump'd).
    plugin_snapshot = SessionRateLimitUsage(
        source="codex",
        updated_at=datetime.now(UTC),
        windows=[UsageWindow(id="5h", label="5h", used_percent=99.0)],
    ).model_dump(mode="json")
    session = await runtime.update_session_fields(
        "sess", rate_limit_usage=plugin_snapshot
    )
    # The guard drops the plugin write; the provider projection survives.
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.origin == "usage_provider"


async def test_provider_poll_marks_unavailable_when_account_gone(tmp_path) -> None:
    provider = _FakeProvider()
    runtime, storage = _make_runtime(tmp_path, provider)
    _session(storage)
    await runtime.set_usage_limit_source(
        "sess",
        UsageLimitSourceUpdateRequest(
            usage_limit_source="usage_provider",
            usage_provider_id="lumid",
            usage_provider_account_key=_ACCOUNT_KEY,
        ),
    )
    # The account vanishes from the provider's buckets; a refresh re-projects.
    provider._empty = True
    await runtime.usage_providers.refresh_one("lumid", force=True)  # type: ignore[union-attr]
    session = runtime.get_session("sess")
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.origin == "usage_provider"
    assert session.rate_limit_usage.unavailable is True
    # Never substituted plugin data.
    assert session.rate_limit_usage.source == "lumid"


async def test_reconcile_marks_unavailable_when_provider_removed(tmp_path) -> None:
    # A session persisted as provider-selected, but no provider is enabled now.
    runtime, storage = _make_runtime(tmp_path, None)
    now = datetime.now(UTC)
    _session(
        storage,
        usage_limit_source="usage_provider",
        usage_provider_id="lumid",
        usage_provider_account_key=_ACCOUNT_KEY,
        rate_limit_usage=SessionRateLimitUsage(
            origin="usage_provider",
            source="lumid",
            source_label="Lumid — a@x.com",
            updated_at=now,
            windows=[UsageWindow(id="5h", label="5h", used_percent=42.0)],
        ),
    )
    runtime._reconcile_provider_selections()
    session = runtime.get_session("sess")
    assert session.rate_limit_usage is not None
    assert session.rate_limit_usage.unavailable is True
    assert session.rate_limit_usage.stale is True
    # Retains the last-good windows, does not fall back to plugin.
    assert session.rate_limit_usage.windows[0].used_percent == 42.0
