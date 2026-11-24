import asyncio
from typing import Any


async def cancel_and_wait(*tasks: asyncio.Future[Any]) -> None:
    """Cancel *tasks*, then wait for each to finish.

    A task's own cancellation is absorbed, and an exception a task finishes
    with propagates. A cancellation of the waiting task that arrives during
    the wait always propagates, even when a task ignores its cancel and
    returns or raises instead; the remaining tasks are then left cancelled but
    not awaited.
    """
    for task in tasks:
        task.cancel()
    current = asyncio.current_task()
    # Relative to entry: the caller may be running this from its own
    # cancellation cleanup.
    baseline = 0 if current is None else current.cancelling()

    def caller_cancelled() -> bool:
        return current is not None and current.cancelling() > baseline

    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            if caller_cancelled():
                raise
        except Exception as exc:
            if caller_cancelled():
                raise asyncio.CancelledError from exc
            raise
        else:
            if caller_cancelled():
                raise asyncio.CancelledError
