"""Startup and restore: no agent plugin keeps the backend from starting or serving."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from waypoint.api import create_app
from waypoint.asyncio_tasks import cancel_and_wait
from waypoint.backends import get_registry
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.runtime import PreparedInput, SessionRuntime
from waypoint.schemas import (
    EventKind,
    HeldMessageOrigin,
    HeldReason,
    LaunchSettingsUpdateRequest,
    SessionAttachRequest,
    SessionCreateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import AssistantConfig, Settings
from waypoint.storage import Storage

Restore = Callable[[SessionRuntime, SessionRecord], Awaitable[None]]


def make_runtime(tmp_path: Path, **overrides: Any) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data", **overrides)
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


def add_session(
    runtime: SessionRuntime,
    session_id: str,
    *,
    backend: str = "codex",
    transport: str = "codex_app_server",
    status: SessionStatus = SessionStatus.IDLE,
    cwd: str = "/tmp",
    source: SessionSource = SessionSource.MANAGED,
) -> SessionRecord:
    session_dir = runtime.settings.sessions_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    return runtime.storage.create_session(
        SessionRecord(
            id=session_id,
            backend=backend,
            source=source,
            transport=transport,
            title=session_id,
            cwd=cwd,
            status=status,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
        )
    )


def stored(runtime: SessionRuntime, session_id: str) -> SessionRecord:
    session = runtime.storage.get_session(session_id)
    assert session is not None
    return session


def notes(runtime: SessionRuntime, session_id: str) -> str:
    return "\n".join(
        event.text
        for event in runtime.storage.list_events(session_id)
        if event.kind == EventKind.SYSTEM_NOTE
    )


async def until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def hang(*_args: Any, **_kwargs: Any) -> Any:
    await asyncio.Event().wait()


async def noop(*_args: Any) -> None:
    return None


async def restore_idle(runtime: SessionRuntime, session: SessionRecord) -> None:
    await runtime._record_system_event(
        session.id, "Restored", status=SessionStatus.IDLE
    )


def stub_restores(
    monkeypatch: pytest.MonkeyPatch, by_session: dict[str, Restore]
) -> None:
    """Route each plugin's restore to a per-session stand-in."""

    async def restore(runtime: SessionRuntime, session: SessionRecord) -> None:
        await by_session.get(session.id, restore_idle)(runtime, session)

    for transport in ("codex_app_server", "tmux"):
        monkeypatch.setattr(
            get_registry().for_transport(transport), "restore_session", restore
        )


@pytest.fixture(autouse=True)
def quiet_warming(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        SessionRuntime, "_warm_command_completions", lambda *_, **__: None
    )


@asynccontextmanager
async def started(runtime: SessionRuntime) -> AsyncIterator[SessionRuntime]:
    async with asyncio.timeout(5):
        await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


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
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
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


async def test_raising_restore_errors_its_session_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "broken")
    add_session(runtime, "healthy")

    async def explode(*_args: Any) -> None:
        raise RuntimeError("the server speaks another protocol")

    stub_restores(monkeypatch, {"broken": explode})

    async with started(runtime):
        await until(lambda: stored(runtime, "broken").status == SessionStatus.ERROR)
        await until(lambda: stored(runtime, "healthy").status == SessionStatus.IDLE)
        assert (
            "Could not restore this session: the server speaks another protocol. "
            "Reconnect to try again." in notes(runtime, "broken")
        )


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


async def test_failed_pane_restore_keeps_the_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "pane", transport="tmux", status=SessionStatus.RUNNING)

    async def explode(*_args: Any) -> None:
        raise RuntimeError("no tmux server")

    stub_restores(monkeypatch, {"pane": explode})

    async with started(runtime):
        await until(lambda: "no tmux server" in notes(runtime, "pane"))
        assert stored(runtime, "pane").status == SessionStatus.RUNNING


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
            with pytest.raises(HTTPException) as refused:
                await request
            assert refused.value.status_code == 409
            assert "still being restored" in refused.value.detail
        assert runtime.get_session("s1").status == SessionStatus.STARTING


async def test_terminate_cancels_an_in_flight_restore(
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

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("held"), HeldMessageOrigin.AGENT)
        async with asyncio.timeout(2):
            await runtime.terminate("s1")
        assert not runtime.restores.is_restoring("s1")
        await asyncio.sleep(0.4)
        assert stored(runtime, "s1").status == SessionStatus.EXITED
        # Nothing starts for the closed session: no probe, no held delivery.
        assert warmed == []
        assert sent == []


async def test_terminate_fails_the_reattach_it_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", status=SessionStatus.EXITED)
    stub_restores(monkeypatch, {"s1": hang})

    reattach = asyncio.create_task(runtime.reattach("s1"))
    await until(lambda: runtime.restores.is_restoring("s1"))
    async with asyncio.timeout(2):
        await runtime.terminate("s1")

    with pytest.raises(HTTPException) as cancelled:
        await reattach
    assert cancelled.value.status_code == 409
    assert stored(runtime, "s1").status == SessionStatus.EXITED


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


def held_texts(storage: Storage, session_id: str) -> list[str]:
    return [message.text for message in storage.list_held_messages(session_id)]


async def test_nothing_is_delivered_while_a_terminate_closes_a_restoring_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {"s1": hang})
    sent = record_dispatches(runtime, monkeypatch)
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
        await closing.wait()
        with pytest.raises(HTTPException) as refused:
            await runtime.handle_input("s1", SessionInputRequest(text="typed"))
        assert refused.value.status_code == 409
        await runtime.held.deliver("s1", peer_send("late"), HeldMessageOrigin.AGENT)
        # Longer than the broadcast debounce, which is what releases held sends.
        await asyncio.sleep(0.4)
        assert sent == []
        close.set()
        await terminate
        assert not runtime.restores.is_restoring("s1")
        assert stored(runtime, "s1").status == SessionStatus.EXITED
        assert held_texts(runtime.storage, "s1") == ["held", "late"]


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


async def test_a_send_reopens_a_session_closed_mid_restore(
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

        assert sent[:1] == ["new"]


async def test_a_restore_that_ignores_a_terminate_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.restores.CANCEL_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, session_restore_timeout_seconds=0.3)
    add_session(runtime, "s1")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stubborn(*_args: Any) -> None:
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()

    stub_restores(monkeypatch, {"s1": stubborn})

    async with started(runtime):
        try:
            await entered.wait()
            assert (restore := runtime.restores.boot_task("s1"))
            await runtime.terminate("s1")
            await asyncio.wait({restore}, timeout=2)
            assert restore.done()
            assert stored(runtime, "s1").status == SessionStatus.EXITED
            assert "Could not restore" not in notes(runtime, "s1")
        finally:
            release.set()


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
    reattaches = [asyncio.create_task(runtime.reattach("s1")) for _ in range(2)]
    await until(lambda: runtime.restores.holds_sends("s1"))
    terminate = asyncio.create_task(runtime.terminate("s1"))
    await asyncio.sleep(0.05)
    torn_down.set()
    async with asyncio.timeout(2):
        await terminate

    for reattach in reattaches:
        with pytest.raises(HTTPException) as refused:
            await reattach
        assert refused.value.status_code == 409
    assert restores == []
    assert stored(runtime, "s1").status == SessionStatus.EXITED


async def test_terminate_cancels_a_settings_switch_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", noop)
    stub_restores(monkeypatch, {"s1": hang})

    switch = asyncio.create_task(
        runtime.update_launch_settings(
            "s1", LaunchSettingsUpdateRequest(args=["--verbose"], restart=True)
        )
    )
    await until(lambda: runtime.restores.is_restoring("s1"))
    async with asyncio.timeout(2):
        await runtime.terminate("s1")

    with pytest.raises(HTTPException) as cancelled:
        await switch
    assert cancelled.value.status_code == 409
    assert stored(runtime, "s1").status == SessionStatus.EXITED


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
        switch = asyncio.create_task(
            runtime.update_launch_settings(
                "s1", LaunchSettingsUpdateRequest(args=["--verbose"], restart=True)
            )
        )
        await until(lambda: runtime.restores.holds_sends("s1"))
        await runtime.held.deliver("s1", peer_send("queued"), HeldMessageOrigin.AGENT)
        await asyncio.sleep(0.4)
        assert sent == []
        torn_down.set()
        await switch
        await until(lambda: sent == ["queued"])


async def test_a_restart_stops_a_delivery_already_under_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    release = asyncio.Event()
    stub_restores(monkeypatch, {"s1": gated_restore(release)})
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", hang)
    sent: list[str] = []
    reattaches: list[asyncio.Task[SessionRecord]] = []

    async def dispatch(prepared: PreparedInput) -> SessionRecord:
        sent.append(prepared.request.text)
        reattaches.append(asyncio.create_task(runtime.reattach("s1")))
        await until(lambda: runtime.restores.holds_sends("s1"))
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", dispatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        for text in ("one", "two"):
            await runtime.held.deliver("s1", peer_send(text), HeldMessageOrigin.AGENT)
        release.set()
        await until(lambda: sent == ["one"])
        await asyncio.sleep(0.4)

        assert sent == ["one"]
        assert held_texts(runtime.storage, "s1") == ["two"]
        await cancel_and_wait(*reattaches)


async def test_held_sends_follow_an_interrupted_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", status=SessionStatus.INTERRUPTED)
    release = asyncio.Event()
    stub_restores(monkeypatch, {"s1": gated_restore(release, noop)})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("held"), HeldMessageOrigin.AGENT)
        release.set()
        await until(lambda: sent == ["held"])


async def test_shutdown_delivers_nothing_as_a_cancelled_reconnect_unwinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    stub_restores(monkeypatch, {})
    monkeypatch.setattr(get_registry().get("codex"), "terminate_session", noop)
    sent = record_dispatches(runtime, monkeypatch)
    reconnects: list[asyncio.Task[SessionRecord]] = []
    stop_scheduler = runtime.scheduler.stop

    async def stop_slowly() -> None:
        # The server gives up on the request while delivery is still stopping.
        reconnects[0].cancel()
        await asyncio.sleep(0.2)
        await stop_scheduler()

    monkeypatch.setattr(runtime.scheduler, "stop", stop_slowly)

    async with started(runtime):
        await until(lambda: not runtime.restores.is_restoring("s1"))
        stub_restores(monkeypatch, {"s1": hang})
        reconnects.append(asyncio.create_task(runtime.reattach("s1")))
        await until(lambda: runtime.restores.is_restoring("s1"))
        await runtime.held.deliver("s1", peer_send("held"), HeldMessageOrigin.AGENT)

    assert sent == []
    assert held_texts(Storage(runtime.settings.database_path), "s1") == ["held"]


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

    reconnect = asyncio.create_task(runtime.reattach("s1"))
    await asyncio.wait_for(tearing_down.wait(), timeout=5)
    await runtime.delete("s1")
    torn_down.set()

    with pytest.raises(HTTPException) as gone:
        await reconnect
    assert gone.value.status_code == 404
    await asyncio.sleep(0.05)
    assert restored == []


def record_dispatches(
    runtime: SessionRuntime, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    sent: list[str] = []

    async def dispatch(prepared: PreparedInput) -> SessionRecord:
        sent.append(prepared.request.text)
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", dispatch)
    return sent


def gated_restore(release: asyncio.Event, outcome: Restore = restore_idle) -> Restore:
    async def restore(runtime: SessionRuntime, session: SessionRecord) -> None:
        await release.wait()
        await outcome(runtime, session)

    return restore


def peer_send(text: str) -> SessionInputRequest:
    return SessionInputRequest(text=text, sender_session_id="peer")


async def test_send_mid_restore_is_held_and_delivered_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    release = asyncio.Event()
    stub_restores(monkeypatch, {"s1": gated_restore(release)})
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

    async def fail(*_args: Any) -> None:
        raise RuntimeError("down")

    stub_restores(monkeypatch, {"s1": gated_restore(release, fail)})
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
        assert [m.text for m in runtime.storage.list_held_messages("s1")] == [
            "first",
            "second",
        ]

        stub_restores(monkeypatch, {})
        await runtime.reattach("s1")
        await until(lambda: sent == ["first", "second"])


def assistant_settings() -> dict[str, Any]:
    return {"assistant": AssistantConfig(backend="codex")}


def add_assistant(
    runtime: SessionRuntime,
    session_id: str = "assistant",
    *,
    transport: str = "codex_app_server",
    status: SessionStatus = SessionStatus.IDLE,
) -> None:
    add_session(
        runtime,
        session_id,
        transport=transport,
        status=status,
        cwd=str(runtime._assistant_workspace_dir()),
        source=SessionSource.ASSISTANT,
    )


async def test_assistant_creation_does_not_block_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.assistant_boot.LAUNCH_STOP_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, **assistant_settings())
    monkeypatch.setattr(runtime, "_create_assistant_session", hang)

    async with started(runtime):
        await until(lambda: runtime.assistant_boot._launching)
        for request in (
            runtime.reset_assistant(),
            runtime.reattach_assistant(),
            runtime.terminate_assistant(),
        ):
            with pytest.raises(HTTPException) as refused:
                await request
            assert refused.value.status_code == 409
            assert "still starting" in refused.value.detail


async def test_assistant_is_pinned_before_its_restore_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime)
    stub_restores(monkeypatch, {"assistant": hang})

    async with started(runtime):
        summary = runtime.assistant_summary()
        assert summary is not None
        assert summary.session_id == "assistant"
        await until(lambda: runtime.restores.is_restoring("assistant"))
        async with asyncio.timeout(2):
            summary = await runtime.terminate_assistant()
        assert summary.session_id == "assistant"
        assert summary.status == SessionStatus.EXITED


async def test_assistant_is_replaced_when_its_restore_ends_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime)
    release = asyncio.Event()

    async def exited(runtime: SessionRuntime, session: SessionRecord) -> None:
        await runtime._record_system_event(
            session.id, "No thread", status=SessionStatus.EXITED
        )

    stub_restores(monkeypatch, {"assistant": gated_restore(release, exited)})

    async def create(*_args: Any, **_kwargs: Any) -> SessionRecord:
        add_assistant(runtime, "fresh")
        return stored(runtime, "fresh")

    monkeypatch.setattr(runtime, "_create_assistant_session", create)

    async with started(runtime):
        assert runtime.assistant_session_id == "assistant"
        release.set()
        await until(lambda: runtime.assistant_session_id == "fresh")
        assert stored(runtime, "assistant").source == SessionSource.MANAGED


@pytest.mark.parametrize(
    ("transport", "replaced"), [("codex_app_server", False), ("tmux", True)]
)
async def test_errored_assistant_is_kept_unless_it_runs_in_a_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    replaced: bool,
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime, transport=transport, status=SessionStatus.ERROR)

    async def create(*_args: Any, **_kwargs: Any) -> SessionRecord:
        add_assistant(runtime, "fresh")
        return stored(runtime, "fresh")

    monkeypatch.setattr(runtime, "_create_assistant_session", create)

    await runtime._ensure_assistant_session()

    assert runtime.assistant_session_id == ("fresh" if replaced else "assistant")


async def test_the_thread_being_replaced_takes_no_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("waypoint.assistant_boot.LAUNCH_STOP_GRACE_SECONDS", 0.05)
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime, status=SessionStatus.EXITED)
    monkeypatch.setattr(runtime, "_create_assistant_session", hang)
    stub_restores(monkeypatch, {})
    sent = record_dispatches(runtime, monkeypatch)

    async with started(runtime):
        await until(lambda: runtime.assistant_boot._launching)
        with pytest.raises(HTTPException) as refused:
            await runtime.handle_input("assistant", SessionInputRequest(text="hi"))
        assert refused.value.status_code == 409
        assert "still starting" in refused.value.detail
        assert sent == []


async def test_shutdown_lets_a_pane_assistant_launch_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(
        tmp_path, assistant=AssistantConfig(backend="codex", transport="tmux")
    )
    launching = asyncio.Event()

    async def create(
        _runtime: SessionRuntime, request: Any, *, session_id: str, **_kwargs: Any
    ) -> SessionRecord:
        launching.set()
        await asyncio.sleep(0.2)
        return add_session(runtime, session_id, transport="tmux", cwd=request.cwd)

    monkeypatch.setattr(get_registry().for_transport("tmux"), "create_session", create)
    monkeypatch.setattr(
        get_registry().get("codex"),
        "validate_new_session_selection",
        None,
        raising=False,
    )

    async with started(runtime):
        await launching.wait()

    [session] = Storage(runtime.settings.database_path).list_sessions()
    assert session.source == SessionSource.ASSISTANT


async def test_failed_assistant_creation_keeps_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime, status=SessionStatus.EXITED)

    async def fail(*_args: Any, **_kwargs: Any) -> SessionRecord:
        raise RuntimeError("cannot launch")

    monkeypatch.setattr(runtime, "_create_assistant_session", fail)

    async with started(runtime):
        task = runtime.assistant_boot._task
        assert task is not None
        await asyncio.wait({task})
        assert runtime.assistant_session_id == "assistant"
        assert stored(runtime, "assistant").source == SessionSource.ASSISTANT


async def test_hung_paneless_launch_is_bounded_and_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)

    async def create(
        _runtime: SessionRuntime, _request: Any, *, session_id: str, **_kwargs: Any
    ) -> SessionRecord:
        add_session(runtime, session_id, status=SessionStatus.STARTING)
        return await hang()

    monkeypatch.setattr(get_registry().get("codex"), "create_session", create)
    monkeypatch.setattr(
        get_registry().get("codex"),
        "validate_new_session_selection",
        None,
        raising=False,
    )

    with pytest.raises(TimeoutError):
        await runtime.create_session(
            SessionCreateRequest(
                backend="codex", transport="codex_app_server", cwd=str(tmp_path)
            ),
            launch_timeout=0.1,
        )

    [session] = runtime.storage.list_sessions()
    assert session.status == SessionStatus.ERROR


@pytest.mark.parametrize("stage", ["setup", "transport_view"])
async def test_plugin_that_fails_to_wire_is_visibly_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
) -> None:
    def explode(*_args: Any) -> None:
        raise RuntimeError("bad wiring")

    monkeypatch.setattr(get_registry().get("codex"), stage, explode)
    caplog.set_level(logging.ERROR, logger="waypoint.runtime")
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")

    async with started(runtime):
        reason = f"backend plugin codex failed in {stage}: bad wiring"
        assert stored(runtime, "s1").status == SessionStatus.ERROR
        assert f"Session unavailable: {reason}" in notes(runtime, "s1")
        assert any(
            record.exc_info and f"codex failed in {stage}" in record.getMessage()
            for record in caplog.records
        )
        with pytest.raises(HTTPException) as refused:
            await runtime.handle_input("s1", SessionInputRequest(text="hi"))
        assert refused.value.status_code == 503
        assert reason in refused.value.detail
        with pytest.raises(HTTPException) as launch:
            await runtime.create_session(
                SessionCreateRequest(
                    backend="codex", transport="codex_app_server", cwd=str(tmp_path)
                )
            )
        assert launch.value.status_code == 503
        await runtime.terminate("s1")
        assert stored(runtime, "s1").status == SessionStatus.EXITED
        await runtime.delete("s1")


async def test_session_with_no_plugin_does_not_block_startup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="waypoint.runtime")
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime)
    # The row names a plugin that no longer loads.
    runtime.storage.connection.execute(
        "UPDATE sessions SET transport = 'gone_transport' WHERE id = 'assistant'"
    )
    runtime.storage.connection.commit()

    async with started(runtime):
        assert stored(runtime, "assistant").status == SessionStatus.ERROR
        assert "no backend plugin provides codex over gone_transport" in notes(
            runtime, "assistant"
        )
        assert any(
            record.exc_info and "gone_transport" in record.getMessage()
            for record in caplog.records
        )
        summary = runtime.assistant_summary()
        assert summary is not None
        assert not summary.supports_reattach


async def test_pane_session_recovers_once_its_plugin_is_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any) -> None:
        raise RuntimeError("bad wiring")

    stub_restores(monkeypatch, {"pane": noop})
    with monkeypatch.context() as broken:
        broken.setattr(get_registry().get("tmux"), "setup", explode)
        runtime = make_runtime(tmp_path)
        add_session(runtime, "pane", transport="tmux", status=SessionStatus.RUNNING)
        async with started(runtime):
            assert stored(runtime, "pane").status == SessionStatus.ERROR

    recovered = make_runtime(tmp_path)
    async with started(recovered):
        session = stored(recovered, "pane")
        assert session.status == SessionStatus.RUNNING
        assert "unavailable_mark" not in session.transport_state


async def test_unavailable_session_is_noted_once_and_stays_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any) -> None:
        raise RuntimeError("bad wiring")

    monkeypatch.setattr(get_registry().get("codex"), "setup", explode)
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    runtime.storage.update_session("s1", read_at=datetime.now(UTC))
    async with started(runtime):
        assert not stored(runtime, "s1").unread

    again = make_runtime(tmp_path)
    async with started(again):
        assert notes(again, "s1").count("Session unavailable") == 1
        assert not stored(again, "s1").unread


async def test_failed_plugin_shows_on_a_session_awaiting_its_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any) -> None:
        raise RuntimeError("bad wiring")

    monkeypatch.setattr(get_registry().get("codex"), "setup", explode)
    runtime = make_runtime(
        tmp_path,
        ssh_targets=[
            SshLaunchTargetConfig(
                id="pw",
                name="pw",
                ssh_destination="host",
                ssh_auth="password",
                ssh_args=[
                    "-oControlMaster=auto",
                    "-oControlPath=/tmp/wp-%C",
                    "-oControlPersist=60",
                ],
            )
        ],
    )
    monkeypatch.setattr(runtime.ssh_master, "disconnect_all", noop)
    add_session(runtime, "s1")
    runtime.storage.connection.execute(
        "UPDATE sessions SET launch_target_id = 'pw' WHERE id = 's1'"
    )
    runtime.storage.connection.commit()

    async with started(runtime):
        assert stored(runtime, "s1").status == SessionStatus.ERROR
        assert "Session unavailable" in notes(runtime, "s1")


async def test_nothing_lands_on_a_failed_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1", backend="claude_code", transport="claude_cli")

    def offered() -> set[str]:
        return {o.id for o in runtime.get_launch_settings("s1").transport_options}

    assert "tmux" in offered()
    runtime.availability.failures["tmux"] = "failed in setup: bad wiring"
    assert "tmux" not in offered()

    async def describe_target(_target: str) -> Any:
        return SimpleNamespace()

    monkeypatch.setattr(runtime.tmux, "describe_target", describe_target)
    for request in (
        runtime.import_thread("codex", {"thread_id": "t", "transport": "tmux"}),
        runtime.attach_tmux(
            SessionAttachRequest(tmux_target="main", backend_hint="codex")
        ),
    ):
        with pytest.raises(HTTPException) as refused:
            await request
        assert refused.value.status_code == 503
        assert "backend plugin tmux" in refused.value.detail


async def test_session_with_no_plugin_answers_without_failing(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    runtime.storage.connection.execute(
        "UPDATE sessions SET transport = 'gone_transport' WHERE id = 's1'"
    )
    runtime.storage.connection.commit()

    async with started(runtime):
        with pytest.raises(HTTPException) as settings:
            runtime.get_launch_settings("s1")
        assert settings.value.status_code == 503
        with pytest.raises(HTTPException) as clone:
            await runtime.clone_session_launch("s1")
        assert clone.value.status_code == 503
        assert await runtime.get_command_completions("s1") == ([], False)
        assert (await runtime.refresh_rate_limit_usage("s1")).id == "s1"


async def test_questions_of_a_failed_plugin_stay_in_the_feed(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    add_session(runtime, "s1")
    await runtime._emit_adapter_event(
        "s1",
        EventKind.TOOL_CALL,
        "AskUserQuestion",
        {
            "tool_name": "AskUserQuestion",
            "tool_use_id": "q1",
            "payload": {
                "input": {
                    "questions": [{"question": "Which?", "options": [{"label": "A"}]}]
                }
            },
        },
        SessionStatus.WAITING_INPUT,
    )
    runtime.availability.failures["codex"] = "failed in setup: bad wiring"

    [ask] = (await runtime.asks.snapshot()).items

    assert (ask.session_id, getattr(ask, "availability", None)) == (
        "s1",
        "unavailable",
    )


def test_routes_refuse_a_session_with_no_plugin(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data"))
    context = app.state.context
    add_session(context.runtime, "s1")
    context.runtime.storage.connection.execute(
        "UPDATE sessions SET transport = 'gone_transport' WHERE id = 's1'"
    )
    context.runtime.storage.connection.commit()
    token = context.tokens.issue().token

    with TestClient(app) as client:
        response = client.delete(
            "/api/sessions/s1/side-questions/q1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 503
        with client.websocket_connect(f"/ws/sessions/s1/terminal?token={token}") as ws:
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_text()
        assert closed.value.code == 4403


def test_plugin_that_fails_to_register_routes_does_not_stop_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any) -> None:
        raise RuntimeError("bad route")

    monkeypatch.setattr(get_registry().get("codex"), "register_routes", explode)

    app = create_app(Settings(data_dir=tmp_path / "data"))

    assert app.state.context.runtime.availability.failures == {
        "codex": "failed in register_routes: bad route"
    }


async def test_terminating_an_unavailable_session_outlives_the_plugin_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any) -> None:
        raise RuntimeError("bad wiring")

    stub_restores(monkeypatch, {})
    with monkeypatch.context() as broken:
        broken.setattr(get_registry().get("tmux"), "setup", explode)
        broken.setattr(get_registry().get("tmux"), "terminate_session", noop)
        runtime = make_runtime(tmp_path)
        add_session(runtime, "pane", transport="tmux", status=SessionStatus.RUNNING)
        async with started(runtime):
            await runtime.terminate("pane")

    recovered = make_runtime(tmp_path)
    async with started(recovered):
        session = stored(recovered, "pane")
        assert session.status == SessionStatus.EXITED
        assert "unavailable_mark" not in session.transport_state
