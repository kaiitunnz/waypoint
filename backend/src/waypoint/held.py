"""Held delivery for agent sends, scheduled firings, and board/inbox wakes.

Each held message records what releases it: the human (Focus), the session's
open dialog clearing, the session going idle (wakes), or its restore ending
(sends). Focus pauses every automatic release."""

import asyncio
import logging
import uuid
from collections import defaultdict
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.asyncio_tasks import cancel_and_wait
from waypoint.schemas import (
    HeldMessageOrigin,
    HeldMessageRecord,
    HeldReason,
    SessionEnvelope,
    SessionInputRequest,
    SessionRecord,
    SessionStatus,
)
from waypoint.transports import InputBlockedError

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.held")

# A pane-only block ends without a status edge, so delivery re-checks on this
# cadence.
DEFER_RETRY_SECONDS = 3.0
_UNDELIVERABLE = frozenset(
    {SessionStatus.EXITED, SessionStatus.ERROR, SessionStatus.STARTING}
)


class HeldQueue:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        # Claimed held id -> session id. A cancel before dispatch stops the
        # delivery; one during dispatch is a 409.
        self._in_flight: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._dispatching: set[str] = set()
        # Serializes the hold-or-deliver decision so held items keep their order.
        self._send_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Sessions with automatic items, and their drain bookkeeping.
        self._auto_held: set[str] = set()
        self._draining: set[str] = set()
        self._rerun: set[str] = set()
        self._retries: dict[str, asyncio.TimerHandle] = {}
        # Session id -> when its pane was first seen not taking input in the
        # current wait.
        self._unready_since: dict[str, float] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._stopped = False

    def start(self) -> None:
        self._auto_held = self._runtime.storage.auto_held_session_ids()

    async def stop(self) -> None:
        self._stopped = True
        for handle in self._retries.values():
            handle.cancel()
        self._retries.clear()
        await cancel_and_wait(*self._tasks)

    async def deliver(
        self,
        session_id: str,
        request: SessionInputRequest,
        origin: HeldMessageOrigin,
    ) -> SessionRecord | HeldMessageRecord:
        if self._runtime.get_session(session_id).focus:
            return await self._hold(session_id, request, origin, HeldReason.FOCUS)
        wake = origin is HeldMessageOrigin.WAKE
        if self._runtime.restores.holds_sends(session_id):
            # No send lock: a send that reopened the session holds it until the
            # restore ends.
            reason = HeldReason.IDLE if wake else HeldReason.RESTORE
            record = await self._hold(session_id, request, origin, reason)
        else:
            async with self._send_locks[session_id]:
                session = self._runtime.get_session(session_id)
                if session.focus:
                    return await self._hold(
                        session_id, request, origin, HeldReason.FOCUS
                    )
                if not wake:
                    # An unavailable session queues no send either.
                    self._runtime.availability.require(session)
                queued = self._queued_behind_restore(session)
                if not queued and self._deliverable_now(session, wake):
                    with suppress(InputBlockedError):
                        delivered = await self._runtime.handle_input(
                            session_id, request
                        )
                        if wake and self._runtime.storage.delete_held_wake(session_id):
                            await self._publish(session_id)
                        return delivered
                if wake:
                    reason = HeldReason.IDLE
                else:
                    reason = HeldReason.RESTORE if queued else HeldReason.DIALOG
                record = await self._hold(session_id, request, origin, reason)
                if queued and not wake and session.status is SessionStatus.EXITED:
                    # Under the send lock, so only one send reopens it.
                    try:
                        await self._reopen(session_id)
                    except BaseException:
                        # The caller sends it again, so no copy may stay held.
                        if self._runtime.storage.take_held_message(record.id):
                            self._unpin(record)
                        raise
        self._auto_held.add(session_id)
        self.kick(session_id)
        return record

    async def _reopen(self, session_id: str) -> None:
        """Reopen an exited session for a send queued behind what it still holds."""
        try:
            await self._runtime.reattach(session_id)
        except HTTPException as exc:
            # The send stays held for the next reconnect.
            log.warning("could not reopen session %s: %s", session_id, exc.detail)

    def _queued_behind_restore(self, session: SessionRecord) -> bool:
        """Whether a send waits for a restore, or behind sends still held from
        one."""
        return self._runtime.restores.holds_sends(session.id) or (
            self._runtime.storage.has_held_messages(session.id, HeldReason.RESTORE)
        )

    def _deliverable_now(self, session: SessionRecord, wake: bool) -> bool:
        # Queue behind items waiting on a dialog so delivery keeps its order.
        if self._runtime.storage.has_held_messages(session.id, HeldReason.DIALOG):
            return False
        return not wake or self._runtime.wake_eligible(session)

    async def _hold(
        self,
        session_id: str,
        request: SessionInputRequest,
        origin: HeldMessageOrigin,
        hold_reason: HeldReason,
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
            hold_reason=hold_reason,
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

    def drain(self, session_ids: set[str]) -> None:
        """Deliver automatic items for sessions that reached a new state."""
        for session_id in session_ids & self._auto_held:
            self.kick(session_id)

    def kick(self, session_id: str) -> None:
        """Deliver automatic items after something changed for the session."""
        # Each change gives a pane that takes no input its full wait again.
        self._unready_since.pop(session_id, None)
        self._start_drain(session_id)

    def _start_drain(self, session_id: str) -> None:
        if self._stopped:
            return
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
                await self._release_auto(session_id)
                if session_id not in self._rerun:
                    return
        except Exception:
            log.exception("held message drain failed", extra={"session": session_id})
        finally:
            self._draining.discard(session_id)

    async def _release_auto(self, session_id: str) -> None:
        session = self._runtime.storage.get_session(session_id)
        if session is None:
            self._auto_held.discard(session_id)
            return
        if (
            session.focus
            or session.status in _UNDELIVERABLE
            or self._runtime.restores.pauses_delivery(session_id)
        ):
            return
        async with self._send_locks[session_id]:
            for held in self._runtime.storage.list_held_messages(session_id):
                if held.hold_reason is HeldReason.FOCUS:
                    continue
                current = self._runtime.storage.get_session(session_id)
                # A restart or close can begin while an earlier item goes out.
                if (
                    current is None
                    or current.focus
                    or self._runtime.restores.pauses_delivery(session_id)
                ):
                    break
                session = current
                if held.hold_reason is not HeldReason.DIALOG and not self._ready_for(
                    held, session
                ):
                    continue
                # Per item: an interface switch can change it between two.
                transport = self._runtime.transport_for(session)
                if transport.has_pending_approval(session):
                    break  # the response or invalidation is the next edge
                if await transport.input_blocked(session):
                    # The wait for the input box starts once the dialog is gone.
                    self._unready_since.pop(session_id, None)
                    self._retry_later(session_id)
                    break
                if not await transport.ready_for_input(session):
                    self._await_input_box(session_id)
                    break
                self._unready_since.pop(session_id, None)
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
        if all(
            held.hold_reason is HeldReason.FOCUS
            for held in self._runtime.storage.list_held_messages(session_id)
        ):
            self._auto_held.discard(session_id)

    def _ready_for(self, held: HeldMessageRecord, session: SessionRecord) -> bool:
        """Whether a message waiting on the session's state may go out."""
        if held.hold_reason is HeldReason.RESTORE:
            # A send queued behind a restore follows an interrupted turn, as a
            # fresh send would. A pane, after an interface switch, takes input
            # mid-turn.
            driver = self._runtime.registry.plugin_for(session)
            if (
                session.status is SessionStatus.INTERRUPTED
                or driver.capabilities.has_terminal_pane
            ):
                return True
        return self._runtime.wake_eligible(session)

    def _await_input_box(self, session_id: str) -> None:
        """Check a pane that takes no input again, but only for the restore
        timeout. A message sent into a booting pane is lost, so past that it
        stays held until the session's next state change."""
        now = asyncio.get_running_loop().time()
        since = self._unready_since.setdefault(session_id, now)
        if now - since < self._runtime.settings.session_restore_timeout_seconds:
            self._retry_later(session_id)
        else:
            log.warning("messages held for %s wait for its input box", session_id)

    def _retry_later(self, session_id: str) -> None:
        if self._stopped or session_id in self._retries:
            return

        def fire() -> None:
            self._retries.pop(session_id, None)
            self._start_drain(session_id)

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
            try:
                await self._publish(record.session_id)
                prepared = await self._runtime.prepare_input(record.session_id, request)
            except (Exception, asyncio.CancelledError):
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
            except Exception:
                if self._runtime.restores.pauses_delivery(record.session_id):
                    # A restart or close took the process mid-send.
                    self._put_back(record)
                else:
                    self._unpin(record)
                raise
            self._unpin(record)
        finally:
            self._in_flight.pop(record.id, None)
            self._cancelled.discard(record.id)
            self._dispatching.discard(record.id)
            await self._publish(record.session_id)

    def _put_back(self, record: HeldMessageRecord) -> None:
        """Re-hold a claimed item unless it was cancelled meanwhile."""
        restored = (
            record.id not in self._cancelled
            and self._runtime.storage.create_held_message(record)
        )
        if not restored:
            self._unpin(record)
        elif record.hold_reason is not HeldReason.FOCUS:
            self._auto_held.add(record.session_id)

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
