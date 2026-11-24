import asyncio

import pytest

from waypoint.asyncio_tasks import cancel_and_wait

TIMEOUT = 2.0


async def _slow_to_cancel(release: asyncio.Event, entered: asyncio.Event) -> None:
    try:
        entered.set()
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await release.wait()
        raise


async def _cancel_while_waiting(
    child_exit: type[BaseException] | None,
) -> asyncio.Task[None]:
    """Cancel a caller mid-wait on a slow-to-stop child that then raises
    *child_exit*, or returns normally when it is ``None``."""
    release = asyncio.Event()
    entered = asyncio.Event()

    async def child() -> None:
        try:
            entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            if child_exit is not None:
                raise child_exit() from None

    child_task = asyncio.create_task(child())
    await entered.wait()
    caller = asyncio.create_task(cancel_and_wait(child_task))
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, TIMEOUT)
    return caller


async def test_absorbs_the_childs_own_cancellation() -> None:
    child = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)

    await asyncio.wait_for(cancel_and_wait(child), TIMEOUT)

    assert child.cancelled()


async def test_propagates_caller_cancellation_while_child_stops() -> None:
    caller = await _cancel_while_waiting(asyncio.CancelledError)
    assert caller.cancelled()


async def test_propagates_caller_cancellation_when_child_ignores_its_cancel() -> None:
    caller = await _cancel_while_waiting(None)
    assert caller.cancelled()


async def test_propagates_caller_cancellation_when_child_raises() -> None:
    caller = await _cancel_while_waiting(ValueError)
    assert caller.cancelled()


async def test_propagates_the_exception_a_child_finished_with() -> None:
    async def boom() -> None:
        raise ValueError("boom")

    child = asyncio.create_task(boom())
    await asyncio.sleep(0)

    with pytest.raises(ValueError, match="boom"):
        await asyncio.wait_for(cancel_and_wait(child), TIMEOUT)


async def test_returns_for_a_child_already_finished() -> None:
    child = asyncio.create_task(asyncio.sleep(0, result="done"))
    await child

    await asyncio.wait_for(cancel_and_wait(child), TIMEOUT)


async def test_runs_inside_the_callers_own_cancellation_cleanup() -> None:
    cleaned_up = asyncio.Event()
    entered = asyncio.Event()

    async def caller() -> None:
        child = asyncio.create_task(asyncio.Event().wait())
        try:
            entered.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await cancel_and_wait(child)
            cleaned_up.set()
            raise

    task = asyncio.create_task(caller())
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, TIMEOUT)
    assert cleaned_up.is_set()


async def test_cancels_every_task_before_waiting() -> None:
    release = asyncio.Event()
    entered = [asyncio.Event(), asyncio.Event()]
    tasks = [asyncio.create_task(_slow_to_cancel(release, e)) for e in entered]
    for event in entered:
        await event.wait()

    waiter = asyncio.create_task(cancel_and_wait(*tasks))
    await asyncio.sleep(0)
    assert all(task.cancelling() for task in tasks)

    release.set()
    await asyncio.wait_for(waiter, TIMEOUT)
    assert all(task.cancelled() for task in tasks)
