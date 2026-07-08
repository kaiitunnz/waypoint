"""ContextUsageSource for Codex sessions running over the generic tmux transport.

Tails the Codex rollout JSONL by byte offset and publishes SessionContextUsage
whenever a ``token_count`` event appears.  The rollout format uses snake_case
(``last_token_usage``, ``model_context_window``) which differs from the
camelCase fields in the app-server notification path.
"""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoint.backends.codex.adapter import _positive_int
from waypoint.backends.context_usage_source import ContextUsageSource
from waypoint.schemas import SessionContextUsage

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.codex")

_POLL_INTERVAL = 1.0


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def find_codex_rollout(thread_id: str, codex_home: str | None = None) -> Path | None:
    """The rollout JSONL for ``thread_id`` under ``codex_home``.

    An explicit ``codex_home`` (e.g. a target account profile's) wins, else
    ``$CODEX_HOME`` / ``~/.codex``. Returns ``None`` when absent — the signal a
    target profile can't yet see the thread.
    """
    # Codex thread ids are UUIDs; guard before it reaches a glob pattern.
    if not _UUID_RE.match(thread_id):
        return None
    home = Path(codex_home or os.environ.get("CODEX_HOME") or "~/.codex").expanduser()
    sessions_dir = home / "sessions"
    if not sessions_dir.is_dir():
        return None
    suffix = f"-{thread_id}.jsonl"
    return next(sessions_dir.glob(f"*/*/*/rollout-*{suffix}"), None)


def _find_rollout_path(thread_id: str) -> Path | None:
    return find_codex_rollout(thread_id)


def _parse_token_count_record(record: dict[str, Any]) -> SessionContextUsage | None:
    """Extract a SessionContextUsage from a rollout ``token_count`` event_msg.

    Rollout fields are snake_case; the camelCase app-server path is handled
    separately by ``_context_usage_snapshot_from_thread_token_usage``.
    """
    if record.get("type") != "event_msg":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    last_usage = info.get("last_token_usage")
    if not isinstance(last_usage, dict):
        return None
    used_tokens = _positive_int(last_usage.get("total_tokens"))
    if used_tokens is None:
        return None
    context_window = _positive_int(info.get("model_context_window"))
    breakdown = {
        key: value
        for key, value in {
            "input_tokens": _positive_int(last_usage.get("input_tokens")),
            "cached_input_tokens": _positive_int(last_usage.get("cached_input_tokens")),
            "output_tokens": _positive_int(last_usage.get("output_tokens")),
            "reasoning_output_tokens": _positive_int(
                last_usage.get("reasoning_output_tokens")
            ),
        }.items()
        if value is not None
    }
    return SessionContextUsage(
        used_tokens=used_tokens,
        context_window_tokens=context_window,
        updated_at=datetime.now(UTC),
        source="codex",
        breakdown=breakdown,
    )


class CodexRolloutUsageSource(ContextUsageSource):
    """Tails a Codex rollout JSONL and publishes context usage for tmux sessions."""

    def __init__(self, session_id: str, runtime: "SessionRuntime") -> None:
        self._session_id = session_id
        self._runtime = runtime
        self._offset = 0
        self._context_usage_signature: tuple[int, int | None] | None = None

    def _read_new_bytes(self, path: Path) -> bytes:
        try:
            with path.open("rb") as fh:
                fh.seek(self._offset)
                return fh.read()
        except OSError:
            return b""

    async def _drain(self, path: Path) -> None:
        data = await asyncio.to_thread(self._read_new_bytes, path)
        if not data:
            return

        lines = data.split(b"\n")
        consumed = len(data)
        if not data.endswith(b"\n"):
            partial = lines.pop()
            consumed -= len(partial)
        self._offset += consumed

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshot = _parse_token_count_record(record)
            if snapshot is None:
                continue
            sig = (snapshot.used_tokens, snapshot.context_window_tokens)
            if sig == self._context_usage_signature:
                continue
            self._context_usage_signature = sig
            await self._runtime.update_session_fields(
                self._session_id, context_usage=snapshot
            )

    async def run(self) -> None:
        try:
            thread_id: str | None = None
            while thread_id is None:
                session = self._runtime.storage.get_session(self._session_id)
                if session is None:
                    return
                tid = session.transport_state.get("thread_id")
                if isinstance(tid, str) and tid:
                    thread_id = tid
                else:
                    await asyncio.sleep(_POLL_INTERVAL)

            path: Path | None = None
            while path is None:
                path = await asyncio.to_thread(_find_rollout_path, thread_id)
                if path is None:
                    await asyncio.sleep(_POLL_INTERVAL)

            while True:
                session = self._runtime.storage.get_session(self._session_id)
                if session is None:
                    return
                await self._drain(path)
                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "codex rollout usage source crashed",
                extra={"session_id": self._session_id},
            )
