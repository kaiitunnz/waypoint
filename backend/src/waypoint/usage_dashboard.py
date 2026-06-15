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
    SessionRateLimitUsage,
    SessionRecord,
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


def account_bucket_for(
    snapshot: SessionRateLimitUsage,
    *,
    session_id: str,
    registry: _PluginRegistry,
) -> tuple[str, str]:
    """Return ``(account_key, account_label)`` for a snapshot.

    Dispatches the account-scoping decision to the snapshot's agent plugin
    (``rate_limit_account``). Falls back to a session-scoped key labelled
    with the plugin's human name when the plugin declines (no account info)
    or is unknown, so probes without org/email metadata still surface as
    their own bucket instead of collapsing together.
    """
    plugin = (
        registry.get(snapshot.source) if registry.has_backend(snapshot.source) else None
    )
    if plugin is not None:
        resolver = getattr(plugin, "rate_limit_account", None)
        if resolver is not None:
            account = resolver(snapshot)
            if account is not None:
                return account
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
    sessions: list[SessionRecord], registry: _PluginRegistry
) -> UsageDashboardResponse:
    # ``session_ids[0]`` is the session that produced the freshest snapshot
    # in the bucket — refresh paths target it so a stale/torn-down session
    # cannot silently no-op the probe when a live one is available.
    buckets: dict[str, UsageDashboardBucket] = {}
    for session in sessions:
        snapshot = session.rate_limit_usage
        if snapshot is None:
            continue
        key, label = account_bucket_for(
            snapshot, session_id=session.id, registry=registry
        )
        existing = buckets.get(key)
        if existing is None:
            buckets[key] = UsageDashboardBucket(
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

    ordered = sorted(
        buckets.values(),
        key=lambda bucket: (bucket.backend, -bucket.snapshot.updated_at.timestamp()),
    )
    return UsageDashboardResponse(buckets=ordered)
