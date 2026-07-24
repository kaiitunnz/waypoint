"""Config → provider-instance construction.

Maps each enabled ``usage_providers`` config entry to its :class:`UsageProvider`
implementation by config type. Adding a provider registers one factory here; no
generic runtime/dashboard/telemetry change.
"""

from waypoint.settings import LumidUsageProviderConfig, Settings
from waypoint.usage_providers.contracts import UsageProvider
from waypoint.usage_providers.lumid import LumidUsageProvider
from waypoint.usage_providers.store import UsageProviderStore


def build_providers(
    settings: Settings, store: UsageProviderStore, http_timeout: float
) -> list[UsageProvider]:
    providers: list[UsageProvider] = []
    for config in settings.enabled_usage_providers():
        if isinstance(config, LumidUsageProviderConfig):
            providers.append(
                LumidUsageProvider(
                    provider_id=config.id,
                    label=config.label,
                    token_env=config.token_env,
                    store=store,
                    http_timeout=http_timeout,
                    refresh_interval_seconds=config.refresh_interval_seconds,
                )
            )
    return providers
