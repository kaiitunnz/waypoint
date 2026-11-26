"""Restores: bounded, cancellable, off the startup path, and holding sends."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from restore_harness import (
    RELEASE_SETTLE_SECONDS,
    Restore,
    add_session,
    assert_refused,
    change_settings,
    gated_restore,
    hang,
    held_texts,
    ignoring_cancellation,
    make_runtime,
    noop,
    notes,
    peer_send,
    raising_async,
    reconnect,
    record_dispatches,
    restore_idle,
    started,
    stored,
    stub_restores,
    until,
)
from waypoint.api import create_app
from waypoint.asyncio_tasks import cancel_and_wait
from waypoint.backends import get_registry
from waypoint.runtime import PreparedInput, SessionRuntime
from waypoint.schemas import (
    HeldMessageOrigin,
    HeldReason,
    LaunchSettingsUpdateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage

pytestmark = pytest.mark.usefixtures("quiet_warming")


async def test_hanging_restore_does_not_block_startup_and_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, session_restore_timeout_seconds=0.3)
    add_session(runtime, "hung")
    add_session(runtime, "healthy")
    stub_restores(monkeypatch, {"hung": hang})

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("hung"))
        assert stored(runtime, "hung").status == SessionStatus.STARTING
        await until(lambda: stored(runtime, "healthy").status == SessionStatus.IDLE)
        await until(lambda: stored(runtime, "hung").status == SessionStatus.ERROR)
        assert "Could not restore this session: timed out" in notes(runtime, "hung")
        assert not runtime.restores.is_restoring("hung")


async def test_app_serves_while_a_restore_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    runtime = app.state.context.runtime
    add_session(runtime, "hung")
    stub_restores(monkeypatch, {"hung": hang})

    async with asyncio.timeout(5):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/health")
            assert response.status_code == 200
            assert stored(runtime, "hung").status == SessionStatus.STARTING


@pytest.mark.parametrize(
    ("transport", "status_after"),
    [
        ("codex_app_server", SessionStatus.ERROR),
        ("tmux", SessionStatus.RUNNING),
    ],
)
async def test_raising_restore_is_noted_and_blocks_no_other(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    status_after: SessionStatus,
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "broken", transport=transport, status=SessionStatus.RUNNING)
    add_session(runtime, "healthy")
    stub_restores(monkeypatch, {"broken": raising_async("another protocol")})
    note = "Could not restore this session: another protocol. Reconnect to try again."

    async with started(runtime):
        await until(lambda: note in notes(runtime, "broken"))
        await until(lambda: stored(runtime, "healthy").status == SessionStatus.IDLE)
        assert stored(runtime, "broken").status == status_after


async def test_restore_that_writes_no_status_returns_to_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "quiet", status=SessionStatus.RUNNING)
    add_session(runtime, "crashed-mid-restore", status=SessionStatus.STARTING)
    stub_restores(monkeypatch, {"quiet": noop, "crashed-mid-restore": noop})

    async with started(runtime):
        await until(lambda: stored(runtime, "quiet").status == SessionStatus.RUNNING)
        await until(
            lambda: (
                stored(runtime, "crashed-mid-restore").status == SessionStatus.IDLE
            )
        )


async def test_driving_requests_are_refused_mid_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {"s1": hang})

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        for request in (
            runtime.handle_input("s1", SessionInputRequest(text="hi")),
            runtime.interrupt("s1"),
            runtime.reattach("s1"),
            runtime.set_model("s1", "other"),
        ):
            await assert_refused(request, 409, "still being restored")
        assert runtime.get_session("s1").status == SessionStatus.STARTING


async def test_a_closing_session_starts_and_delivers_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {"s1": hang})
    sent = record_dispatches(runtime, monkeypatch)
    warmed: list[str] = []
    monkeypatch.setattr(
        runtime,
        "_warm_command_completions",
        lambda session, **_: warmed.append(session.id),
    )
    closing = asyncio.Event()
    close = asyncio.Event()

    async def slow_terminate(*_args: Any) -> None:
        closing.set()
        await close.wait()

    monkeypatch.setattr(
        get_registry().get("codex"), "terminate_session", slow_terminate
    )

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("held"), HeldMessageOrigin.AGENT)
        terminate = asyncio.create_task(runtime.terminate("s1"))
        await asyncio.wait_for(closing.wait(), timeout=2)
        typed = SessionInputRequest(text="typed")
        await assert_refused(runtime.handle_input("s1", typed), 409)
        await runtime.held.deliver("s1", peer_send("late"), HeldMessageOrigin.AGENT)
        await asyncio.sleep(RELEASE_SETTLE_SECONDS)
        close.set()
        await terminate
        await asyncio.sleep(RELEASE_SETTLE_SECONDS)

        assert not runtime.restores.is_restoring("s1")
        assert stored(runtime, "s1").status == SessionStatus.EXITED
        assert (sent, warmed) == ([], [])
        assert held_texts(runtime.storage, "s1") == ["held", "late"]


@pytest.mark.parametrize(
    ("status", "restart"),
    [(SessionStatus.EXITED, reconnect), (SessionStatus.IDLE, change_settings)],
)
async def test_terminate_fails_the_request_whose_restore_it_cancels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: SessionStatus,
    restart: Callable[[SessionRuntime], Awaitable[SessionRecord]],
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", status=status)
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", noop)
    stub_restores(monkeypatch, {"s1": hang})

    request = asyncio.ensure_future(restart(runtime))
    await until(lambda: runtime.restores.is_restoring("s1"))
    async with asyncio.timeout(2):
        await runtime.terminate("s1")

    await assert_refused(request, 409)
    assert stored(runtime, "s1").status == SessionStatus.EXITED


async def test_a_restore_that_ignores_a_terminate_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.restores.CANCEL_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, session_restore_timeout_seconds=0.3)
    add_session(runtime, "s1")
    entered = asyncio.Event()
    release = asyncio.Event()
    stub_restores(monkeypatch, {"s1": ignoring_cancellation(entered, release)})

    async with started(runtime):
        try:
            await entered.wait()
            assert (boot := runtime.restores.boot_task("s1"))
            await runtime.terminate("s1")
            await asyncio.wait({boot}, timeout=2)
            assert boot.done()
            assert stored(runtime, "s1").status == SessionStatus.EXITED
            assert "Could not restore" not in notes(runtime, "s1")
        finally:
            release.set()


async def test_terminate_outlasts_a_reconnect_restore_that_ignores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.restores.CANCEL_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, session_restore_timeout_seconds=30)
    add_session(runtime, "s1", status=SessionStatus.EXITED)
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", noop)
    entered = asyncio.Event()
    release = asyncio.Event()
    stub_restores(monkeypatch, {"s1": ignoring_cancellation(entered, release)})

    request = asyncio.create_task(runtime.reattach("s1"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        terminate = asyncio.create_task(runtime.terminate("s1"))
        done, _ = await asyncio.wait({terminate}, timeout=2)
        assert done
        await assert_refused(request, 409)
        assert stored(runtime, "s1").status == SessionStatus.EXITED
    finally:
        release.set()
        await asyncio.wait({request}, timeout=5)


async def test_terminate_refuses_restores_that_have_not_begun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", status=SessionStatus.ERROR)
    torn_down = asyncio.Event()
    restores: list[str] = []

    async def slow_teardown(*_args: Any) -> None:
        await torn_down.wait()

    async def hang_restore(_runtime: SessionRuntime, session: SessionRecord) -> None:
        restores.append(session.id)
        await hang()

    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", slow_teardown)
    stub_restores(monkeypatch, {"s1": hang_restore})

    # One tearing the old process down and one queued behind it.
    requests = [asyncio.create_task(runtime.reattach("s1")) for _ in range(2)]
    await until(lambda: runtime.restores.holds_sends("s1"))
    terminate = asyncio.create_task(runtime.terminate("s1"))
    await asyncio.sleep(0.05)
    torn_down.set()
    async with asyncio.timeout(2):
        await terminate

    for request in requests:
        await assert_refused(request, 409)
    assert restores == []
    assert stored(runtime, "s1").status == SessionStatus.EXITED


@pytest.mark.parametrize("transport", ["codex_app_server", "tmux"])
async def test_a_session_deleted_mid_reconnect_starts_no_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", transport=transport, status=SessionStatus.EXITED)
    tearing_down = asyncio.Event()
    torn_down = asyncio.Event()
    restored: list[str] = []

    async def slow_teardown(*_args: Any) -> None:
        tearing_down.set()
        await torn_down.wait()

    async def record(_runtime: SessionRuntime, session: SessionRecord) -> None:
        restored.append(session.id)

    driver = get_registry().for_transport(transport)
    monkeypatch.setattr(driver, "terminate_session", slow_teardown)
    stub_restores(monkeypatch, {"s1": record})

    request = asyncio.create_task(runtime.reattach("s1"))
    await asyncio.wait_for(tearing_down.wait(), timeout=5)
    await runtime.delete("s1")
    torn_down.set()

    await assert_refused(request, 404)
    await asyncio.sleep(0.05)
    assert restored == []


async def test_stopping_mid_restore_writes_the_status_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", status=SessionStatus.WAITING_INPUT)
    stub_restores(monkeypatch, {"s1": hang})

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))

    reopened = Storage(runtime.settings.database_path)
    session = reopened.get_session("s1")
    assert session is not None
    assert session.status == SessionStatus.WAITING_INPUT


async def test_nothing_is_delivered_while_shutdown_cancels_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    add_session(runtime, "slow")

    async def unwind_slowly(*_args: Any) -> None:
        try:
            await hang()
        finally:
            await asyncio.sleep(0.6)

    stub_restores(monkeypatch, {"s1": hang, "slow": unwind_slowly})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("held"), HeldMessageOrigin.AGENT)

    assert sent == []
    assert held_texts(Storage(runtime.settings.database_path), "s1") == ["held"]


async def test_shutdown_delivers_nothing_as_a_cancelled_reconnect_unwinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {})
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", noop)
    sent = record_dispatches(runtime, monkeypatch)
    requests: list[asyncio.Task[SessionRecord]] = []
    stop_scheduler = runtime.scheduler.stop

    async def stop_slowly() -> None:
        # The server gives up on the request while delivery is still stopping.
        requests[0].cancel()
        await asyncio.sleep(0.2)
        await stop_scheduler()

    monkeypatch.setattr(runtime.scheduler, "stop", stop_slowly)

    async with started(runtime):
        await until(lambda: not runtime.restores.is_restoring("s1"))
        stub_restores(monkeypatch, {"s1": hang})
        requests.append(asyncio.create_task(runtime.reattach("s1")))
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("held"), HeldMessageOrigin.AGENT)

    assert sent == []
    assert held_texts(Storage(runtime.settings.database_path), "s1") == ["held"]


@pytest.mark.parametrize(
    ("status", "outcome"),
    [(SessionStatus.IDLE, restore_idle), (SessionStatus.INTERRUPTED, noop)],
)
async def test_send_mid_restore_is_held_and_delivered_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: SessionStatus,
    outcome: Restore,
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", status=status)
    release = asyncio.Event()
    stub_restores(monkeypatch, {"s1": gated_restore(release, outcome)})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("first"), HeldMessageOrigin.AGENT)
        [held] = runtime.storage.list_held_messages("s1")
        assert held.hold_reason is HeldReason.RESTORE
        assert sent == []

        release.set()
        await until(lambda: sent == ["first"])


async def test_held_sends_survive_a_failed_restore_and_keep_their_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    release = asyncio.Event()
    failing = gated_restore(release, raising_async("down"))
    stub_restores(monkeypatch, {"s1": failing})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("first"), HeldMessageOrigin.AGENT)
        release.set()
        await until(lambda: stored(runtime, "s1").status == SessionStatus.ERROR)
        # A newer send queues behind the held one instead of overtaking it.
        await runtime.held.deliver("s1", peer_send("second"), HeldMessageOrigin.AGENT)
        await asyncio.sleep(0.05)
        assert sent == []
        assert held_texts(runtime.storage, "s1") == ["first", "second"]

        stub_restores(monkeypatch, {})
        await runtime.reattach("s1")
        await until(lambda: sent == ["first", "second"])


async def test_a_send_reopens_a_closed_session_behind_what_it_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {"s1": hang})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("old"), HeldMessageOrigin.AGENT)
        await runtime.terminate("s1")
        stub_restores(monkeypatch, {})
        await runtime.held.deliver("s1", peer_send("new"), HeldMessageOrigin.AGENT)

        await until(lambda: sent == ["old", "new"])
        assert stored(runtime, "s1").status == SessionStatus.IDLE


async def test_concurrent_sends_to_a_closed_session_reopen_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    restores: list[str] = []

    async def restore(runtime: SessionRuntime, session: SessionRecord) -> None:
        restores.append(session.id)
        await restore_idle(runtime, session)

    stub_restores(monkeypatch, {"s1": hang})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("old"), HeldMessageOrigin.AGENT)
        await runtime.terminate("s1")
        stub_restores(monkeypatch, {"s1": restore})
        await asyncio.gather(
            *(
                runtime.held.deliver("s1", peer_send(text), HeldMessageOrigin.AGENT)
                for text in ("one", "two")
            )
        )

        await until(lambda: sent == ["old", "one", "two"])
        assert restores == ["s1"]


async def test_a_cancelled_reopening_send_leaves_no_copy_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {"s1": hang})
    record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("old"), HeldMessageOrigin.AGENT)
        await runtime.terminate("s1")
        send = asyncio.create_task(
            runtime.held.deliver("s1", peer_send("new"), HeldMessageOrigin.AGENT)
        )
        await until(lambda: runtime.restores.is_restoring("s1"))
        send.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send
        # Its sender saw no result and sends it again, so no copy stays held.
        assert held_texts(runtime.storage, "s1") == ["old"]


async def test_shutdown_does_not_wait_long_for_a_hung_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {})
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", hang)

    async with asyncio.timeout(3):
        async with started(runtime):
            await until(lambda: not runtime.restores.is_restoring("s1"))
            request = asyncio.create_task(runtime.reattach("s1"))
            await until(lambda: runtime.restores.holds_sends("s1"))
            # As the server does at its graceful timeout.
            request.cancel()


async def test_sends_behind_a_reopening_send_are_held_not_made_to_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", status=SessionStatus.ERROR)
    torn_down = asyncio.Event()
    restores: list[str] = []

    async def slow_teardown(*_args: Any) -> None:
        await torn_down.wait()

    async def fail(_runtime: SessionRuntime, session: SessionRecord) -> None:
        restores.append(session.id)
        raise RuntimeError("down")

    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", slow_teardown)
    stub_restores(monkeypatch, {"s1": fail})
    record_dispatches(runtime, monkeypatch)

    first = asyncio.create_task(
        runtime.held.deliver("s1", peer_send("first"), HeldMessageOrigin.AGENT)
    )
    await until(lambda: runtime.restores.holds_sends("s1"))
    async with asyncio.timeout(2):
        await runtime.held.deliver("s1", peer_send("second"), HeldMessageOrigin.AGENT)
    torn_down.set()

    with pytest.raises(HTTPException):
        await first
    assert restores == ["s1"]
    assert held_texts(runtime.storage, "s1") == ["second"]


async def test_a_settings_change_holds_sends_until_the_restart_is_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    torn_down = asyncio.Event()

    async def teardown(_runtime: SessionRuntime, session: SessionRecord) -> None:
        # The interrupted turn closes while the old process goes away.
        await runtime._record_system_event(
            session.id, "Turn closed", status=SessionStatus.IDLE
        )
        await torn_down.wait()

    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", teardown)
    stub_restores(monkeypatch, {})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: not runtime.restores.is_restoring("s1"))
        request = asyncio.create_task(change_settings(runtime))
        await until(lambda: runtime.restores.holds_sends("s1"))
        await runtime.held.deliver("s1", peer_send("queued"), HeldMessageOrigin.AGENT)
        await asyncio.sleep(RELEASE_SETTLE_SECONDS)
        assert sent == []
        torn_down.set()
        await request
        await until(lambda: sent == ["queued"])


async def test_sends_held_through_a_switch_to_a_pane_wait_for_its_input_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.held.DEFER_RETRY_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, session_restore_timeout_seconds=0.5)
    add_session(runtime, "s1")
    runtime.storage.update_session("s1", transport_state={"thread_id": str(uuid4())})
    torn_down = asyncio.Event()

    async def teardown(*_args: Any) -> None:
        await torn_down.wait()

    async def persisted(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(get_registry().get("codex"), "conversation_exists", persisted)
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", teardown)
    stub_restores(monkeypatch, {})
    sent: list[str] = []

    async def dispatch(prepared: PreparedInput) -> SessionRecord:
        sent.append(prepared.request.text)
        # A pane reads busy once it takes input.
        return runtime.storage.update_session("s1", status=SessionStatus.RUNNING)

    async def unblocked(*_args: Any) -> bool:
        return False

    booted = asyncio.Event()

    async def ready(*_args: Any) -> bool:
        return booted.is_set()

    monkeypatch.setattr(runtime, "dispatch_input", dispatch)
    pane = runtime.transport_for(add_session(runtime, "pane", transport="tmux"))
    monkeypatch.setattr(pane, "input_blocked", unblocked)
    monkeypatch.setattr(pane, "ready_for_input", ready)

    async with started(runtime):
        await until(lambda: not runtime.restores.is_restoring("s1"))
        request = asyncio.create_task(
            runtime.update_launch_settings(
                "s1", LaunchSettingsUpdateRequest(transport="tmux", restart=True)
            )
        )
        await until(lambda: runtime.restores.holds_sends("s1"))
        wake = SessionInputRequest(text="wake")
        await runtime.held.deliver("s1", wake, HeldMessageOrigin.WAKE)
        for text in ("one", "two"):
            await runtime.held.deliver("s1", peer_send(text), HeldMessageOrigin.AGENT)
        torn_down.set()
        await request
        # Longer than the restore timeout: a booting pane is never typed into.
        await asyncio.sleep(0.8)
        assert sent == []
        assert held_texts(runtime.storage, "s1") == ["wake", "one", "two"]
        booted.set()
        await until(lambda: sent == ["wake", "one", "two"])


async def test_a_send_that_fails_as_a_restart_begins_stays_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    release = asyncio.Event()
    torn_down = asyncio.Event()
    stub_restores(monkeypatch, {"s1": gated_restore(release)})

    async def teardown(*_args: Any) -> None:
        await torn_down.wait()

    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", teardown)
    sent: list[str] = []
    requests: list[asyncio.Task[SessionRecord]] = []

    async def dispatch(prepared: PreparedInput) -> SessionRecord:
        if not requests:
            requests.append(asyncio.create_task(runtime.reattach("s1")))
            await until(lambda: runtime.restores.holds_sends("s1"))
            raise RuntimeError("the process is gone")
        sent.append(prepared.request.text)
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", dispatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("one"), HeldMessageOrigin.AGENT)
        release.set()
        await until(lambda: requests != [])
        await asyncio.sleep(0.05)
        assert held_texts(runtime.storage, "s1") == ["one"]

        torn_down.set()
        await requests[0]
        await until(lambda: sent == ["one"])


@pytest.mark.parametrize("restart", [reconnect, change_settings])
async def test_a_hung_teardown_is_bounded_and_keeps_held_sends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    restart: Callable[[SessionRuntime], Awaitable[SessionRecord]],
) -> None:
    runtime = make_runtime(tmp_path, session_restore_timeout_seconds=0.3)
    add_session(runtime, "s1")
    release = asyncio.Event()
    torn_down = asyncio.Event()

    async def stuck_teardown(*_args: Any) -> None:
        await release.wait()
        torn_down.set()

    monkeypatch.setattr(
        get_registry().get("codex"), "terminate_session", stuck_teardown
    )
    stub_restores(monkeypatch, {})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: not runtime.restores.is_restoring("s1"))
        request = asyncio.ensure_future(restart(runtime))
        await until(lambda: runtime.restores.holds_sends("s1"))
        await runtime.held.deliver("s1", peer_send("held"), HeldMessageOrigin.AGENT)
        async with asyncio.timeout(5):
            await assert_refused(request, 504, "timed out")

        await asyncio.sleep(RELEASE_SETTLE_SECONDS)
        assert not runtime.restores.holds_sends("s1")
        assert stored(runtime, "s1").status == SessionStatus.ERROR
        assert "timed out after 0.3s" in notes(runtime, "s1")
        assert sent == []
        assert held_texts(runtime.storage, "s1") == ["held"]
        # The teardown was left to finish, not cut short.
        release.set()
        await asyncio.wait_for(torn_down.wait(), timeout=2)


async def test_a_restart_stops_a_delivery_already_under_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Shutdown gives the teardown that never ends this long.
    monkeypatch.setattr("waypoint.restores.TEARDOWN_STOP_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    release = asyncio.Event()
    stub_restores(monkeypatch, {"s1": gated_restore(release)})
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", hang)
    sent: list[str] = []
    requests: list[asyncio.Task[SessionRecord]] = []

    async def dispatch(prepared: PreparedInput) -> SessionRecord:
        sent.append(prepared.request.text)
        requests.append(asyncio.create_task(runtime.reattach("s1")))
        await until(lambda: runtime.restores.holds_sends("s1"))
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", dispatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        for text in ("one", "two"):
            await runtime.held.deliver("s1", peer_send(text), HeldMessageOrigin.AGENT)
        release.set()
        await until(lambda: sent == ["one"])
        await asyncio.sleep(RELEASE_SETTLE_SECONDS)

        assert sent == ["one"]
        assert held_texts(runtime.storage, "s1") == ["two"]
        await cancel_and_wait(*requests)
