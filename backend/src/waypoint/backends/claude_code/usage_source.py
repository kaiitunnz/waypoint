"""Context-usage source for the claude_code plugin on the generic tmux transport.

Tails the Claude TUI JSONL transcript (same file as the claude_tty tailer)
to extract context-usage data and publish it to the runtime without the
dialog/pane-management machinery that claude_tty needs.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from waypoint.backends.claude_code.adapter import (
    _context_usage_snapshot_from_message,
    claude_token_usage_record,
)
from waypoint.backends.claude_tty.tailer import transcript_path
from waypoint.backends.context_usage_source import ContextUsageSource

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_code")

_POLL_INTERVAL = 0.5


class TranscriptContextUsageSource(ContextUsageSource):
    """Tails the Claude TUI transcript and publishes context usage for tmux-wrapped sessions."""

    def __init__(
        self,
        session_id: str,
        session_uuid: str,
        cwd: str,
        runtime: "SessionRuntime",
        config_dir: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._path = transcript_path(cwd, session_uuid, config_dir)
        self._runtime = runtime
        self._offset = 0
        self._context_usage_signature: (
            tuple[int, int | None, tuple[tuple[str, int], ...]] | None
        ) = None

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
            if record.get("type") == "assistant":
                await self._maybe_publish_context_usage(record)

    async def _maybe_publish_context_usage(self, record: dict[str, Any]) -> None:
        message: dict[str, Any] = record.get("message") or {}
        usage: dict[str, Any] = message.get("usage") or {}
        # Prefer the session's configured model alias for the window: it carries
        # the ``[1m]`` marker (→ 1M window), whereas the transcript's resolved API
        # id normalizes to the base family and loses it. Read it fresh each publish
        # so a dynamic model change is reflected on the next snapshot.
        session = self._runtime.storage.get_session(self._session_id)
        model = (session.model if session is not None else None) or (
            str(message.get("model") or "") or None
        )
        snapshot = _context_usage_snapshot_from_message(model, usage)
        if snapshot is None:
            return
        # Key on the breakdown too, so a same-total/different-split turn refreshes.
        sig = (
            snapshot.used_tokens,
            snapshot.context_window_tokens,
            tuple(sorted(snapshot.breakdown.items())),
        )
        context_changed = sig != self._context_usage_signature
        # Key the ledger on the message id (uuid fallback) so offset-zero replay
        # is idempotent; recorded regardless of the snapshot dedup so two turns
        # with identical totals both count. Broadcast it here only when the
        # context publish below won't (a deduped snapshot), so the aggregate
        # increment is never stranded, yet a changed turn still emits one frame.
        record_id = str(message.get("id") or record.get("uuid") or "")
        token_record = claude_token_usage_record(record_id, snapshot)
        if token_record is not None:
            await self._runtime.publish_token_usage_record(
                self._session_id, token_record, publish=not context_changed
            )
        if not context_changed:
            return
        self._context_usage_signature = sig
        await self._runtime.update_session_fields(
            self._session_id, context_usage=snapshot
        )

    async def run(self) -> None:
        try:
            while True:
                await self._drain()
                await asyncio.sleep(_POLL_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception(
                "context usage tailer crashed",
                extra={"session_id": self._session_id},
            )
