"""Transcript file tailer for the claude_tty (Emulated) transport.

Polls the session JSONL transcript by byte offset, normalizes each new record
into canonical events, and emits them through the runtime.  Periodically checks
whether the tmux pane is still alive and marks the session EXITED when it dies,
and has its ``DialogWatcher`` read the pane's dialogs.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from waypoint.backends.claude_code.adapter import (
    _context_usage_snapshot_from_message,
    claude_token_usage_record,
)
from waypoint.backends.claude_code.models import (
    ClaudeContextWindowResolver,
    claude_context_window_for_model,
    is_claude_plan_switching_model,
    observe_claude_model,
)
from waypoint.backends.claude_code.session_folder import follow_relocation
from waypoint.backends.claude_tty.byte_source import READ_LIMIT, TailedTranscript
from waypoint.backends.claude_tty.dialog_watch import DialogWatcher, pane_target
from waypoint.backends.claude_tty.normalize import (
    SYNTHETIC_MODEL_ID,
    TranscriptNormalizer,
)
from waypoint.backends.claude_tty.pending_calls import CallSource
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import EventKind, SessionStatus

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

_POLL_INTERVAL = 0.5  # seconds between transcript polls
_PANE_CHECK_INTERVAL = 10.0  # seconds between tmux pane liveness checks
_DIALOG_POLL_INTERVAL = 1.0  # seconds between live-pane dialog captures
# Protective cap on the unparsed trailing buffer: a JSONL record that never
# completes past this size is dropped rather than grown without bound.
_MAX_PARTIAL_BYTES = 8 * 1024 * 1024
# The CLI re-appends ``relocated`` about every 32 KB it writes, so the tail of an
# existing transcript holds the latest one. One remote read covers it.
_RELOCATION_TAIL_BYTES = READ_LIMIT


def _relocated_folder(record: dict[str, Any]) -> str | None:
    """The folder Claude says it moved the transcript to. The other records'
    ``cwd`` also follows a shell ``cd``, which moves nothing."""
    folder = record.get("relocatedCwd")
    if record.get("type") != "relocated" or not isinstance(folder, str):
        return None
    return folder or None


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
        source: TailedTranscript,
        runtime: "SessionRuntime",
        plugin: "ClaudeTtyPlugin",
        *,
        start_at_end: bool = False,
        calls: CallSource | None = None,
        context_window_resolver: ClaudeContextWindowResolver = (
            claude_context_window_for_model
        ),
    ) -> None:
        self._session_id = session_id
        self._source = source
        self._runtime = runtime
        self._plugin = plugin
        self._context_window_resolver = context_window_resolver
        self.dialogs = DialogWatcher(session_id, runtime, plugin, source, calls)
        self._normalizer = TranscriptNormalizer(
            capture_enabled=runtime.settings.task_output_capture_enabled
        )
        self._pane_check_elapsed = 0.0
        self._dialog_check_elapsed = 0.0
        # Cursor state, source-independent. ``start_at_end`` is applied lazily on
        # the first successful observation (see ``_drain``), never here — the
        # constructor runs on the event loop and must not do IO.
        self._start_at_end = start_at_end
        self._primed = False
        self._fetch_offset = 0
        self._partial = b""
        self._identity: tuple[int, int] | None = None
        self._context_usage_signature: (
            tuple[int, int | None, tuple[tuple[str, int], ...]] | None
        ) = None
        # Previous reply's model for this pane, reset per construction so the
        # first reply after a boundary reads as first. Dedup for repeated records
        # of one message.
        self._last_observed_base: str | None = None
        self._model_seen_ids: set[str] = set()
        self._stop_requested = False

    def stop(self) -> None:
        """Finish after one last forced drain, leaving dialog state for the next
        tailer to adopt."""
        self._stop_requested = True

    async def _drain(self, *, force: bool = False) -> None:
        # The priming tick fetches size + identity only (no body).
        metadata_only = self._start_at_end and not self._primed
        read = await asyncio.to_thread(
            self._source.read_from,
            self._fetch_offset,
            metadata_only=metadata_only,
            force=force,
        )
        if not read.observed:
            return

        # First observation: record identity and, for start-at-end, jump to the
        # current end without emitting (records already on disk are in the DB).
        # This is correct regardless of whether the source resolved at
        # construction time, so a transient boot-time miss never replays history.
        if not self._primed:
            self._primed = True
            self._identity = read.identity
            if self._start_at_end:
                self._fetch_offset = read.size or 0
                self._partial = b""
                await self._follow_earlier_move(self._fetch_offset)
                return

        # Truncation (size shrank below the cursor) or replacement (file identity
        # changed): the store cannot dedup a replay, so skip the ambiguous bytes,
        # jump to the new end, and record one content-free note. Never re-read.
        identity_changed = (
            self._identity is not None
            and read.identity is not None
            and read.identity != self._identity
        )
        truncated = read.size is not None and read.size < self._fetch_offset
        if identity_changed or truncated:
            self._partial = b""
            self._fetch_offset = read.size or 0
            self._identity = read.identity
            log.warning(
                "transcript discontinuity; skipping to end",
                extra={
                    "session_id": self._session_id,
                    "reason": "identity_changed" if identity_changed else "truncated",
                },
            )
            await self._runtime._record_system_event(
                self._session_id,
                "Transcript was replaced or truncated; resuming from its end.",
            )
            return

        self._identity = read.identity or self._identity
        if not read.data:
            return

        buf = self._partial + read.data
        self._fetch_offset += len(read.data)
        parts = buf.split(b"\n")
        # A trailing element without a newline is an incomplete record; keep it
        # for the next read. ``split`` always yields a final element (b"" when
        # ``buf`` ends in a newline), so this uniformly handles both cases.
        self._partial = parts[-1]
        lines = parts[:-1]
        if len(self._partial) > _MAX_PARTIAL_BYTES:
            log.warning(
                "transcript partial record exceeded cap; dropping",
                extra={"session_id": self._session_id},
            )
            self._partial = b""

        relocated: str | None = None
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
            relocated = _relocated_folder(record) or relocated
            if record.get("type") == "assistant":
                # Before the normalizer, so the divider precedes the new-model turn.
                await self._maybe_observe_model(record)
            for ev in self._normalizer.process_record(record):
                if ev.kind is EventKind.STATUS_UPDATE and ev.status is not None:
                    await self._runtime.update_session_fields(
                        self._session_id, status=ev.status
                    )
                    continue
                await self._runtime._emit_adapter_event(
                    self._session_id,
                    ev.kind,
                    ev.text,
                    ev.metadata,
                    ev.status,
                )
            if record.get("type") == "assistant":
                await self._maybe_publish_context_usage(record)
        # After the records: the offset is past them, so a restart during the
        # follow would drop any not yet emitted.
        if relocated is not None:
            await follow_relocation(self._runtime, self._session_id, relocated)

    async def _follow_earlier_move(self, end: int) -> None:
        """Follow a move made before this tailer started, as after a restart or
        reattach."""
        read = await asyncio.to_thread(
            self._source.read_from, max(0, end - _RELOCATION_TAIL_BYTES), force=True
        )
        for line in reversed(read.data.split(b"\n")):
            if b'"relocated"' not in line:
                continue
            try:
                record: dict[str, Any] = json.loads(line)
            except ValueError:  # a window cut inside a character or record
                continue
            if folder := _relocated_folder(record):
                await follow_relocation(self._runtime, self._session_id, folder)
                return

    async def _maybe_observe_model(self, record: dict[str, Any]) -> None:
        """Record a real assistant reply's model, adopt it into the selection
        when diverged (no restart), and emit a ``model.change`` note. Synthetic,
        repeated same-id, and plan-switching selections are skipped.
        """
        message: dict[str, Any] = record.get("message") or {}
        concrete = str(message.get("model") or "")
        if not concrete or concrete == SYNTHETIC_MODEL_ID:
            return
        message_id = str(message.get("id") or record.get("uuid") or "")
        if message_id:
            if message_id in self._model_seen_ids:
                return
            self._model_seen_ids.add(message_id)

        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return
        selection = session.model
        if is_claude_plan_switching_model(selection):
            return

        previous_base = self._last_observed_base
        observation = observe_claude_model(concrete, selection, previous_base)
        if observation is None:
            return
        self._last_observed_base = observation.resolved_base

        updates: dict[str, Any] = {"resolved_model": observation.resolved_base}
        if observation.adopt_selection is not None:
            updates["model"] = observation.adopt_selection
        if session.resolved_model != observation.resolved_base or "model" in updates:
            await self._runtime.update_session_fields(self._session_id, **updates)

        if observation.reason is None:
            return
        current = observation.resolved_base
        if observation.reason == "initial_mismatch":
            text = f"Running {current} (selected {selection})"
        else:
            text = f"Model changed to {current}"
        # session.status keeps the event-insert COALESCE a no-op; this note fires
        # mid-turn and must not flip the lifecycle.
        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.SYSTEM_NOTE,
            text,
            {
                "method": "model.change",
                "reason": observation.reason,
                "previous_model": previous_base,
                "current_model": current,
                "selection": selection,
            },
            session.status,
        )

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
        snapshot = _context_usage_snapshot_from_message(
            model, usage, self._context_window_resolver
        )
        if snapshot is None:
            return
        # The ledger's model is the concrete resolved id for *this* turn, taken
        # from the transcript message itself (authoritative "actual model at
        # turn time", FR-4) — never the session's mutable latest ``resolved_model``,
        # which would rewrite earlier turns onto the current model when the
        # transcript is replayed from offset 0 after a resume. The session model
        # is only a fallback for a message with no model field.
        record_model = (str(message.get("model") or "") or None) or (
            session.resolved_model if session is not None else None
        )
        effort = session.effort if session is not None else None
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
        token_record = claude_token_usage_record(
            record_id, snapshot, model=record_model, effort=effort
        )
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

    async def _pane_alive(self) -> bool:
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return False
        try:
            info = await self._runtime.tmux.describe_target(pane_target(session))
        except TmuxError:
            return False
        return not info.pane_dead

    async def run(self) -> None:
        try:
            self.dialogs.adopt_open_cards()
            await self._loop()
            if self._stop_requested:
                return
        except Exception:
            log.exception(
                "transcript tailer crashed",
                extra={"session_id": self._session_id},
            )
        # Not on cancel: terminate/restart close the card themselves; after a
        # backend shutdown the next tailer adopts it.
        await self.dialogs.close()

    async def _loop(self) -> None:
        while True:
            if self._stop_requested:
                await self._drain(force=True)
                return
            session = self._runtime.storage.get_session(self._session_id)
            if session is None:
                return

            await self._drain()

            if session.status not in (SessionStatus.EXITED, SessionStatus.ERROR):
                self._dialog_check_elapsed += _POLL_INTERVAL
                if self._dialog_check_elapsed >= _DIALOG_POLL_INTERVAL:
                    self._dialog_check_elapsed = 0.0
                    await self.dialogs.poll()

            if session.status in (SessionStatus.EXITED, SessionStatus.ERROR):
                # One final drain in case records landed between the status
                # check and this point. ``force`` bypasses a remote source's
                # poll cadence so a tail written in the last second isn't lost.
                await self._drain(force=True)
                return

            self._pane_check_elapsed += _POLL_INTERVAL
            if self._pane_check_elapsed >= _PANE_CHECK_INTERVAL:
                self._pane_check_elapsed = 0.0
                if not await self._pane_alive():
                    await self._drain(force=True)
                    await self._runtime._record_system_event(
                        self._session_id,
                        "Claude TUI session exited",
                        status=SessionStatus.EXITED,
                    )
                    return

            await asyncio.sleep(_POLL_INTERVAL)
