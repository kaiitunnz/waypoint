"""Aggregate session rate-limit snapshots into per-account dashboard buckets.

Rate limits live at the provider account level (a Claude org's 5h/weekly
window is shared across every session signed into that org), so the
dashboard groups by ``(backend, account_key)`` rather than per session.
The account key is derived by the agent plugin from the ``notes`` list on
each snapshot — that per-agent knowledge (Claude's org/tier, Codex's
email/plan) lives on the plugin's ``rate_limit_account`` method, not here.
"""

from typing import Protocol

from waypoint.schemas import (
    ProviderUsageDashboardBucket,
    ProviderUsageStatus,
    SessionRateLimitUsage,
    SessionRecord,
    SessionUsageDashboardBucket,
    UsageDashboardBucket,
    UsageDashboardResponse,
)


class _PluginRegistry(Protocol):
    """Minimal registry surface the dashboard needs.

    Declared structurally so the dashboard doesn't import the concrete
    ``BackendRegistry`` (and the backends package) just to look plugins up.
    """

    def has_backend(self, backend_id: str) -> bool: ...

    def get(self, backend_id: str) -> object: ...


class _ProviderService(Protocol):
    """Minimal provider-service surface the dashboard consumes.

    Declared structurally so the dashboard stays provider-neutral and never
    imports the usage_providers package or any concrete provider.
    """

    def dashboard_buckets(self) -> list[ProviderUsageDashboardBucket]: ...

    def statuses(self) -> list[ProviderUsageStatus]: ...


def resolve_account(
    snapshot: SessionRateLimitUsage,
    *,
    registry: _PluginRegistry | None,
    verified_account_key: str | None = None,
    verified_account_label: str | None = None,
) -> tuple[str, str] | None:
    """Return the raw ``(account_key, account_label)`` for a snapshot, or ``None``.

    Resolution order: the session's persisted ``verified_account_key``/``label``
    (last probed at launch/switch/reattach) when present — it's already in the
    same ``{backend}:{identity}`` shape ``rate_limit_account`` produces, since
    both derive from the same probe — else the snapshot's agent plugin's own
    ``rate_limit_account``. ``None`` means neither source could attribute the
    snapshot to an account; callers decide what that means for them (the
    dashboard buckets it per-session below, telemetry ingest skips it instead
    of minting a per-session pseudo-account).
    """
    if verified_account_key is not None:
        return verified_account_key, verified_account_label or verified_account_key
    if registry is None:
        return None
    plugin = (
        registry.get(snapshot.source) if registry.has_backend(snapshot.source) else None
    )
    if plugin is not None:
        resolver = getattr(plugin, "rate_limit_account", None)
        if resolver is not None:
            account = resolver(snapshot)
            if account is not None:
                return account
    return None


def account_bucket_for(
    snapshot: SessionRateLimitUsage,
    *,
    session_id: str,
    registry: _PluginRegistry,
    verified_account_key: str | None = None,
    verified_account_label: str | None = None,
) -> tuple[str, str]:
    """Return ``(account_key, account_label)`` for a snapshot, raw identity.

    Falls back to a session-scoped key labelled with the plugin's human name
    when ``resolve_account`` declines (no account info) or the plugin is
    unknown, so probes without org/email metadata still surface as their own
    bucket instead of collapsing together.
    """
    resolved = resolve_account(
        snapshot,
        registry=registry,
        verified_account_key=verified_account_key,
        verified_account_label=verified_account_label,
    )
    if resolved is not None:
        return resolved
    return (
        f"{snapshot.source}:session:{session_id}",
        _humanise_backend(snapshot.source, registry),
    )


def _humanise_backend(backend: str, registry: _PluginRegistry) -> str:
    if registry.has_backend(backend):
        plugin = registry.get(backend)
        label = getattr(plugin, "label", None)
        if isinstance(label, str):
            return label
    return backend


def build_dashboard(
    sessions: list[SessionRecord],
    registry: _PluginRegistry,
    provider_service: _ProviderService | None = None,
) -> UsageDashboardResponse:
    # ``session_ids[0]`` is the session that produced the freshest snapshot
    # in the bucket — refresh paths target it so a stale/torn-down session
    # cannot silently no-op the probe when a live one is available.
    buckets: dict[str, SessionUsageDashboardBucket] = {}
    for session in sessions:
        snapshot = session.rate_limit_usage
        if snapshot is None:
            continue
        key, label = account_bucket_for(
            snapshot,
            session_id=session.id,
            registry=registry,
            verified_account_key=session.verified_account_key,
            verified_account_label=session.verified_account_label,
        )
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = SessionUsageDashboardBucket(
                backend=snapshot.source,
                account_key=key,
                account_label=label,
                snapshot=snapshot,
                session_ids=[session.id],
            )
            continue
        if snapshot.updated_at > existing.snapshot.updated_at:
            existing.snapshot = snapshot
            existing.account_label = label
            existing.session_ids.insert(0, session.id)
        else:
            existing.session_ids.append(session.id)

    session_ordered = sorted(
        buckets.values(),
        key=lambda bucket: (bucket.backend, -bucket.snapshot.updated_at.timestamp()),
    )
    ordered: list[UsageDashboardBucket] = list(session_ordered)
    statuses: list[ProviderUsageStatus] = []
    if provider_service is not None:
        # Provider buckets sort after all session buckets.
        ordered.extend(provider_service.dashboard_buckets())
        statuses = provider_service.statuses()
    return UsageDashboardResponse(buckets=ordered, providers=statuses)
