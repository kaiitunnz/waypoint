from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.attachments import ResolvedAttachment
from waypoint.backends.opencode.adapter import OpenCodeError
from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.backends.opencode.plugin import OpenCodePlugin
    from waypoint.runtime import SessionRuntime


class OpenCodeTransport(TransportAdapter):
    is_structured = True
    supports_resume = False

    def __init__(self, runtime: "SessionRuntime", plugin: "OpenCodePlugin") -> None:
        self._runtime = runtime
        self._plugin = plugin

    async def send_input(
        self,
        session: SessionRecord,
        text: str,
        attachments: list[ResolvedAttachment] | None = None,
    ) -> None:
        adapter = await self._plugin.session_adapter(self._runtime, session)
        await adapter.send_input(session.id, text, attachments)

    async def interrupt(self, session: SessionRecord) -> None:
        adapter = await self._plugin.session_adapter(self._runtime, session)
        await adapter.interrupt(session.id)

    async def terminate(self, session: SessionRecord) -> None:
        # Soft path: runtime.terminate dispatches via plugin.terminate_session
        # so this method is effectively unreachable from the API. Keep it
        # tolerant of a missing adapter for any direct callers (tests,
        # internal cleanup) so a stale opencode session can always be
        # disposed of without first spinning up an SSH server.
        await self._plugin.terminate_session(self._runtime, session)

    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
        approval_id: str | None = None,
    ) -> bool:
        adapter = await self._plugin.session_adapter(self._runtime, session)
        try:
            return await adapter.respond_to_permission(
                session.id, decision, text, approval_id
            )
        except OpenCodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    def has_pending_approval(self, session: SessionRecord) -> bool:
        # Pure introspection — must not provoke an SSH server start on a
        # session whose adapter happens to be missing (e.g. cross-backend
        # poll). Treat "no adapter" as "no pending approval".
        adapter = self._plugin.existing_session_adapter(self._runtime, session)
        return adapter is not None and adapter.has_pending_approval(session.id)

    def pending_approval_ids(self, session: SessionRecord) -> tuple[str, ...]:
        adapter = self._plugin.existing_session_adapter(self._runtime, session)
        return adapter.pending_approval_ids(session.id) if adapter else ()
