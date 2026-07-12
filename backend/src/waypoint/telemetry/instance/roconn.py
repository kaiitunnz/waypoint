"""Read-only, no-migration SQLite access for instance-health measurement.

The normal ``Storage`` connection opens read-write and runs WAL pragmas plus
idempotent schema migration on every construction. Instance-health collection
must never do that against a live server's database (PRD NFR Safety): it opens
a dedicated ``mode=ro`` connection with a bounded busy timeout, never
initializes schema, never enables writes, and never takes an exclusive lock. A
brief SQLite shared-read lock is permitted; lock contention, a runaway query,
or an unopenable file all degrade to a partial/unavailable snapshot rather than
delaying runtime work.
"""

import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

# PRD NFR Safety/Performance: a 250 ms lock-acquisition timeout and a separate
# 250 ms per-query execution budget on top of it.
LOCK_TIMEOUT_MS = 250
QUERY_BUDGET_MS = 250


@contextmanager
def open_readonly(db_path: Path) -> Iterator[sqlite3.Connection | None]:
    """Yield a read-only connection to an existing database, or ``None``.

    Yields ``None`` (never raises) when the file is absent or cannot be opened
    read-only — the caller reports the database category as unavailable rather
    than failing the whole snapshot. A WAL-mode database with no live writer can
    fail to open read-only when its shared-memory index is absent; that too is
    an acceptable degradation to ``None``.
    """
    if not db_path.exists():
        yield None
        return
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={LOCK_TIMEOUT_MS}")
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        if conn is not None:
            conn.close()
        yield None
        return
    try:
        yield conn
    finally:
        conn.close()


def budgeted_query(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[object] = (),
    *,
    budget_ms: int = QUERY_BUDGET_MS,
) -> list[sqlite3.Row] | None:
    """Run a read query under a wall-clock execution budget.

    Installs a progress handler that aborts the statement once ``budget_ms``
    elapses so a contended or pathological read returns control instead of
    blocking the collector. Returns the fetched rows, or ``None`` if the query
    was aborted, timed out on the lock, or errored — the caller treats that as a
    partial/unavailable category rather than raising into the request.
    """
    deadline = time.monotonic() + budget_ms / 1000.0

    def _watchdog() -> int:
        return 1 if time.monotonic() > deadline else 0

    # 1000 VM steps between checks keeps the handler cheap while still bounding
    # a runaway query to well under the budget in wall-clock terms.
    conn.set_progress_handler(_watchdog, 1000)
    try:
        return list(conn.execute(sql, tuple(params)).fetchall())
    except sqlite3.Error:
        return None
    finally:
        conn.set_progress_handler(None, 0)
