"""In-process browser-presence leases for session pages.

A visible ``/session/<id>`` page registers and renews a short lease keyed by an
opaque per-tab viewer id. Notification producers consult :meth:`is_active` to
suppress redundant session-interaction alerts while the operator is looking at
that exact session. State is memory-only, node-local, and erased on restart;
leases use ``time.monotonic()`` so a wall-clock adjustment cannot extend them.
"""

import time

# Longer than the frontend's 15s renewal, so one missed renewal does not drop
# presence; short enough that a crashed tab stops suppressing within a minute.
LEASE_SECONDS = 45.0


class SessionPresenceRegistry:
    def __init__(self, *, lease_seconds: float = LEASE_SECONDS) -> None:
        self._lease_seconds = lease_seconds
        self._leases: dict[str, dict[str, float]] = {}

    def touch(
        self, session_id: str, viewer_id: str, *, now: float | None = None
    ) -> None:
        """Set/renew a lease for ``viewer_id`` on ``session_id`` and prune the
        session's expired viewers. Identity/authorization is the caller's job."""
        moment = time.monotonic() if now is None else now
        viewers = self._leases.setdefault(session_id, {})
        viewers[viewer_id] = moment + self._lease_seconds
        self._prune(session_id, moment)

    def release(self, session_id: str, viewer_id: str) -> None:
        """Drop only the ``(session_id, viewer_id)`` lease. Idempotent."""
        viewers = self._leases.get(session_id)
        if viewers is None:
            return
        viewers.pop(viewer_id, None)
        if not viewers:
            self._leases.pop(session_id, None)

    def is_active(self, session_id: str, *, now: float | None = None) -> bool:
        """True when any unexpired viewer remains for ``session_id``."""
        moment = time.monotonic() if now is None else now
        self._prune(session_id, moment)
        return bool(self._leases.get(session_id))

    def drop_session(self, session_id: str) -> None:
        """Forget all leases for a deleted session."""
        self._leases.pop(session_id, None)

    def clear(self) -> None:
        """Forget every lease (process-local runtime shutdown)."""
        self._leases.clear()

    def _prune(self, session_id: str, now: float) -> None:
        viewers = self._leases.get(session_id)
        if viewers is None:
            return
        expired = [vid for vid, expires_at in viewers.items() if expires_at <= now]
        for vid in expired:
            del viewers[vid]
        if not viewers:
            self._leases.pop(session_id, None)
