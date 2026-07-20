"""Transport adapter for the claude_tty (Emulated) transport.

Thin subclass of ``TmuxTransport`` that overrides:

- ``is_structured = True`` — the transcript tailer emits canonical events, so
  the frontend must not fall back to the heuristic raw-terminal view.
- ``interrupt`` sends ``Esc`` instead of ``Ctrl-C``; ``Esc`` is the Claude TUI's
  cancel key and cancels the running generation without terminating the process.
- ``has_pending_approval`` / ``respond_to_approval`` — drive tool-permission
  dialogs by sending the appropriate digit + Enter keystroke to the TUI pane.
  Pending state is owned by the plugin singleton (``ClaudeTtyPlugin``).
"""

import asyncio
from typing import TYPE_CHECKING

from waypoint.backends.approvals import is_approve_decision
from waypoint.backends.claude_tty import pane_dialog
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.backends.tmux.transport import TmuxTransport
from waypoint.schemas import SessionRecord

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime

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


class ClaudeTtyTransport(TmuxTransport):
    is_structured = True
    supports_resume = True

    def __init__(self, runtime: "SessionRuntime", plugin: "ClaudeTtyPlugin") -> None:
        super().__init__(runtime)
        self._plugin = plugin

    async def interrupt(self, session: SessionRecord) -> None:
        # Esc cancels whatever the pane is showing — including an open
        # permission dialog, which it declines. Drop any pending approval now
        # so ``has_pending_approval`` goes false immediately instead of lingering
        # until the next dialog poll, where a racing ``respond_to_approval``
        # would fire a stray digit at the ready prompt. A pending question is
        # already dismissed on the pane (we Esc it when surfacing), so just drop
        # the entry so a later answer is rejected rather than misrouted.
        pending = self._plugin._pending_approvals.pop(session.id, None)
        self._plugin._pending_questions.pop(session.id, None)
        target = self._target(session)
        await self.adapter.send_bytes(target, b"\x1b")
        # The chat approval card is dequeued only by a resolution note; emit one
        # so interrupt clears it promptly, before the dismissal retries below.
        if pending is not None:
            await self._runtime._record_system_event(
                session.id,
                "Pending approval cleared by interrupt",
                metadata={
                    "method": "approval.invalidated",
                    "approval_id": pending.approval_id,
                },
            )
        # Nothing else retries a stranded prompt: the tailer's surfaced-signature
        # guard suppresses a re-emit and the pending entry is already popped.
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

    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
        approval_id: str | None = None,
    ) -> bool:
        pending = self._plugin._pending_approvals.get(session.id)
        if pending is None:
            return False
        if approval_id is not None and pending.approval_id != approval_id:
            return False

        # Claim the approval before the await below, so a concurrent call
        # (double-click, retried POST) short-circuits on the None lookup above
        # rather than sending a second keystroke and double-deleting the key.
        self._plugin._pending_approvals.pop(session.id, None)

        target = self._target(session)

        if is_approve_decision(decision):
            await self.adapter.send_input(
                target, str(pending.approve_number), submit=True
            )
            if pending.is_plan:
                # Approving exits plan mode in the TUI; the pressed option already
                # lands the pane in ``restore_mode`` (the pre-plan mode the dialog
                # can express, else default). Mirror it into the stored mode so the
                # badge tracks the binary and a later restart does not relaunch
                # back into plan mode.
                await self._runtime.update_session_fields(
                    session.id, permission_mode=pending.restore_mode or "default"
                )
        else:
            # Decline: send the No-labelled option's digit, never position 2
            # ("allow all this session"). Fall back to Esc if no explicit No.
            if pending.decline_number is not None:
                await self.adapter.send_input(
                    target, str(pending.decline_number), submit=True
                )
            else:
                await self.adapter.send_bytes(target, b"\x1b")

        return True
