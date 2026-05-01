from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

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
        return session.tmux_pane or session.tmux_session or session.id

    async def send_input(self, session: SessionRecord, text: str) -> None:
        try:
            await self.adapter.send_input(self._target(session), text, True)
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
        if session.source == SessionSource.MANAGED and session.tmux_session:
            with suppress(TmuxError):
                await self.adapter.kill_session(session.tmux_session)
        monitor = self._runtime.monitor_tasks.pop(session.id, None)
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await monitor

    async def respond_to_approval(
        self, session: SessionRecord, decision: str, text: str | None
    ) -> bool:
        normalized = decision.strip().lower()
        mapped = "y" if normalized in {"approve", "yes", "y"} else "n"
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
        snapshot = raw_log_path.read_text(encoding="utf-8", errors="ignore")
        return self._runtime.normalizer.clean(snapshot)
