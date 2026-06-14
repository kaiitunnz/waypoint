from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.attachments import ResolvedAttachment, append_attachment_paths
from waypoint.backends.approvals import is_approve_decision
from waypoint.backends.tmux.adapter import TmuxError
from waypoint.schemas import (
    SessionInputRequest,
    SessionRecord,
    SessionSource,
)
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class TmuxTransport(TransportAdapter):
    is_structured = False
    supports_resume = True

    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    @property
    def adapter(self):
        return self._runtime.tmux

    @staticmethod
    def _target(session: SessionRecord) -> str:
        state = session.transport_state
        return state.get("tmux_pane") or state.get("tmux_session") or session.id

    async def send_input(
        self,
        session: SessionRecord,
        text: str,
        attachments: list[ResolvedAttachment] | None = None,
    ) -> None:
        # A raw terminal can't carry binary, so attachments degrade to their
        # host paths appended to the message; the inner CLI reads them itself.
        payload = append_attachment_paths(text, attachments or [])
        try:
            await self.adapter.send_input(self._target(session), payload, True)
        except TmuxError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def interrupt(self, session: SessionRecord) -> None:
        await self.adapter.interrupt(self._target(session))

    async def resume(self, session: SessionRecord) -> None:
        await self.adapter.resume(self._target(session))

    async def terminate(self, session: SessionRecord) -> None:
        target = self._target(session)
        with suppress(TmuxError):
            await self.adapter.stop_pipe(target)
        tmux_session = session.transport_state.get("tmux_session")
        if session.source == SessionSource.MANAGED and tmux_session:
            with suppress(TmuxError):
                await self.adapter.kill_session(tmux_session)
        monitor = self._runtime.monitor_tasks.pop(session.id, None)
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await monitor

    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
        approval_id: str | None = None,
    ) -> bool:
        mapped = "y" if is_approve_decision(decision) else "n"
        reply = text or mapped
        await self._runtime.handle_input(
            session.id, SessionInputRequest(text=reply, submit=True)
        )
        await self._runtime._record_system_event(
            session.id, f"Approval response sent: {mapped}"
        )
        return True

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return False

    def terminal_snapshot(self, session: SessionRecord) -> str:
        raw_log_path = Path(session.raw_log_path)
        if not raw_log_path.exists():
            return ""
        # Return the pipe-pane bytes verbatim — ANSI sequences included —
        # so the frontend's terminal emulator can render colors, cursor
        # moves, and alternate-screen redraws produced by the agent's TUI.
        return raw_log_path.read_text(encoding="utf-8", errors="ignore")
