"""SQLite-based ContextUsageSource for opencode sessions over the tmux transport.

Polls the opencode SQLite DB (read-only) to extract token counts from the latest
assistant message and publishes SessionContextUsage updates to the runtime.
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoint.backends.context_usage_source import ContextUsageSource
from waypoint.backends.opencode.adapter import _non_negative_int
from waypoint.schemas import SessionContextUsage

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.opencode")

_POLL_INTERVAL = 1.5


def _opencode_db_dir() -> Path:
    xdg_data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg_data) / "opencode"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _normalize_path(p: str) -> str:
    return os.path.realpath(os.path.normpath(p))


def _find_session_id(conn: sqlite3.Connection, cwd: str) -> str | None:
    cur = conn.cursor()
    # Fast exact match first.
    cur.execute(
        "SELECT id FROM session WHERE directory = ? ORDER BY time_updated DESC LIMIT 1",
        (cwd,),
    )
    row = cur.fetchone()
    if row is not None:
        return row[0]
    # Normalized fallback — handles trailing slashes, symlinked worktrees, and
    # platform realpath differences (e.g. /tmp vs /private/tmp on macOS).
    normalized_cwd = _normalize_path(cwd)
    cur.execute("SELECT id, directory FROM session ORDER BY time_updated DESC")
    for sess_id, directory in cur.fetchall():
        if _normalize_path(directory) == normalized_cwd:
            return sess_id
    return None


def _snapshot_from_data(data: dict[str, Any]) -> SessionContextUsage | None:
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None

    input_tokens = _non_negative_int(tokens.get("input"))
    output_tokens = _non_negative_int(tokens.get("output"))
    reasoning_tokens = _non_negative_int(tokens.get("reasoning"))
    cache = tokens.get("cache")
    if not isinstance(cache, dict):
        cache = {}
    cache_read_tokens = _non_negative_int(cache.get("read"))
    cache_write_tokens = _non_negative_int(cache.get("write"))

    used_tokens = sum(
        v
        for v in (input_tokens, cache_read_tokens, cache_write_tokens)
        if v is not None
    )
    if used_tokens <= 0:
        return None

    breakdown = {
        key: value
        for key, value in {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        }.items()
        if value is not None
    }
    return SessionContextUsage(
        used_tokens=used_tokens,
        context_window_tokens=None,
        updated_at=datetime.now(UTC),
        source="opencode",
        breakdown=breakdown,
    )


def _latest_assistant_snapshot(
    conn: sqlite3.Connection, opencode_session_id: str
) -> SessionContextUsage | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT data FROM message
        WHERE session_id = ? AND json_extract(data, '$.role') = 'assistant'
        ORDER BY time_updated DESC LIMIT 1
        """,
        (opencode_session_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    try:
        data: dict[str, Any] = json.loads(row[0])
    except (json.JSONDecodeError, ValueError):
        return None
    return _snapshot_from_data(data)


class OpenCodeTmuxUsageSource(ContextUsageSource):
    def __init__(
        self,
        session_id: str,
        cwd: str,
        runtime: "SessionRuntime",
        db_dir: Path,
    ) -> None:
        self._session_id = session_id
        self._cwd = cwd
        self._runtime = runtime
        self._db_dir = db_dir
        self._signature: tuple[int, int | None] | None = None

    def _query_snapshot(self) -> SessionContextUsage | None:
        db_path = self._db_dir / "opencode.db"
        if not db_path.exists():
            return None
        try:
            conn = _connect_ro(db_path)
            try:
                opencode_session_id = _find_session_id(conn, self._cwd)
                if opencode_session_id is None:
                    return None
                return _latest_assistant_snapshot(conn, opencode_session_id)
            finally:
                conn.close()
        except Exception:
            log.debug(
                "opencode DB poll failed",
                extra={"session_id": self._session_id},
                exc_info=True,
            )
            return None

    async def run(self) -> None:
        try:
            while True:
                snapshot = await asyncio.to_thread(self._query_snapshot)
                if snapshot is not None:
                    sig = (snapshot.used_tokens, snapshot.context_window_tokens)
                    if sig != self._signature:
                        self._signature = sig
                        await self._runtime.update_session_fields(
                            self._session_id, context_usage=snapshot
                        )
                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "opencode usage source crashed",
                extra={"session_id": self._session_id},
            )
