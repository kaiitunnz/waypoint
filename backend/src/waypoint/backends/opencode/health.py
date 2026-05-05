"""Per-adapter health state with cooldown and circuit-breaker.

Each ``(launch_target_id, cwd)`` adapter slot has one ``AdapterHealth``
instance. After a server death, a brief cooldown stops a thundering
herd of reattach attempts from hammering SSH; after K consecutive
launch failures the slot is quarantined so subsequent calls fail fast
with a clear message instead of every request paying the full SSH
launch timeout.

User-initiated retries (the explicit /reattach button) bypass both
gates so the user can always force an immediate attempt.
"""

import time
from dataclasses import dataclass

# How long after a `_on_server_died` to reject non-user `start()` calls.
# Just enough to swallow the burst of reattach attempts that happen when
# multiple sessions on the same dead host all notice at once.
DEATH_COOLDOWN_SECONDS = 5.0

# Failures in a row before quarantining the slot. Tuned conservatively
# so a single transient flake during initial setup doesn't trip it.
QUARANTINE_AFTER_FAILURES = 3

# How long the slot stays quarantined. The Phase-6 reconnect loop and
# the user-initiated /reattach both bypass this, so the wait only
# affects passive callers (incoming HTTP requests).
QUARANTINE_SECONDS = 60.0


@dataclass
class AdapterHealth:
    last_death_at: float = 0.0
    consecutive_failures: int = 0
    quarantined_until: float = 0.0

    def can_attempt(self, *, user_initiated: bool = False) -> tuple[bool, str | None]:
        """Return ``(allowed, reason_when_blocked)``.

        ``reason_when_blocked`` is a short message suitable for surfacing
        to the API; ``None`` when allowed.
        """
        if user_initiated:
            return True, None
        now = time.monotonic()
        if self.quarantined_until > now:
            remaining = int(self.quarantined_until - now)
            return False, (
                f"opencode launch target unavailable (retry in ~{remaining}s)"
            )
        if self.last_death_at and now - self.last_death_at < DEATH_COOLDOWN_SECONDS:
            return False, "opencode connection just died; cooling down briefly"
        return True, None

    def record_death(self) -> None:
        self.last_death_at = time.monotonic()

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= QUARANTINE_AFTER_FAILURES:
            self.quarantined_until = time.monotonic() + QUARANTINE_SECONDS

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.quarantined_until = 0.0
        self.last_death_at = 0.0
