"""The personal assistant's setup, run beside the app once it serves."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

from waypoint.asyncio_tasks import cancel_and_wait

if TYPE_CHECKING:
    from waypoint.runtime import SessionRuntime

log = logging.getLogger("waypoint.assistant_boot")

# Shutdown's grace for a launch in flight. The supervisor kills the backend 10s
# after asking it to stop, and draining requests can take 5s of that.
LAUNCH_STOP_GRACE_SECONDS = 3.0

STARTING_DETAIL = "the assistant is still starting; try again in a moment"


class AssistantBoot:
    def __init__(self, runtime: "SessionRuntime") -> None:
        self._runtime = runtime
        self._task: asyncio.Task[None] | None = None
        # A new assistant thread is launching.
        self._launching = False

    def start(self) -> None:
        # Pin the existing thread now, so clients see it as soon as the app serves.
        self._pin()
        self._task = asyncio.create_task(self._run(), name="assistant-bootstrap")

    @contextmanager
    def launch(self) -> Iterator[None]:
        self._launching = True
        try:
            yield
        finally:
            self._launching = False

    def require_done(self) -> None:
        if self._task is not None and not self._task.done():
            raise _starting()

    def require_not_replaced(self, session_id: str) -> None:
        """Refuse the thread that a launching assistant is about to replace."""
        if self._launching and session_id == self._runtime.assistant_session_id:
            raise _starting()

    async def cancel_waiting(self) -> bool:
        """Stop a setup still waiting on a restore, so it cannot replace the
        thread under a terminate or reset, and return whether one was. A launch
        in flight refuses them."""
        if self._launching:
            raise _starting()
        if self._task is None or self._task.done():
            return False
        await cancel_and_wait(self._task)
        return True

    @asynccontextmanager
    async def replacing(self) -> AsyncIterator[None]:
        """Stop a waiting setup while the block replaces the thread, and start it
        again if the block fails."""
        stopped = await self.cancel_waiting()
        try:
            yield
        except BaseException:
            if stopped:
                self.start()
            raise

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._launching:
            # A pane launch cancelled midway orphans its agent.
            await asyncio.wait({self._task}, timeout=LAUNCH_STOP_GRACE_SECONDS)
        await cancel_and_wait(self._task)
        self._task = None

    def _pin(self) -> None:
        runtime = self._runtime
        if runtime.settings.assistant_backend() is None:
            return
        rows = runtime._assistant_rows()
        pinned = next(filter(runtime._assistant_is_reusable, rows), None)
        if pinned is None:
            pinned = next(iter(rows), None)
        if pinned is not None:
            runtime.assistant_session_id = pinned.id

    async def _run(self) -> None:
        pinned = self._runtime.assistant_session_id
        if pinned is not None and (restore := self._runtime.restores.boot_task(pinned)):
            # Reuse or replace depends on how the restore ends.
            await asyncio.wait({restore})
        try:
            await self._runtime._ensure_assistant_session()
        except Exception:
            log.exception("failed to ensure the assistant session")


def _starting() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STARTING_DETAIL)
