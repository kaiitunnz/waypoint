"""Transcript file tailer for the claude_tty backend.

Polls the session JSONL transcript by byte offset, normalizes each new record
into canonical events, and emits them through the runtime.  Periodically checks
whether the tmux pane is still alive and marks the session EXITED when it dies.

Also polls the live pane for tool-permission dialogs (when the session's
permission_mode requires them) and emits APPROVAL_REQUEST events so the
frontend can surface them.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from waypoint.backends.claude_code.normalize import format_approval_text
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.claude_tty._state import PendingTtyApproval
from waypoint.backends.claude_tty.normalize import TranscriptNormalizer
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import EventKind, SessionStatus

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

_POLL_INTERVAL = 0.5  # seconds between transcript polls
_PANE_CHECK_INTERVAL = 10.0  # seconds between tmux pane liveness checks
_DIALOG_POLL_INTERVAL = 1.0  # seconds between live-pane dialog captures
_DIALOG_STABLE_TICKS = 2  # consecutive identical captures before surfacing


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
        plugin: "ClaudeTtyPlugin",
        *,
        start_at_end: bool = False,
    ) -> None:
        self._session_id = session_id
        self._path = transcript_path(cwd, session_uuid)
        self._runtime = runtime
        self._plugin = plugin
        self._normalizer = TranscriptNormalizer()
        self._pane_check_elapsed = 0.0
        self._dialog_check_elapsed = 0.0
        self._offset = (
            self._path.stat().st_size if start_at_end and self._path.exists() else 0
        )
        # Dialog debounce state
        self._prev_dialog_sig: str | None = None
        self._dialog_stable_count: int = 0
        # Signature of the dialog already surfaced as an APPROVAL_REQUEST; held
        # until the dialog leaves the screen so a response that clears pending
        # before the pane redraws cannot trigger a duplicate emit.
        self._surfaced_sig: str | None = None

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

    async def _poll_dialog(self) -> None:
        """Capture the live pane and surface any stable tool-permission dialog.

        Detection is intentionally mode-agnostic: ``claude_tty`` cannot change
        permission mode inline (``supports_set_permission_mode_inline=False``),
        so the session's stored ``permission_mode`` is fixed at launch, while
        the TUI's real posture can drift independently (a human pressing
        shift+tab in the pane, or choosing "allow all this session"). Gating on
        the stored mode would miss a dialog that appears after an auto→prompting
        drift and hang the session, so we always trust the on-screen dialog
        rather than the recorded mode. (The run loop throttles how often this
        runs to keep the capture cost low on sessions that never prompt.)
        """
        session = self._runtime.storage.get_session(self._session_id)
        if session is None:
            return

        state = session.transport_state
        pane = state.get("tmux_pane") or state.get("tmux_session") or self._session_id

        try:
            snapshot = await self._runtime.tmux.capture_snapshot(pane)
        except TmuxError:
            return

        screen_type = pane_dialog.classify(snapshot)

        if screen_type is not pane_dialog.PaneScreen.APPROVAL:
            # Dialog gone — clear any pending approval for this session.
            self._plugin._pending_approvals.pop(self._session_id, None)
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            self._surfaced_sig = None
            return

        dialog = pane_dialog.parse_approval(snapshot)
        if dialog is None:
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            return

        sig = f"{dialog.tool_name}:{dialog.target}:{dialog.question}"

        if sig == self._prev_dialog_sig:
            self._dialog_stable_count += 1
        else:
            self._prev_dialog_sig = sig
            self._dialog_stable_count = 1

        if self._dialog_stable_count < _DIALOG_STABLE_TICKS:
            return

        # Already surfaced this exact dialog — do not re-emit, even once the
        # response has cleared the pending entry but the pane has not yet
        # redrawn the box away.
        if sig == self._surfaced_sig:
            return

        approve_num = dialog.approve_option.number if dialog.approve_option else 1
        decline_opt = dialog.decline_option
        decline_num = decline_opt.number if decline_opt else None

        approval_id = str(uuid.uuid4())
        tool_name = dialog.tool_name or "Unknown"
        target = dialog.target or ""

        if tool_name == "Bash":
            tool_input: dict[str, str] = {"command": target}
        elif tool_name in {"Write", "Edit", "MultiEdit"}:
            tool_input = {"file_path": target}
        else:
            tool_input = {"description": target}

        payload: dict[str, Any] = {"tool_name": tool_name, "tool_input": tool_input}
        text = format_approval_text(payload)

        self._plugin._pending_approvals[self._session_id] = PendingTtyApproval(
            approval_id=approval_id,
            tool_name=tool_name,
            target=target or None,
            approve_number=approve_num,
            decline_number=decline_num,
            signature=sig,
        )
        self._surfaced_sig = sig

        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.APPROVAL_REQUEST,
            text,
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
                "approval_id": approval_id,
                "method": "tty_permission",
                "status": SessionStatus.WAITING_INPUT,
            },
            SessionStatus.WAITING_INPUT,
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

                if session.status not in (SessionStatus.EXITED, SessionStatus.ERROR):
                    self._dialog_check_elapsed += _POLL_INTERVAL
                    if self._dialog_check_elapsed >= _DIALOG_POLL_INTERVAL:
                        self._dialog_check_elapsed = 0.0
                        await self._poll_dialog()

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
