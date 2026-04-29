from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from waypoint.schemas import SessionRecord
from waypoint.transports.base import TransportAdapter

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


# Maps Waypoint's per-session mode string to the params Codex's TUI builds
# for the equivalent /permissions picker entry. See
# tmp/docs/BACKEND_CONTROL_PROTOCOLS.md for source-of-truth wiring.
CODEX_PERMISSION_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "approval_policy": "on-request",
        "sandbox_policy": {"type": "workspaceWrite"},
        "approvals_reviewer": "user",
    },
    "auto_review": {
        "approval_policy": "on-request",
        "sandbox_policy": {"type": "workspaceWrite"},
        "approvals_reviewer": "guardian_subagent",
    },
    "full_access": {
        "approval_policy": "never",
        "sandbox_policy": {"type": "dangerFullAccess"},
        "approvals_reviewer": "user",
    },
}


def codex_turn_params_for(mode: str | None) -> dict[str, Any] | None:
    if mode is None:
        return None
    preset = CODEX_PERMISSION_PRESETS.get(mode)
    if preset is None:
        return None
    return dict(preset)


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
            await self.adapter.send_input(
                session.id,
                text,
                turn_params=codex_turn_params_for(session.permission_mode),
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
        self, session: SessionRecord, decision: str, text: str | None
    ) -> bool:
        return await self.adapter.respond_to_approval(session.id, decision)

    def has_pending_approval(self, session: SessionRecord) -> bool:
        return self.adapter.has_pending_approval(session.id)

    def terminal_snapshot(self, session: SessionRecord) -> str:
        return self.adapter.terminal_snapshot(session.id)
