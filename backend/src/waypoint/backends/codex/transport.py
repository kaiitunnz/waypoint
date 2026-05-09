from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.backends.codex.adapter import CodexAppServerAdapter
    from waypoint.backends.codex.plugin import CodexPlugin
    from waypoint.runtime import SessionRuntime


class CodexTransport(TransportAdapter):
    is_structured = True
    supports_resume = False

    def __init__(self, runtime: SessionRuntime, plugin: CodexPlugin) -> None:
        self._runtime = runtime
        self._plugin = plugin

    @property
    def adapter(self) -> CodexAppServerAdapter:
        # Late binding so tests can swap ``plugin.adapter`` after setup;
        # the codex plugin always holds a real adapter post-setup so the
        # cast is safe in the live-session path.
        adapter = self._plugin.adapter
        if adapter is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="codex adapter is not initialized",
            )
        return adapter

    async def send_input(self, session: SessionRecord, text: str) -> None:
        try:
            await self.adapter.send_input(
                session.id,
                text,
                turn_params=self._plugin.turn_params_for(session),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    async def interrupt(self, session: SessionRecord) -> None:
        await self.adapter.interrupt(session.id)

    async def terminate(self, session: SessionRecord) -> None:
        await self.adapter.terminate_session(session.id)

    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
        approval_id: str | None = None,
    ) -> bool:
        # Note: Codex does not support multiple concurrent approvals so approval_id is ignored
        return await self.adapter.respond_to_approval(session.id, decision, text)

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return self.adapter.has_pending_approval(session.id)

    def terminal_snapshot(self, session: SessionRecord) -> str:
        return self.adapter.terminal_snapshot(session.id)
