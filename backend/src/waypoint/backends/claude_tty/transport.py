"""Transport adapter for the claude_tty backend.

Thin subclass of ``TmuxTransport`` that overrides:

- ``is_structured = True`` — the transcript tailer emits canonical events, so
  the frontend must not fall back to the heuristic raw-terminal view.
- ``interrupt`` sends ``Esc`` instead of ``Ctrl-C``; ``Esc`` is the Claude TUI's
  cancel key and cancels the running generation without terminating the process.
- ``has_pending_approval`` / ``respond_to_approval`` — drive tool-permission
  dialogs by sending the appropriate digit + Enter keystroke to the TUI pane.
  Pending state is owned by the plugin singleton (``ClaudeTtyPlugin``).
"""

from typing import TYPE_CHECKING

from waypoint.backends.tmux.transport import TmuxTransport
from waypoint.schemas import SessionRecord

if TYPE_CHECKING:
    from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
    from waypoint.runtime import SessionRuntime


class ClaudeTtyTransport(TmuxTransport):
    is_structured = True
    supports_resume = True

    def __init__(self, runtime: "SessionRuntime", plugin: "ClaudeTtyPlugin") -> None:
        super().__init__(runtime)
        self._plugin = plugin

    async def interrupt(self, session: SessionRecord) -> None:
        await self.adapter.send_bytes(self._target(session), b"\x1b")

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

        target = self._target(session)
        normalized = decision.strip().lower()

        if normalized in {"approve", "yes", "y"}:
            await self.adapter.send_input(
                target, str(pending.approve_number), submit=True
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

        del self._plugin._pending_approvals[session.id]
        return True
