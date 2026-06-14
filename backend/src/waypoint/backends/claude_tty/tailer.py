"""Transcript file tailer for the claude_tty backend.

Polls the session JSONL transcript by byte offset, normalizes each new record
into canonical events, and emits them through the runtime.  Periodically checks
whether the tmux pane is still alive and marks the session EXITED when it dies.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoint.backends.claude_tty.normalize import TranscriptNormalizer
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import SessionStatus

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

_POLL_INTERVAL = 0.5  # seconds between transcript polls
_PANE_CHECK_INTERVAL = 10.0  # seconds between tmux pane liveness checks


def transcript_path(cwd: str, session_uuid: str) -> Path:
    """Return the Claude TUI's JSONL transcript path for the given session."""
    dashed = cwd.replace("/", "-")
    return Path.home() / ".claude" / "projects" / dashed / f"{session_uuid}.jsonl"


class TranscriptTailer:
    """Background task that tails a Claude TUI transcript and emits events.

    ``start_at_end=True`` skips existing content (used on boot-time restore so
    already-emitted records are not replayed).  The default (``False``) reads
    from byte 0, which is correct for fresh sessions where the transcript file
    will be created on first user input.
    """

    def __init__(
        self,
        session_id: str,
        session_uuid: str,
        cwd: str,
        runtime: "SessionRuntime",
        *,
        start_at_end: bool = False,
    ) -> None:
        self._session_id = session_id
        self._path = transcript_path(cwd, session_uuid)
        self._runtime = runtime
        self._normalizer = TranscriptNormalizer()
        self._pane_check_elapsed = 0.0
        self._offset = (
            self._path.stat().st_size if start_at_end and self._path.exists() else 0
        )

    def _read_new_bytes(self) -> bytes:
        if not self._path.exists():
            return b""
        try:
            with self._path.open("rb") as fh:
                fh.seek(self._offset)
                return fh.read()
        except OSError:
            return b""

    async def _drain(self) -> None:
        data = await asyncio.to_thread(self._read_new_bytes)
        if not data:
            return

        lines = data.split(b"\n")
        consumed = len(data)
        # If the file ends without a trailing newline the last element is a
        # partial record; rewind so it is re-read on the next poll.
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
                log.warning(
                    "transcript JSON decode error",
                    extra={"session_id": self._session_id, "line": raw_line[:200]},
                )
                continue
            for ev in self._normalizer.process_record(record):
                await self._runtime._emit_adapter_event(
                    self._session_id,
                    ev.kind,
                    ev.text,
                    ev.metadata,
                    ev.status,
                )

    async def _pane_alive(self) -> bool:
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return False
        state = session.transport_state
        target = state.get("tmux_pane") or state.get("tmux_session") or self._session_id
        try:
            info = await self._runtime.tmux.describe_target(target)
        except TmuxError:
            return False
        return not info.pane_dead

    async def run(self) -> None:
        try:
            while True:
                session = self._runtime.storage.get_session(self._session_id)
                if session is None:
                    return

                await self._drain()

                if session.status in (SessionStatus.EXITED, SessionStatus.ERROR):
                    # One final drain in case records landed between the status
                    # check and this point.
                    await self._drain()
                    return

                self._pane_check_elapsed += _POLL_INTERVAL
                if self._pane_check_elapsed >= _PANE_CHECK_INTERVAL:
                    self._pane_check_elapsed = 0.0
                    if not await self._pane_alive():
                        await self._drain()
                        await self._runtime._record_system_event(
                            self._session_id,
                            "Claude TUI session exited",
                            status=SessionStatus.EXITED,
                        )
                        return

                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "transcript tailer crashed",
                extra={"session_id": self._session_id},
            )
