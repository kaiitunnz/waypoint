"""Running ``restore_session``: failures are noted on the session, and a restore
without a terminal pane is bounded, tracked and cancellable.

Such a restore re-attaches the agent's own process or server, which can hang. A
pane-backed session keeps its live pane and its relaunch is not safe to cancel
midway (it would orphan the agent), so those restores run to completion."""

import asyncio
import logging
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from waypoint.schemas import SessionRecord, SessionStatus

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.restores")

# How long a cancelled restore has to unwind before it is abandoned.
CANCEL_GRACE_SECONDS = 5.0

RESTORING_DETAIL = "session is still being restored; try again in a moment"


@dataclass
class _Restore:
    call: asyncio.Task[None]
    # Set once the coordinator is done with the session.
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    # The session is being closed, and its closer records the final status.
    superseded: bool = False


class RestoreCoordinator:
    def __init__(self, runtime: "SessionRuntime", timeout_seconds: float) -> None:
        self._runtime = runtime
        self._timeout = timeout_seconds
        self._restoring: dict[str, _Restore] = {}

    def is_restoring(self, session_id: str) -> bool:
        return session_id in self._restoring

    def require_restored(self, session_id: str) -> None:
        if self.is_restoring(session_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=RESTORING_DETAIL
            )

    @staticmethod
    def raise_for_request(failure: BaseException | None) -> None:
        """Fail a request whose restore was cancelled or refused outright."""
        if isinstance(failure, asyncio.CancelledError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the restore was cancelled",
            )
        if isinstance(failure, HTTPException):
            raise failure

    async def run(
        self, session: SessionRecord, restore: Coroutine[Any, Any, None]
    ) -> BaseException | None:
        """Await ``restore`` and return its failure, if it raised, timed out or
        was cancelled (a ``CancelledError``, whose session is being closed).

        ``session`` is the record as read before any status write, which is also
        what ``restore`` must have been given."""
        if self._runtime.registry.plugin_for(session).capabilities.has_terminal_pane:
            try:
                await restore
            except Exception as exc:
                log.exception("restore failed for session %s", session.id)
                # The pane may be alive, and an errored pane session cannot
                # relaunch, so the status stands.
                await self._note_failure(session.id, exc, None)
                return exc
            return None
        return await self._run_bounded(session, restore)

    async def cancel(self, session_id: str) -> None:
        """Stop an in-flight restore of a session that is being closed.

        The session keeps its ``starting`` status, which holds sends back, until
        the caller records the final one."""
        if entry := self._restoring.get(session_id):
            entry.superseded = True
            await _stop(entry)

    async def stop(self) -> None:
        """Stop every restore for shutdown, giving each session its status back."""
        await asyncio.gather(*map(_stop, list(self._restoring.values())))

    async def _run_bounded(
        self, session: SessionRecord, restore: Coroutine[Any, Any, None]
    ) -> BaseException | None:
        if self.is_restoring(session.id):
            restore.close()
            self.require_restored(session.id)
        previous = session
        if previous.status is SessionStatus.STARTING:
            # Left behind by a process that stopped mid-restore.
            previous = previous.model_copy(update={"status": SessionStatus.IDLE})
        entry = _Restore(
            asyncio.create_task(restore, name=f"restore-call-{session.id}")
        )
        self._restoring[session.id] = entry
        try:
            self._runtime.storage.update_session(
                session.id, status=SessionStatus.STARTING
            )
            self._runtime._publish_session_state(session.id)
            done, _ = await asyncio.wait({entry.call}, timeout=self._timeout)
            failure: BaseException | None = None
            if not done:
                await _abandon(entry.call)
                failure = TimeoutError(f"timed out after {self._timeout:g}s")
            elif not entry.call.cancelled():
                failure = entry.call.exception()
            if entry.superseded:
                return asyncio.CancelledError()
            if done and entry.call.cancelled():
                self._write_back(previous)
                return asyncio.CancelledError()
            if failure is None:
                self._write_back(previous)
            else:
                log.error("restore failed for session %s", session.id, exc_info=failure)
                await self._note_failure(session.id, failure, SessionStatus.ERROR)
            return failure
        except asyncio.CancelledError:
            await _abandon(entry.call)
            if not entry.superseded:
                self._write_back(previous)
            raise
        finally:
            self._restoring.pop(session.id, None)
            entry.settled.set()

    def _write_back(self, previous: SessionRecord) -> None:
        """Replace the ``starting`` placeholder when the restore wrote no status."""
        current = self._runtime.storage.get_session(previous.id)
        if current is None or current.status is not SessionStatus.STARTING:
            return
        self._runtime.storage.restore_status(previous)
        self._runtime._publish_session_state(previous.id)

    async def _note_failure(
        self,
        session_id: str,
        failure: BaseException,
        new_status: SessionStatus | None,
    ) -> None:
        if self._runtime.storage.get_session(session_id) is None:
            return
        reason = (
            failure.detail if isinstance(failure, HTTPException) else str(failure)
        ) or type(failure).__name__
        await self._runtime._record_system_event(
            session_id,
            f"Could not restore this session: {reason}. Reconnect to try again.",
            status=new_status,
        )


async def _stop(entry: _Restore) -> None:
    entry.call.cancel()
    with suppress(TimeoutError):
        async with asyncio.timeout(CANCEL_GRACE_SECONDS):
            await entry.settled.wait()


async def _abandon(call: asyncio.Task[None]) -> None:
    call.cancel()
    done, _ = await asyncio.wait({call}, timeout=CANCEL_GRACE_SECONDS)
    if not done:
        log.warning("%s ignored its cancellation; abandoning it", call.get_name())
        call.add_done_callback(_consume_result)
    else:
        _consume_result(call)


def _consume_result(call: asyncio.Task[None]) -> None:
    if not call.cancelled():
        call.exception()
