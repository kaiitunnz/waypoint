"""Aggregate session rate-limit snapshots into per-account dashboard buckets.

Rate limits live at the provider account level (a Claude org's 5h/weekly
window is shared across every session signed into that org), so the
dashboard groups by ``(backend, account_key)`` rather than per session.
The account key is derived from the ``notes`` list on each snapshot —
Claude carries ``org: <name>``/``org tier: <tier>``; Codex carries an
email plus ``plan: <plan_type>``.
"""

from waypoint.schemas import (
    BackendId,
    SessionRateLimitUsage,
    SessionRecord,
    UsageDashboardBucket,
    UsageDashboardResponse,
)

_CLAUDE_ORG_PREFIX = "org: "
_CLAUDE_ORG_TIER_PREFIX = "org tier: "
_PLAN_PREFIX = "plan: "
_DEFAULT_SEED_NOTES = {"CLI OAuth", "remote OAuth"}


def _find_prefixed(notes: list[str], prefix: str) -> str | None:
    for note in notes:
        if note.startswith(prefix):
            value = note[len(prefix) :].strip()
            if value:
                return value
    return None


def _find_email(notes: list[str]) -> str | None:
    for note in notes:
        if note in _DEFAULT_SEED_NOTES:
            continue
        if note.startswith(_PLAN_PREFIX):
            continue
        if "@" in note and " " not in note:
            return note
    return None


def account_bucket_for(
    snapshot: SessionRateLimitUsage, *, session_id: str
) -> tuple[str, str]:
    """Return ``(account_key, account_label)`` for a snapshot.

    Falls back to a session-scoped key when notes carry no account info,
    so probes without org/email metadata still surface as their own
    bucket instead of collapsing together.
    """
    if snapshot.source == "claude_code":
        org = _find_prefixed(snapshot.notes, _CLAUDE_ORG_PREFIX)
        if org is not None:
            tier = _find_prefixed(snapshot.notes, _CLAUDE_ORG_TIER_PREFIX)
            label = f"{org} · {tier}" if tier else org
            return f"claude_code:{org}", label
    elif snapshot.source == "codex":
        email = _find_email(snapshot.notes)
        if email is not None:
            plan = _find_prefixed(snapshot.notes, _PLAN_PREFIX)
            label = f"{email} · plan: {plan}" if plan else email
            return f"codex:{email}", label
    return f"{snapshot.source}:session:{session_id}", _humanise_backend(snapshot.source)


def _humanise_backend(backend: BackendId) -> str:
    if backend == "claude_code":
        return "Claude Code"
    if backend == "codex":
        return "Codex"
    return backend


def build_dashboard(sessions: list[SessionRecord]) -> UsageDashboardResponse:
    # ``session_ids[0]`` is the session that produced the freshest snapshot
    # in the bucket — refresh paths target it so a stale/torn-down session
    # cannot silently no-op the probe when a live one is available.
    buckets: dict[str, UsageDashboardBucket] = {}
    for session in sessions:
        snapshot = session.rate_limit_usage
        if snapshot is None:
            continue
        key, label = account_bucket_for(snapshot, session_id=session.id)
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
