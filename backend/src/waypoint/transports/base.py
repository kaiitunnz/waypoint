from __future__ import annotations

from abc import ABC, abstractmethod

from fastapi import HTTPException, status

from waypoint.schemas import SessionRecord


class TransportAdapter(ABC):
    """Routes runtime operations to the underlying session backend.

    Each session transport (codex app-server, claude CLI, tmux) historically
    required its own branch inside ``SessionRuntime``. ``TransportAdapter``
    formalises that interface so the runtime dispatches polymorphically.
    """

    is_structured: bool = False
    supports_resume: bool = False

    @abstractmethod
    async def send_input(self, session: SessionRecord, text: str) -> None: ...

    @abstractmethod
    async def interrupt(self, session: SessionRecord) -> None: ...

    @abstractmethod
    async def terminate(self, session: SessionRecord) -> None: ...

    @abstractmethod
    async def respond_to_approval(
        self,
        session: SessionRecord,
        decision: str,
        text: str | None,
        approval_id: str | None = None,
    ) -> bool: ...

    @abstractmethod
    def has_pending_approval(self, session: SessionRecord) -> bool: ...

    @abstractmethod
    def terminal_snapshot(self, session: SessionRecord) -> str: ...

    async def resume(self, session: SessionRecord) -> None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"resume is not supported for {session.transport} sessions",
        )
