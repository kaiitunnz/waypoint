"""Focus mode: a focused session holds agent sends, scheduled firings, and
board/inbox wakes until the human releases or cancels them."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.schemas import (
    HeldMessageOrigin,
    HeldMessageRecord,
    SessionEnvelope,
    SessionInputRequest,
    SessionRecord,
)

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime


class FocusGate:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        # Claimed held ids mid-delivery, keyed to their session. A cancel that
        # lands on one stops a failed delivery from putting it back.
        self._in_flight: dict[str, str] = {}
        self._cancelled: set[str] = set()

    def list(self, session_id: str) -> list[HeldMessageRecord]:
        self._runtime.get_session(session_id)
        return self._runtime.storage.list_held_messages(session_id)

    async def deliver(
        self,
        session_id: str,
        request: SessionInputRequest,
        origin: HeldMessageOrigin,
        schedule_id: str | None = None,
    ) -> SessionRecord | HeldMessageRecord:
        session = self._runtime.get_session(session_id)
        if not session.focus:
            return await self._runtime.handle_input(session_id, request)
        self._runtime.resolve_attachments(session_id, request.attachments)
        record = HeldMessageRecord(
            id=uuid.uuid4().hex,
            session_id=session_id,
            origin=origin,
            sender_session_id=request.sender_session_id,
            schedule_id=schedule_id,
            text=request.text,
            submit=request.submit,
            command=request.command,
            items=request.items,
            attachments=list(request.attachments or []),
            created_at=datetime.now(UTC),
        )
        self._pin(record)
        if self._runtime.storage.create_held_message(record):
            await self._publish(session_id)
        else:
            self._unpin(record)
        return record

    async def release(self, held_id: str) -> SessionRecord:
        record = self._runtime.storage.take_held_message(held_id)
        if record is None:
            raise _not_found()
        return await self._deliver_claimed(record)

    async def release_all(self, session_id: str) -> SessionRecord:
        for held in self.list(session_id):
            record = self._runtime.storage.take_held_message(held.id)
            if record is not None:
                await self._deliver_claimed(record)
        return self._runtime.get_session(session_id)

    async def cancel(self, held_id: str) -> None:
        if held_id in self._in_flight:
            self._cancelled.add(held_id)
            return
        record = self._runtime.storage.take_held_message(held_id)
        if record is None:
            raise _not_found()
        self._unpin(record)
        await self._publish(record.session_id)

    async def cancel_all(self, session_id: str) -> None:
        self._runtime.get_session(session_id)
        self._cancelled.update(
            held_id for held_id, owner in self._in_flight.items() if owner == session_id
        )
        for record in self._runtime.storage.take_held_messages(session_id):
            self._unpin(record)
        await self._publish(session_id)

    async def _deliver_claimed(self, record: HeldMessageRecord) -> SessionRecord:
        self._in_flight[record.id] = record.session_id
        request = SessionInputRequest(
            text=record.text,
            submit=record.submit,
            command=record.command,
            items=record.items,
            attachments=record.attachments or None,
        )
        try:
            try:
                prepared = await self._runtime.prepare_input(record.session_id, request)
            except Exception:
                # Nothing reached the transcript: put it back unless it was
                # cancelled meanwhile (a deleted session refuses the insert).
                if record.id in self._cancelled:
                    self._unpin(record)
                elif not self._runtime.storage.create_held_message(record):
                    self._unpin(record)
                raise
            try:
                return await self._runtime.dispatch_input(prepared)
            finally:
                self._unpin(record)
        finally:
            self._in_flight.pop(record.id, None)
            self._cancelled.discard(record.id)
            await self._publish(record.session_id)

    def _pin(self, record: HeldMessageRecord) -> None:
        if record.attachments:
            self._runtime.attachments.mark_held_references(
                record.session_id, record.id, record.attachments
            )

    def _unpin(self, record: HeldMessageRecord) -> None:
        if record.attachments:
            self._runtime.attachments.release_held_references(
                record.session_id, record.id, record.attachments
            )

    async def _publish(self, session_id: str) -> None:
        held = self._runtime.storage.list_held_messages(session_id)
        await self._runtime.broadcast.publish(
            held_messages_envelope(session_id, held), session_id=session_id
        )


def held_messages_envelope(
    session_id: str, held: list[HeldMessageRecord]
) -> SessionEnvelope:
    return SessionEnvelope(
        type="held_messages",
        payload={
            "session_id": session_id,
            "held_messages": [record.model_dump(mode="json") for record in held],
        },
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="held message not found"
    )
