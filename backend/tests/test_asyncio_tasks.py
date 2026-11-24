import asyncio

import pytest

from waypoint.asyncio_tasks import cancel_and_wait

TIMEOUT = 2.0


async def _slow_to_cancel(
    release: asyncio.Event,
    child_exit: type[BaseException] | None = asyncio.CancelledError,
) -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await release.wait()
        if child_exit is not None:
            raise child_exit() from None


async def _cancel_while_waiting(
    child_exit: type[BaseException] | None,
) -> asyncio.Task[None]:
    release = asyncio.Event()
    child = asyncio.create_task(_slow_to_cancel(release, child_exit))
    await asyncio.sleep(0)
    caller = asyncio.create_task(cancel_and_wait(child))
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, TIMEOUT)
    return child


async def test_absorbs_the_childs_own_cancellation() -> None:
    child = asyncio.create_task(asyncio.Event().wait())
    await asyncio.sleep(0)

    await asyncio.wait_for(cancel_and_wait(child), TIMEOUT)

    assert child.cancelled()


async def test_propagates_caller_cancellation_while_child_stops() -> None:
    child = await _cancel_while_waiting(asyncio.CancelledError)
    assert child.cancelled()


async def test_propagates_caller_cancellation_when_child_ignores_its_cancel() -> None:
    child = await _cancel_while_waiting(None)
    assert child.result() is None


async def test_propagates_caller_cancellation_when_child_raises() -> None:
    child = await _cancel_while_waiting(ValueError)
    assert isinstance(child.exception(), ValueError)


async def test_propagates_the_exception_a_child_finished_with() -> None:
    async def boom() -> None:
        raise ValueError("boom")

    child = asyncio.create_task(boom())
    await asyncio.sleep(0)

    with pytest.raises(ValueError, match="boom"):
        await asyncio.wait_for(cancel_and_wait(child), TIMEOUT)


async def test_returns_for_a_child_already_finished() -> None:
    child = asyncio.create_task(asyncio.sleep(0))
    await child

    await asyncio.wait_for(cancel_and_wait(child), TIMEOUT)


async def test_runs_inside_the_callers_own_cancellation_cleanup() -> None:
    cleaned_up = asyncio.Event()

    async def caller() -> None:
        child = asyncio.create_task(asyncio.Event().wait())
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await cancel_and_wait(child)
            cleaned_up.set()
            raise

    task = asyncio.create_task(caller())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, TIMEOUT)
    assert cleaned_up.is_set()


async def test_cancels_every_task_before_waiting() -> None:
    release = asyncio.Event()
    tasks = [asyncio.create_task(_slow_to_cancel(release)) for _ in range(2)]
    await asyncio.sleep(0)

    waiter = asyncio.create_task(cancel_and_wait(*tasks))
    await asyncio.sleep(0)
    assert all(task.cancelling() for task in tasks)

    release.set()
    await asyncio.wait_for(waiter, TIMEOUT)
    assert all(task.cancelled() for task in tasks)
