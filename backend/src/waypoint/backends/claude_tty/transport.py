"""Transport adapter for the claude_tty (Emulated) transport.

Thin subclass of ``TmuxTransport`` that overrides:

- ``is_structured = True`` — the transcript tailer emits canonical events, so
  the frontend must not fall back to the heuristic raw-terminal view.
- ``interrupt`` sends ``Esc`` instead of ``Ctrl-C``; ``Esc`` is the Claude TUI's
  cancel key and cancels the running generation without terminating the process.
- ``send_input`` moves the session onto the conversation a ``/clear`` starts.
- ``send_input`` and ``resume`` send keystrokes only with the composer drawn,
  since anything that replaces it, recognized or not, would take them.
- ``has_pending_approval`` / ``respond_to_approval`` — drive tool-permission
  dialogs by pressing the option's digit in the TUI pane, after checking the
  pane still shows what the ask was raised for. Pending state is owned by the
  plugin singleton (``ClaudeTtyPlugin``).
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from waypoint.attachments import ResolvedAttachment
from waypoint.backends.approvals import is_approve_decision
from waypoint.backends.base import PaneSubmitConfirming
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.claude_tty._state import PendingTtyApproval
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.backends.tmux.transport import TmuxTransport
from waypoint.schemas import SessionRecord, SessionStatus
from waypoint.transports import InputBlockedError

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.backends.claude_tty")

# Dialogs where Esc cancels the turn. Esc means something else on the
# trust/model/effort popups, so the retry loop must not target those.
_INTERRUPTIBLE_DIALOGS = frozenset(
    {
        pane_dialog.PaneScreen.APPROVAL,
        pane_dialog.PaneScreen.PLAN,
        pane_dialog.PaneScreen.QUESTION,
    }
)
# A single Esc can lose the TUI's escape-disambiguation race, so confirm the
# dialog left the pane and re-send it a bounded number of times.
_INTERRUPT_ESC_RETRIES = 4
_INTERRUPT_ESC_POLL_SECONDS = 0.3
_STALE_REPLY_NOTE = "Reply not sent: the terminal no longer shows this request"
# Polls for the composer before a send gives up, long enough for a slow remote
# resume to draw it.
_COMPOSER_WAIT_ATTEMPTS = 100
_CLEAR_COMMAND = "/clear"
_CLEAR_UNFOLLOWED_NOTE = (
    "Couldn't follow the new conversation started by /clear; "
    "replies after it may not appear here."
)
_TURN_IN_FLIGHT = frozenset({SessionStatus.RUNNING, SessionStatus.WAITING_INPUT})


class ClaudeTtyTransport(TmuxTransport):
    is_structured = True
    supports_resume = True

    def __init__(self, runtime: "SessionRuntime", plugin: "ClaudeTtyPlugin") -> None:
        super().__init__(runtime)
        self._plugin = plugin
        # Sessions whose unreadable pane has been logged.
        self._unreadable: set[str] = set()

    async def send_input(
        self,
        session: SessionRecord,
        text: str,
        attachments: list[ResolvedAttachment] | None = None,
    ) -> None:
        await super().send_input(session, text, attachments)
        if text.split(maxsplit=1)[:1] != [_CLEAR_COMMAND]:
            return
        if await self._plugin.follow_clear(self._runtime, session.id):
            return
        # The clear's completion record is in the unfollowed transcript, so
        # resolve the turn here, unless a turn was in flight with the /clear
        # queued behind it.
        await self._runtime._record_system_event(
            session.id,
            _CLEAR_UNFOLLOWED_NOTE,
            status=None if session.status in _TURN_IN_FLIGHT else SessionStatus.IDLE,
        )

    async def _await_pane_ready(
        self,
        target: str,
        confirmer: PaneSubmitConfirming,
        *,
        attempts: int = _COMPOSER_WAIT_ATTEMPTS,
        poll_seconds: float = 0.3,
    ) -> None:
        # Unlike the base, never give up and send anyway: whatever replaced the
        # composer would take the keystrokes, recognized as a dialog or not.
        for _ in range(attempts):
            snapshot = await self.adapter.capture_snapshot(target)
            if confirmer.pane_shows_blocking_dialog(snapshot):
                raise InputBlockedError()
            if pane_dialog.composer_drawn(snapshot):
                return
            await asyncio.sleep(poll_seconds)
        raise InputBlockedError()

    async def _submit_confirmed(
        self,
        target: str,
        confirmer: PaneSubmitConfirming,
        sent_text: str,
        *,
        attempts: int = 30,
        poll_seconds: float = 0.4,
    ) -> None:
        # As the base, but each Enter also waits for the composer: a popup nothing
        # recognizes would take it as its answer.
        for _ in range(attempts):
            snapshot = await self.adapter.capture_snapshot(target)
            if confirmer.pane_shows_blocking_dialog(snapshot):
                return
            if not pane_dialog.composer_drawn(snapshot):
                await asyncio.sleep(poll_seconds)
                continue
            await self.adapter.submit(target)
            await asyncio.sleep(poll_seconds)
            if confirmer.confirm_pane_submit(
                await self.adapter.capture_snapshot(target), sent_text
            ):
                return

    async def _require_composer(self, target: str) -> None:
        if not pane_dialog.composer_drawn(await self.adapter.capture_snapshot(target)):
            raise InputBlockedError()

    async def resume(self, session: SessionRecord) -> bool:
        await self._require_composer(self._target(session))
        return await super().resume(session)

    async def interrupt(self, session: SessionRecord) -> None:
        # Esc cancels whatever the pane is showing — including an open
        # permission dialog, which it declines. Drop any pending approval now
        # so ``has_pending_approval`` goes false immediately instead of lingering
        # until the next dialog poll, where a racing ``respond_to_approval``
        # would fire a stray digit at the ready prompt. Open questions stay
        # answerable: their popup was already dismissed when surfaced.
        await self._plugin.drop_pending_approval(
            self._runtime, session.id, "Pending approval cleared by interrupt"
        )
        target = self._target(session)
        await self.adapter.send_bytes(target, b"\x1b")
        # Retried here: the tailer takes several polls to raise a dialog that
        # outlived its card.
        await self._ensure_dialog_dismissed(target)

    async def _ensure_dialog_dismissed(self, target: str) -> None:
        for _ in range(_INTERRUPT_ESC_RETRIES):
            await asyncio.sleep(_INTERRUPT_ESC_POLL_SECONDS)
            try:
                snapshot = await self.adapter.capture_snapshot(target)
            except TmuxError:
                return
            if pane_dialog.classify(snapshot) not in _INTERRUPTIBLE_DIALOGS:
                return
            await self.adapter.send_bytes(target, b"\x1b")

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return session.id in self._plugin._pending_approvals

    def pending_approval_ids(self, session: SessionRecord) -> tuple[str, ...]:
        pending = self._plugin._pending_approvals.get(session.id)
        return (pending.approval_id,) if pending is not None else ()

    async def input_blocked(self, session: SessionRecord) -> bool:
        return self.has_pending_approval(session) or await super().input_blocked(
            session
        )

    async def ready_for_input(self, session: SessionRecord) -> bool:
        try:
            snapshot = await self.adapter.capture_snapshot(self._target(session))
        except TmuxError as exc:
            if session.id not in self._unreadable:
                self._unreadable.add(session.id)
                log.warning("cannot read the pane of %s: %s", session.id, exc)
            return False
        self._unreadable.discard(session.id)
        return pane_dialog.composer_drawn(snapshot)

    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
        approval_id: str | None = None,
    ) -> bool:
        pending = self._plugin._pending_approvals.get(session.id)
        if pending is None or pending.replying:
            return False
        if approval_id is not None and pending.approval_id != approval_id:
            return False

        # Claim the approval before the awaits below, so a concurrent call
        # (double-click, retried POST) short-circuits above rather than sending
        # a second keystroke.
        pending.replying = True
        approve = is_approve_decision(decision)
        try:
            async with self._input_lock(session):
                # A key pressed on a dialog that has moved on would answer a
                # request the human never saw.
                if not await self._plugin.still_shows(session.id, pending):
                    await self._refuse_reply(session)
                    return False
                if self._plugin._pending_approvals.get(session.id) is not pending:
                    return False  # interrupted meanwhile
                await self._press(session, pending, approve)
        except Exception:
            # Nothing reached the dialog: keep it answerable.
            self._runtime.asks.mark_dirty(session.id)
            raise
        finally:
            pending.replying = False
        if self._plugin._pending_approvals.get(session.id) is pending:
            del self._plugin._pending_approvals[session.id]
        if approve and pending.is_plan:
            # Approving exits plan mode in the TUI; the pressed option already
            # lands the pane in ``restore_mode`` (the pre-plan mode the dialog
            # can express, else default). Mirror it into the stored mode so the
            # badge tracks the binary and a later restart does not relaunch
            # back into plan mode.
            await self._runtime.update_session_fields(
                session.id, permission_mode=pending.restore_mode or "default"
            )
        return True

    async def _refuse_reply(self, session: SessionRecord) -> None:
        # Forgotten before the entry goes, so the next poll raises what is on
        # screen as a new request.
        self._plugin.forget_dialog(session.id)
        await self._plugin.drop_pending_approval(
            self._runtime, session.id, _STALE_REPLY_NOTE
        )

    async def _press(
        self, session: SessionRecord, pending: PendingTtyApproval, approve: bool
    ) -> None:
        target = self._target(session)
        # The digit alone confirms. An Enter after it would approve whatever
        # dialog the TUI had drawn by then, its "Yes" highlighted.
        if approve:
            await self.adapter.send_input(
                target, str(pending.approve_number), submit=False
            )
        elif pending.decline_number is not None:
            # Decline: send the No-labelled option's digit, never position 2
            # ("allow all this session"). Fall back to Esc if no explicit No.
            await self.adapter.send_input(
                target, str(pending.decline_number), submit=False
            )
        else:
            await self.adapter.send_bytes(target, b"\x1b")
