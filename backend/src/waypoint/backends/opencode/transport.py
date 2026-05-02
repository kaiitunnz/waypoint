from typing import TYPE_CHECKING

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

    async def send_input(self, session: SessionRecord, text: str) -> None:
        adapter = self._plugin._require_adapter(
            self._runtime, session.launch_target_id, session.cwd
        )
        await adapter.send_input(session.id, text)

    async def interrupt(self, session: SessionRecord) -> None:
        adapter = self._plugin._require_adapter(
            self._runtime, session.launch_target_id, session.cwd
        )
        await adapter.interrupt(session.id)

    async def terminate(self, session: SessionRecord) -> None:
        adapter = self._plugin._require_adapter(
            self._runtime, session.launch_target_id, session.cwd
        )
        await adapter.terminate_session(session.id)

    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
    ) -> bool:
        adapter = self._plugin._require_adapter(
            self._runtime, session.launch_target_id, session.cwd
        )
        return await adapter.respond_to_permission(session.id, decision)

    def has_pending_approval(self, session: SessionRecord) -> bool:
        adapter = self._plugin._require_adapter(
            self._runtime, session.launch_target_id, session.cwd
        )
        return adapter.has_pending_approval(session.id)

    def terminal_snapshot(self, session: SessionRecord) -> str:
        adapter = self._plugin._require_adapter(
            self._runtime, session.launch_target_id, session.cwd
        )
        return adapter.terminal_snapshot(session.id)
