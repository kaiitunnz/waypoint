"""Backend-neutral usage-provider contract.

A usage provider fetches account rate-limit usage independently of any coding
session. The runtime, dashboard, and telemetry paths see only this Protocol and
the provider-neutral models in :mod:`waypoint.schemas`; nothing here or in a
generic consumer references a specific provider (e.g. Lumid). A new provider
type implements this Protocol in its own module and registers a config +
factory — no generic-path change.
"""

from typing import Protocol, runtime_checkable

from waypoint.schemas import (
    ProviderRefreshResult,
    ProviderUsageSnapshot,
    ProviderUsageStatus,
)


@runtime_checkable
class UsageProvider(Protocol):
    id: str
    label: str
    type: str

    async def refresh(self, *, force: bool) -> ProviderRefreshResult:
        """Fetch fresh usage for every configured credential, persist the latest
        successful snapshot, and return a safe aggregate result."""
        ...

    def buckets(self) -> list[ProviderUsageSnapshot]:
        """The current published account snapshots (one per resolved account)."""
        ...

    def status(self) -> ProviderUsageStatus:
        """Provider-level health, independent of whether any account resolved."""
        ...

    async def aclose(self) -> None:
        """Release any held resources (HTTP client)."""
        ...
