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
        # Claimed held id -> session id. A cancel before dispatch stops the
        # delivery; one during dispatch is a 409.
        self._in_flight: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._dispatching: set[str] = set()

    async def deliver(
        self,
        session_id: str,
        request: SessionInputRequest,
        origin: HeldMessageOrigin,
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
            sender_title=self._title_of(request.sender_session_id),
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
        await self._deliver_claimed(record)
        return self._runtime.get_session(record.session_id)

    async def release_all(self, session_id: str) -> SessionRecord:
        self._runtime.get_session(session_id)
        for held in self._runtime.storage.list_held_messages(session_id):
            record = self._runtime.storage.take_held_message(held.id)
            if record is not None:
                await self._deliver_claimed(record)
        return self._runtime.get_session(session_id)

    async def cancel(self, held_id: str) -> None:
        if held_id in self._dispatching:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="held message is already being delivered",
            )
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
            held_id
            for held_id, owner in self._in_flight.items()
            if owner == session_id and held_id not in self._dispatching
        )
        for record in self._runtime.storage.take_held_messages(session_id):
            self._unpin(record)
        await self._publish(session_id)

    async def _deliver_claimed(self, record: HeldMessageRecord) -> None:
        self._in_flight[record.id] = record.session_id
        request = SessionInputRequest(
            text=record.text,
            submit=record.submit,
            command=record.command,
            items=record.items,
            attachments=record.attachments or None,
        )
        try:
            await self._publish(record.session_id)
            try:
                prepared = await self._runtime.prepare_input(record.session_id, request)
            except Exception:
                # Nothing reached the transcript: put it back unless it was
                # cancelled meanwhile (a deleted session refuses the insert).
                restored = (
                    record.id not in self._cancelled
                    and self._runtime.storage.create_held_message(record)
                )
                if not restored:
                    self._unpin(record)
                raise
            if record.id in self._cancelled:
                self._unpin(record)
                return
            self._dispatching.add(record.id)
            try:
                await self._runtime.dispatch_input(prepared)
            finally:
                self._unpin(record)
        finally:
            self._in_flight.pop(record.id, None)
            self._cancelled.discard(record.id)
            self._dispatching.discard(record.id)
            await self._publish(record.session_id)

    def _title_of(self, session_id: str | None) -> str | None:
        sender = self._runtime.storage.get_session(session_id) if session_id else None
        return sender.title if sender else None

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

    def envelope(self, session_id: str) -> SessionEnvelope:
        held = self._runtime.storage.list_held_messages(session_id)
        return SessionEnvelope(
            type="held_messages",
            payload={
                "session_id": session_id,
                "held_messages": [record.model_dump(mode="json") for record in held],
            },
        )

    async def _publish(self, session_id: str) -> None:
        await self._runtime.broadcast.publish(
            self.envelope(session_id), session_id=session_id
        )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="held message not found"
    )
