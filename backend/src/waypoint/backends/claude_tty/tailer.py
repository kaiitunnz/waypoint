"""Transcript file tailer for the claude_tty (Emulated) transport.

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

from waypoint.backends.claude_code.adapter import _context_usage_snapshot_from_message
from waypoint.backends.claude_code.normalize import format_approval_text
from waypoint.backends.claude_code.threads import (
    claude_projects_root,
    encode_project_dir,
)
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.claude_tty._state import PendingTtyApproval, PendingTtyQuestion
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
    """Return the Claude TUI's JSONL transcript path for the given session.

    Resolves the store root and the encoded project-dir name through the same
    helpers thread discovery uses, so the tailed path matches where the CLI
    actually writes — including for cwds with non-slash special characters
    (hidden dirs, worktrees) and a non-default ``$CLAUDE_CONFIG_DIR``.
    """
    return claude_projects_root() / encode_project_dir(cwd) / f"{session_uuid}.jsonl"


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
        self._context_usage_signature: tuple[int, int | None] | None = None
        # Dialog debounce state
        self._prev_dialog_sig: str | None = None
        self._dialog_stable_count: int = 0
        # Signature of the dialog already surfaced as an APPROVAL_REQUEST; held
        # until the dialog leaves the screen so a response that clears pending
        # before the pane redraws cannot trigger a duplicate emit.
        self._surfaced_sig: str | None = None
        # True once we have Esc-dismissed the current AskUserQuestion popup, so
        # the dismissal fires once per appearance; reset when the pane leaves
        # the question screen.
        self._question_dismissed: bool = False

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
                if (
                    ev.kind == EventKind.TOOL_CALL
                    and ev.metadata.get("tool_name") == "AskUserQuestion"
                    and ev.status == SessionStatus.WAITING_INPUT
                ):
                    tool_use_id = str(ev.metadata.get("tool_use_id") or "")
                    self._plugin._pending_questions[self._session_id] = (
                        PendingTtyQuestion(
                            approval_id=uuid.uuid4().hex,
                            tool_use_id=tool_use_id,
                        )
                    )
                await self._runtime._emit_adapter_event(
                    self._session_id,
                    ev.kind,
                    ev.text,
                    ev.metadata,
                    ev.status,
                )
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
        sig = (snapshot.used_tokens, snapshot.context_window_tokens)
        if sig == self._context_usage_signature:
            return
        self._context_usage_signature = sig
        await self._runtime.update_session_fields(
            self._session_id, context_usage=snapshot
        )

    async def _poll_dialog(self) -> None:
        """Capture the live pane and surface any stable tool-permission dialog.

        Detection is intentionally mode-agnostic: ``claude_tty`` only changes
        permission mode by relaunching the pane, so the stored
        ``permission_mode`` holds whatever the last launch passed, while the
        TUI's real posture can drift independently (a human pressing shift+tab
        in the pane, or choosing "allow all this session"). Gating on the
        stored mode would miss a dialog that appears after an auto→prompting
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

        if screen_type is pane_dialog.PaneScreen.TRUST:
            # A fresh cwd opens with the workspace-trust prompt, which blocks the
            # session (including autonomously-spawned ones in fresh worktrees)
            # until answered. Option 1 ("trust") is preselected, so a bare Enter
            # accepts. Re-sent each tick it persists — idempotent, and self-heals
            # if a keystroke is dropped; a stray Enter at the ready prompt after
            # it clears is a harmless empty submit.
            log.info(
                "accepting workspace-trust prompt",
                extra={"session_id": self._session_id},
            )
            await self._runtime.tmux.send_input(pane, "", submit=True)
            return

        if screen_type is pane_dialog.PaneScreen.QUESTION:
            # The AskUserQuestion popup withholds its structured questions from
            # the transcript until it is resolved, so it is invisible to the
            # tailer while it blocks the turn. Esc dismisses it, which flushes
            # the full tool_use record to the JSONL; the armed normalizer then
            # surfaces it as an answerable card (and swallows the resulting
            # "user rejected" result). The answer is delivered later as a normal
            # user turn via the plugin's answer_question.
            if self._prev_dialog_sig == "question":
                self._dialog_stable_count += 1
            else:
                self._prev_dialog_sig = "question"
                self._dialog_stable_count = 1
            if (
                self._dialog_stable_count >= _DIALOG_STABLE_TICKS
                and not self._question_dismissed
            ):
                log.info(
                    "dismissing AskUserQuestion popup to surface it",
                    extra={"session_id": self._session_id},
                )
                await self._runtime.tmux.send_bytes(pane, b"\x1b")
                self._normalizer.arm_question_dismissal()
                self._question_dismissed = True
            return

        if screen_type is pane_dialog.PaneScreen.PLAN:
            await self._surface_plan_dialog(snapshot)
            return

        if screen_type is not pane_dialog.PaneScreen.APPROVAL:
            # Dialog gone — clear any pending approval for this session.
            self._plugin._pending_approvals.pop(self._session_id, None)
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            self._surfaced_sig = None
            self._question_dismissed = False
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

    async def _surface_plan_dialog(self, snapshot: str) -> None:
        """Surface the ExitPlanMode dialog as the same approval card Chat shows.

        The dialog is the plan-mode analogue of a tool-permission prompt: the
        binary withholds the ExitPlanMode tool_use from the transcript while the
        dialog blocks, so it is read off the pane. The plan body comes from the
        plan-file Write the normalizer already captured, matching the Chat card's
        ``tool_input.plan``. Approve presses the manual-approve option; decline
        falls through to Esc (``decline_number=None``), which keeps plan mode.
        """
        dialog = pane_dialog.parse_plan_dialog(snapshot)
        if dialog is None or dialog.approve_option is None:
            self._prev_dialog_sig = None
            self._dialog_stable_count = 0
            return

        sig = f"ExitPlanMode:{dialog.plan_path}"
        if sig == self._prev_dialog_sig:
            self._dialog_stable_count += 1
        else:
            self._prev_dialog_sig = sig
            self._dialog_stable_count = 1
        if self._dialog_stable_count < _DIALOG_STABLE_TICKS:
            return
        if sig == self._surfaced_sig:
            return

        plan_path = self._normalizer.last_plan_path or dialog.plan_path
        tool_input: dict[str, Any] = {"plan": self._normalizer.last_plan_content or ""}
        if plan_path:
            tool_input["planFilePath"] = plan_path

        approval_id = str(uuid.uuid4())
        self._plugin._pending_approvals[self._session_id] = PendingTtyApproval(
            approval_id=approval_id,
            tool_name="ExitPlanMode",
            target=plan_path,
            approve_number=dialog.approve_option.number,
            decline_number=None,
            signature=sig,
            is_plan=True,
        )
        self._surfaced_sig = sig

        payload = {"tool_name": "ExitPlanMode", "tool_input": tool_input}
        await self._runtime._emit_adapter_event(
            self._session_id,
            EventKind.APPROVAL_REQUEST,
            format_approval_text(payload),
            {
                "tool_name": "ExitPlanMode",
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
