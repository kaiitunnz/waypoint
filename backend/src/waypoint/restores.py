"""Running ``restore_session``, noting its failures on the session.

Only a restore without a terminal pane is bounded and cancellable: cancelling a
pane relaunch midway orphans its agent."""

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status

from waypoint.asyncio_tasks import cancel_and_wait
from waypoint.schemas import SessionRecord, SessionStatus

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.restores")

# How long a cancelled restore has to unwind before it is abandoned.
CANCEL_GRACE_SECONDS = 5.0

# Shutdown's grace for a teardown in flight. The supervisor kills the backend 10s
# after asking it to stop, and plugins are still to be shut down.
TEARDOWN_STOP_GRACE_SECONDS = 1.0

RESTORING_DETAIL = "session is still being restored; try again in a moment"

type Restore = Callable[["SessionRuntime", SessionRecord], Coroutine[Any, Any, None]]


class RestoreCancelled(HTTPException):
    """The session is being closed, or the backend is stopping."""

    def __init__(self) -> None:
        super().__init__(status.HTTP_409_CONFLICT, "the restore was cancelled")


@dataclass
class _Restore:
    call: asyncio.Task[None]
    # Asks for the call to be stopped.
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    # Set once this restore writes nothing more.
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    # The session is being closed, and its closer records the final status.
    superseded: bool = False


class RestoreCoordinator:
    def __init__(self, runtime: "SessionRuntime", timeout_seconds: float) -> None:
        self._runtime = runtime
        self._timeout = timeout_seconds
        # Boot restores, which no request awaits.
        self._boot_tasks: dict[str, asyncio.Task[None]] = {}
        # Bounded restores in flight, and ones a close stopped until it ends.
        self._restoring: dict[str, _Restore] = {}
        # Sessions whose process is being replaced, from before the old one
        # goes, each with the deadline on tearing it down.
        self._restarting: dict[str, asyncio.Timeout] = {}
        # Sessions being closed, which take no new restore.
        self._closing: Counter[str] = Counter()
        # Teardowns in flight, which can outlive the request that began them.
        self._teardowns: dict[str, asyncio.Task[None]] = {}
        # Restarts waiting on one of those, each with the event that stops them.
        self._awaiting_teardown: dict[str, asyncio.Event] = {}

    def spawn_boot(self, session_id: str, boot: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(boot, name=f"restore-{session_id}")
        self._boot_tasks[session_id] = task
        task.add_done_callback(lambda _: self._boot_tasks.pop(session_id, None))

    def boot_task(self, session_id: str) -> asyncio.Task[None] | None:
        return self._boot_tasks.get(session_id)

    def is_restoring(self, session_id: str) -> bool:
        return session_id in self._restoring

    def holds_sends(self, session_id: str) -> bool:
        """Whether sends queue for the session: it is restoring or about to."""
        return self.is_restoring(session_id) or session_id in self._restarting

    def pauses_delivery(self, session_id: str) -> bool:
        """Whether queued sends wait: the session holds sends or is closing."""
        return self.holds_sends(session_id) or session_id in self._closing

    def require_restored(self, session_id: str) -> None:
        if self.is_restoring(session_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=RESTORING_DETAIL
            )

    @asynccontextmanager
    async def restarting(
        self, session: SessionRecord
    ) -> AsyncIterator[Callable[[], None]]:
        """Queue sends from before a reattach or settings change tears the old
        process down. The caller holds the session lock, and calls the yielded
        function as the teardown begins, which bounds it like the restore."""
        if not self._is_bounded(session):
            await self._await_earlier_teardown(session.id)
            yield lambda: None
            return
        deadline = asyncio.timeout(None)

        def start_clock() -> None:
            deadline.reschedule(asyncio.get_running_loop().time() + self._timeout)

        self._restarting[session.id] = deadline
        try:
            await self._await_earlier_teardown(session.id)
            async with deadline:
                yield start_clock
        except TimeoutError:
            if not deadline.expired():
                raise
            failure = TimeoutError(f"timed out after {self._timeout:g}s")
            log.error("restart of session %s %s", session.id, failure)
            await self._note_failure(session.id, failure, SessionStatus.ERROR)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(failure)
            ) from None
        finally:
            self._restarting.pop(session.id, None)

    @asynccontextmanager
    async def closing(self, session_id: str) -> AsyncIterator[None]:
        """Stop the restore in flight and refuse new ones while the block closes
        the session. The block records the final status."""
        self._closing[session_id] += 1
        try:
            # Its restart holds the session lock the close needs.
            if waiting := self._awaiting_teardown.get(session_id):
                waiting.set()
            if entry := self._restoring.get(session_id):
                entry.superseded = True
                await _stop(entry)
            yield
        finally:
            self._closing[session_id] -= 1
            if not self._closing[session_id]:
                del self._closing[session_id]
                # Any entry left is a restore this close stopped.
                self._restoring.pop(session_id, None)

    async def tear_down(
        self, session_id: str, teardown: Coroutine[Any, Any, None]
    ) -> None:
        """Await ``teardown`` without cancelling it: one cut short leaves its
        process running and untracked."""
        task = asyncio.create_task(teardown)
        self._teardowns[session_id] = task
        task.add_done_callback(lambda _: self._teardowns.pop(session_id, None))
        await asyncio.shield(task)

    async def _await_earlier_teardown(self, session_id: str) -> None:
        """Wait for a teardown an earlier request stopped waiting for, so no
        process starts beside the one still going: its adapter has already
        forgotten it."""
        if not (earlier := self._teardowns.get(session_id)):
            return
        stop = self._awaiting_teardown[session_id] = asyncio.Event()
        if session_id in self._closing:
            stop.set()
        try:
            await _wait_for_end(earlier, stop, self._timeout)
        finally:
            del self._awaiting_teardown[session_id]
        if stop.is_set():
            raise RestoreCancelled
        if not earlier.done():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="the previous process is still shutting down",
            )

    async def run(
        self, session: SessionRecord, restore: Restore
    ) -> BaseException | None:
        """Restore ``session``, the record as read before any status write, and
        return the failure noted on it.

        Raises ``RestoreCancelled`` when the session is being closed or the
        backend is stopping, and 404 when it has been deleted."""
        if session.id in self._closing:
            raise RestoreCancelled
        if self._runtime.storage.get_session(session.id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="session not found"
            )
        if deadline := self._restarting.get(session.id):
            # The restore has its own bound.
            deadline.reschedule(None)
        if self._is_bounded(session):
            return await self._run_bounded(session, restore)
        try:
            await restore(self._runtime, session)
        except Exception as exc:
            log.exception("restore failed for session %s", session.id)
            # The pane may be alive, and an errored pane session cannot
            # relaunch, so the status stands.
            await self._note_failure(session.id, exc, None)
            return exc
        return None

    async def stop(self) -> None:
        """Stop every restore for shutdown, giving each session its status back."""
        await cancel_and_wait(*self._boot_tasks.values())
        await asyncio.gather(*map(_stop, list(self._restoring.values())))
        if self._teardowns:
            _, stuck = await asyncio.wait(
                self._teardowns.values(), timeout=TEARDOWN_STOP_GRACE_SECONDS
            )
            with suppress(Exception):
                await cancel_and_wait(*stuck)

    def _is_bounded(self, session: SessionRecord) -> bool:
        plugin = self._runtime.registry.plugin_for(session)
        return not plugin.capabilities.has_terminal_pane

    async def _run_bounded(
        self, session: SessionRecord, restore: Restore
    ) -> BaseException | None:
        previous = session
        if previous.status is SessionStatus.STARTING:
            # Left behind by a process that stopped mid-restore.
            previous = previous.model_copy(update={"status": SessionStatus.IDLE})
        self._runtime.storage.update_session(session.id, status=SessionStatus.STARTING)
        entry = _Restore(
            asyncio.create_task(
                restore(self._runtime, session), name=f"restore-call-{session.id}"
            )
        )
        entry.call.add_done_callback(_consume_result)
        self._restoring[session.id] = entry
        try:
            self._runtime._publish_session_state(session.id)
            await _wait_for_end(entry.call, entry.stop, self._timeout)
            timed_out = not entry.call.done() and not entry.stop.is_set()
            if not entry.call.done():
                await _abandon(entry.call)
            if entry.superseded:
                raise RestoreCancelled
            failure: BaseException | None
            if timed_out:
                failure = TimeoutError(f"timed out after {self._timeout:g}s")
            elif entry.stop.is_set() or entry.call.cancelled():
                self._write_back(previous)
                raise RestoreCancelled
            else:
                failure = entry.call.exception()
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
            # A close keeps the entry until it has recorded the final status.
            if not entry.superseded:
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


async def _wait_for_end(
    call: asyncio.Task[None], stop: asyncio.Event, timeout: float
) -> None:
    """Wait until the call ends, a stop is asked for, or ``timeout`` passes."""
    stopping = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait(
            {call, stopping},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        stopping.cancel()


async def _stop(entry: _Restore) -> None:
    entry.stop.set()
    await entry.settled.wait()


async def _abandon(call: asyncio.Task[None]) -> None:
    call.cancel()
    done, _ = await asyncio.wait({call}, timeout=CANCEL_GRACE_SECONDS)
    if not done:
        log.warning("%s ignored its cancellation; abandoning it", call.get_name())


def _consume_result(call: asyncio.Task[None]) -> None:
    if not call.cancelled():
        call.exception()
