"""Runtime-owned usage-provider service.

Owns provider lifecycle: loads durable snapshots at start, runs an initial
refresh and one cancellable poll loop per provider, serializes (coalesces)
scheduled and manual refreshes per provider, and projects the current state into
dashboard buckets + provider statuses. On each successful refresh it hands every
current snapshot to an optional telemetry hook. Mirrors
:class:`~waypoint.notifications.service.NotificationService`.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from waypoint.schemas import (
    ProviderBucketHealth,
    ProviderRefreshResult,
    ProviderUsageDashboardBucket,
    ProviderUsageSnapshot,
    ProviderUsageStatus,
)
from waypoint.usage_providers.contracts import UsageProvider

log = logging.getLogger(__name__)

TelemetryHook = Callable[[ProviderUsageSnapshot], None]


class UsageProviderService:
    def __init__(
        self,
        providers: list[UsageProvider],
        telemetry_hook: TelemetryHook | None = None,
    ) -> None:
        self._providers = providers
        self._by_id = {p.id: p for p in providers}
        self._telemetry_hook = telemetry_hook
        self._loops: list[asyncio.Task[None]] = []
        self._inflight: dict[str, asyncio.Task[ProviderRefreshResult]] = {}
        self._wake: dict[str, asyncio.Event] = {
            p.id: asyncio.Event() for p in providers
        }
        self._stopping = False

    async def start(self) -> None:
        for provider in self._providers:
            load = getattr(provider, "load_durable", None)
            if callable(load):
                load()
        # Initial refresh + poll loops run off the boot path so a slow provider
        # never delays startup.
        for provider in self._providers:
            self._loops.append(
                asyncio.create_task(
                    self._run(provider), name=f"usage-provider-{provider.id}"
                )
            )

    async def stop(self) -> None:
        self._stopping = True
        for event in self._wake.values():
            event.set()
        for loop_task in self._loops:
            loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task
        self._loops.clear()
        for inflight_task in list(self._inflight.values()):
            inflight_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await inflight_task
        self._inflight.clear()
        for provider in self._providers:
            with contextlib.suppress(Exception):
                await provider.aclose()

    async def _run(self, provider: UsageProvider) -> None:
        interval = getattr(provider, "refresh_interval_seconds", 300)
        # Initial refresh immediately, then poll at the interval.
        first = True
        while not self._stopping:
            if not first:
                self._wake[provider.id].clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wake[provider.id].wait(), timeout=interval
                    )
                if self._stopping:
                    break
            first = False
            try:
                await self._refresh_provider(provider, force=False)
            except Exception:  # noqa: BLE001 - a transient error must not kill the loop
                log.exception(
                    "usage provider refresh failed", extra={"provider_id": provider.id}
                )

    async def refresh_all(self, *, force: bool) -> list[ProviderRefreshResult]:
        results = await asyncio.gather(
            *(self._refresh_provider(p, force=force) for p in self._providers),
            return_exceptions=True,
        )
        out: list[ProviderRefreshResult] = []
        for provider, result in zip(self._providers, results, strict=True):
            if isinstance(result, ProviderRefreshResult):
                out.append(result)
            else:
                log.warning(
                    "usage provider refresh raised",
                    extra={"provider_id": provider.id, "error": type(result).__name__},
                )
        return out

    async def _refresh_provider(
        self, provider: UsageProvider, *, force: bool
    ) -> ProviderRefreshResult:
        # Coalesce concurrent callers (scheduled loop + manual API) onto one
        # in-flight refresh rather than issuing a duplicate request.
        existing = self._inflight.get(provider.id)
        if existing is not None and not existing.done():
            return await existing
        task = asyncio.create_task(self._do_refresh(provider, force))
        self._inflight[provider.id] = task
        try:
            return await task
        finally:
            if self._inflight.get(provider.id) is task:
                del self._inflight[provider.id]

    async def _do_refresh(
        self, provider: UsageProvider, force: bool
    ) -> ProviderRefreshResult:
        result = await provider.refresh(force=force)
        if self._telemetry_hook is not None and result.ok_count:
            for snapshot in provider.buckets():
                with contextlib.suppress(Exception):
                    self._telemetry_hook(snapshot)
        return result

    def statuses(self) -> list[ProviderUsageStatus]:
        return [p.status() for p in self._providers]

    def dashboard_buckets(self) -> list[ProviderUsageDashboardBucket]:
        buckets: list[ProviderUsageDashboardBucket] = []
        for provider in self._providers:
            interval = getattr(provider, "refresh_interval_seconds", 300)
            threshold = max(2 * interval, 600)
            for snapshot in provider.buckets():
                age = (datetime.now(UTC) - snapshot.last_success_at).total_seconds()
                buckets.append(
                    ProviderUsageDashboardBucket(
                        provider_id=snapshot.provider_id,
                        provider_type=snapshot.provider_type,
                        provider_label=provider.label,
                        account_key=snapshot.account_key,
                        account_label=snapshot.account_label,
                        snapshot=snapshot.snapshot,
                        metadata=snapshot.metadata,
                        health=ProviderBucketHealth(
                            last_success_at=snapshot.last_success_at,
                            stale=age > threshold,
                        ),
                    )
                )
        return buckets
