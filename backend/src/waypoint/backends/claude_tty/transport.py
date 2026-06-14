"""Transport adapter for the claude_tty backend.

Thin subclass of ``TmuxTransport`` that overrides two properties:

- ``is_structured = True`` — the transcript tailer emits canonical events, so
  the frontend must not fall back to the heuristic raw-terminal view.
- ``interrupt`` sends ``Esc`` instead of ``Ctrl-C``; ``Esc`` is the Claude TUI's
  cancel key and cancels the running generation without terminating the process.
"""

from typing import TYPE_CHECKING

from waypoint.backends.tmux.transport import TmuxTransport
from waypoint.schemas import SessionRecord

if TYPE_CHECKING:
    pass


class ClaudeTtyTransport(TmuxTransport):
    is_structured = True
    supports_resume = True

    async def interrupt(self, session: SessionRecord) -> None:
        await self.adapter.send_bytes(self._target(session), b"\x1b")
