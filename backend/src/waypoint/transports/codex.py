from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class CodexTransport(TransportAdapter):
    is_structured = True
    supports_resume = False

    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    @property
    def adapter(self):  # late binding so tests can swap runtime.codex
        return self._runtime.codex

    async def send_input(self, session: SessionRecord, text: str) -> None:
        try:
            await self.adapter.send_input(session.id, text)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def interrupt(self, session: SessionRecord) -> None:
        await self.adapter.interrupt(session.id)

    async def terminate(self, session: SessionRecord) -> None:
        await self.adapter.terminate_session(session.id)

    async def respond_to_approval(
        self, session: SessionRecord, decision: str, text: str | None
    ) -> bool:
        return await self.adapter.respond_to_approval(session.id, decision)

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return self.adapter.has_pending_approval(session.id)

    def terminal_snapshot(self, session: SessionRecord) -> str:
        return self.adapter.terminal_snapshot(session.id)
