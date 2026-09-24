"""Held delivery for agent sends, scheduled firings, and board/inbox wakes.

A focused session holds them until the human releases or cancels them. A
session waiting on a dialog holds them too, and delivers them on its own once
the dialog clears."""

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.schemas import (
    HeldMessageOrigin,
    HeldMessageRecord,
    SessionEnvelope,
    SessionInputRequest,
    SessionRecord,
    SessionStatus,
)
from waypoint.transports import InputBlockedError

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.focus")

# Re-check cadence while only the pane (no pending approval, which ends with a
# status edge) blocks delivery — e.g. a popup the tailer is about to dismiss.
DEFER_RETRY_SECONDS = 3.0
_UNDELIVERABLE = frozenset(
    {SessionStatus.EXITED, SessionStatus.ERROR, SessionStatus.STARTING}
)


class FocusGate:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        # Claimed held id -> session id. A cancel before dispatch stops the
        # delivery; one during dispatch is a 409.
        self._in_flight: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._dispatching: set[str] = set()
        # Serializes the hold-or-deliver decision so held items keep their order.
        self._send_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Sessions with auto-release items, and their drain bookkeeping.
        self._deferred: set[str] = set()
        self._draining: set[str] = set()
        self._rerun: set[str] = set()
        self._retries: dict[str, asyncio.TimerHandle] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        self._deferred = self._runtime.storage.auto_release_session_ids()

    async def deliver(
        self,
        session_id: str,
        request: SessionInputRequest,
        origin: HeldMessageOrigin,
    ) -> SessionRecord | HeldMessageRecord:
        session = self._runtime.get_session(session_id)
        if session.focus:
            return await self._hold(session_id, request, origin, auto_release=False)
        transport = self._runtime.transport_for(session)
        async with self._send_locks[session_id]:
            # Queue behind earlier auto-held items so delivery keeps its order.
            if session_id not in self._deferred and not await transport.input_blocked(
                session
            ):
                try:
                    return await self._runtime.handle_input(session_id, request)
                except InputBlockedError:
                    pass
            record = await self._hold(session_id, request, origin, auto_release=True)
        self._deferred.add(session_id)
        self.kick(session_id)
        return record

    async def _hold(
        self,
        session_id: str,
        request: SessionInputRequest,
        origin: HeldMessageOrigin,
        auto_release: bool,
    ) -> HeldMessageRecord:
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
            auto_release=auto_release,
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
        async with self._send_locks[record.session_id]:
            await self._deliver_claimed(record)
        return self._runtime.get_session(record.session_id)

    async def release_all(self, session_id: str) -> SessionRecord:
        self._runtime.get_session(session_id)
        async with self._send_locks[session_id]:
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

    def drain_deferred(self, session_ids: set[str]) -> None:
        """Deliver auto-release items for sessions that reached a new state."""
        for session_id in session_ids & self._deferred:
            self.kick(session_id)

    def kick(self, session_id: str) -> None:
        if session_id in self._draining:
            self._rerun.add(session_id)
            return
        self._draining.add(session_id)
        task = asyncio.create_task(self._drain(session_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _drain(self, session_id: str) -> None:
        try:
            while True:
                self._rerun.discard(session_id)
                await self._release_deferred(session_id)
                if session_id not in self._rerun:
                    return
        except Exception:
            log.exception("held message drain failed", extra={"session": session_id})
        finally:
            self._draining.discard(session_id)

    async def _release_deferred(self, session_id: str) -> None:
        session = self._runtime.storage.get_session(session_id)
        if session is None:
            self._deferred.discard(session_id)
            return
        if session.focus or session.status in _UNDELIVERABLE:
            return
        transport = self._runtime.transport_for(session)
        async with self._send_locks[session_id]:
            for held in self._runtime.storage.list_held_messages(session_id):
                if not held.auto_release:
                    continue
                current = self._runtime.storage.get_session(session_id)
                if current is None:
                    break
                session = current
                if held.origin is HeldMessageOrigin.WAKE and not (
                    self._runtime._wake_eligible(session)
                ):
                    continue
                if transport.has_pending_approval(session):
                    break  # the response or invalidation is the next edge
                if await transport.input_blocked(session):
                    self._retry_later(session_id)
                    break
                record = self._runtime.storage.take_held_message(held.id)
                if record is None:
                    continue
                try:
                    await self._deliver_claimed(record)
                except InputBlockedError:
                    self._retry_later(session_id)
                    break
                except Exception:
                    log.exception(
                        "held message delivery failed", extra={"held": record.id}
                    )
        if not any(
            held.auto_release
            for held in self._runtime.storage.list_held_messages(session_id)
        ):
            self._deferred.discard(session_id)

    def _retry_later(self, session_id: str) -> None:
        if session_id in self._retries:
            return

        def fire() -> None:
            self._retries.pop(session_id, None)
            self.kick(session_id)

        self._retries[session_id] = asyncio.get_running_loop().call_later(
            DEFER_RETRY_SECONDS, fire
        )

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
                # Nothing reached the transcript: put it back.
                self._put_back(record)
                raise
            if record.id in self._cancelled:
                self._unpin(record)
                return
            self._dispatching.add(record.id)
            try:
                await self._runtime.dispatch_input(prepared)
            except InputBlockedError:
                # A dialog opened first; keep the item for the next edge.
                self._put_back(record)
                raise
            self._unpin(record)
        except Exception:
            if record.id not in self._runtime.storage.held_message_ids():
                self._unpin(record)
            raise
        finally:
            self._in_flight.pop(record.id, None)
            self._cancelled.discard(record.id)
            self._dispatching.discard(record.id)
            await self._publish(record.session_id)

    def _put_back(self, record: HeldMessageRecord) -> None:
        # Unless it was cancelled meanwhile (a deleted session refuses the
        # insert too).
        if record.id in self._cancelled:
            return
        if self._runtime.storage.create_held_message(record) and record.auto_release:
            self._deferred.add(record.session_id)

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
