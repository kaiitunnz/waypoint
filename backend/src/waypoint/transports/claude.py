from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.claude_cli import ClaudeCliError
from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class ClaudeTransport(TransportAdapter):
    is_structured = True
    supports_resume = False

    def __init__(self, runtime: SessionRuntime) -> None:
        self._runtime = runtime

    @property
    def adapter(self):
        return self._runtime.claude

    def _require_adapter(self):
        if self.adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="claude adapter is not initialized",
            )
        return self.adapter

    async def send_input(self, session: SessionRecord, text: str) -> None:
        adapter = self._require_adapter()
        try:
            await adapter.send_input(session.id, text)
        except ClaudeCliError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def interrupt(self, session: SessionRecord) -> None:
        adapter = self._require_adapter()
        await adapter.interrupt(session.id)

    async def terminate(self, session: SessionRecord) -> None:
        adapter = self._require_adapter()
        await adapter.terminate_session(session.id)

    async def respond_to_approval(
        self, session: SessionRecord, decision: str, text: str | None
    ) -> bool:
        adapter = self._require_adapter()
        return await adapter.respond_to_approval(session.id, decision)

    def has_pending_approval(self, session: SessionRecord) -> bool:
        if self.adapter is None:
            return False
        return self.adapter.has_pending_approval(session.id)

    def terminal_snapshot(self, session: SessionRecord) -> str:
        if self.adapter is None:
            return ""
        return self.adapter.terminal_snapshot(session.id)
