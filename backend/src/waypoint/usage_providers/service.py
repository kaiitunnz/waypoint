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
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from waypoint.schemas import (
    ProviderBucketHealth,
    ProviderRefreshResult,
    ProviderUsageDashboardBucket,
    ProviderUsageSnapshot,
    ProviderUsageStatus,
    UsageProviderAccountOption,
    UsageProviderOption,
)
from waypoint.usage_providers.contracts import UsageProvider

log = logging.getLogger(__name__)

TelemetryHook = Callable[[ProviderUsageSnapshot], None]

# Invoked after every attempted provider refresh (success OR failure) with the
# provider's current buckets and status, so the runtime can re-project selected
# sessions and mark unavailable ones stale. Distinct from the telemetry hook,
# which only fires on successful refreshes.
PostRefreshObserver = Callable[
    [str, list[ProviderUsageSnapshot], ProviderUsageStatus], Awaitable[None]
]


class UsageProviderService:
    def __init__(
        self,
        providers: list[UsageProvider],
        telemetry_hook: TelemetryHook | None = None,
        observer: PostRefreshObserver | None = None,
    ) -> None:
        self._providers = providers
        self._telemetry_hook = telemetry_hook
        self._observer = observer
        self._by_id = {p.id: p for p in providers}
        self._loops: list[asyncio.Task[None]] = []
        self._inflight: dict[str, asyncio.Task[ProviderRefreshResult]] = {}
        self._wake: dict[str, asyncio.Event] = {
            p.id: asyncio.Event() for p in providers
        }
        self._stopping = False

    async def start(self) -> None:
        for provider in self._providers:
            provider.load_durable()
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
        interval = provider.refresh_interval_seconds
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

    async def refresh_one(
        self, provider_id: str, *, force: bool = True
    ) -> ProviderRefreshResult | None:
        provider = self._by_id.get(provider_id)
        if provider is None:
            return None
        return await self._refresh_provider(provider, force=force)

    async def _do_refresh(
        self, provider: UsageProvider, force: bool
    ) -> ProviderRefreshResult:
        try:
            result = await provider.refresh(force=force)
        finally:
            # The observer runs on every attempt — success, soft error, or a
            # hard exception — so a selected session whose provider went
            # unavailable can be marked stale (FR-8).
            if self._observer is not None:
                with contextlib.suppress(Exception):
                    await self._observer(
                        provider.id, provider.buckets(), provider.status()
                    )
        if self._telemetry_hook is not None and result.ok_count:
            for snapshot in provider.buckets():
                with contextlib.suppress(Exception):
                    self._telemetry_hook(snapshot)
        return result

    def statuses(self) -> list[ProviderUsageStatus]:
        return [p.status() for p in self._providers]

    def options(self) -> list[UsageProviderOption]:
        """Enabled provider/account choices for launch + settings selectors.

        Computed purely from current in-memory buckets + status — no upstream
        request. A provider with no published accounts is still listed (its
        status marks it unavailable) so the UI can show it as unselectable.
        """
        options: list[UsageProviderOption] = []
        for provider in self._providers:
            status = provider.status()
            accounts = [
                UsageProviderAccountOption(
                    account_key=snapshot.account_key,
                    account_label=snapshot.account_label,
                )
                for snapshot in provider.buckets()
            ]
            options.append(
                UsageProviderOption(
                    id=provider.id,
                    label=provider.label,
                    type=provider.type,
                    accounts=accounts,
                    status=status,
                )
            )
        return options

    def snapshot(
        self, provider_id: str, account_key: str
    ) -> ProviderUsageSnapshot | None:
        provider = self._by_id.get(provider_id)
        if provider is None:
            return None
        for snapshot in provider.buckets():
            if snapshot.account_key == account_key:
                return snapshot
        return None

    def provider_status(self, provider_id: str) -> ProviderUsageStatus | None:
        provider = self._by_id.get(provider_id)
        return provider.status() if provider is not None else None

    def provider_buckets(self, provider_id: str) -> list[ProviderUsageSnapshot]:
        provider = self._by_id.get(provider_id)
        return provider.buckets() if provider is not None else []

    def dashboard_buckets(self) -> list[ProviderUsageDashboardBucket]:
        buckets: list[ProviderUsageDashboardBucket] = []
        for provider in self._providers:
            interval = provider.refresh_interval_seconds
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
