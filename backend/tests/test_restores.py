"""Startup and restore: no agent plugin keeps the backend from starting or serving."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from waypoint.api import create_app
from waypoint.backends import get_registry
from waypoint.runtime import PreparedInput, SessionRuntime
from waypoint.schemas import (
    EventKind,
    HeldMessageOrigin,
    HeldReason,
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

    async with started(runtime):
        await until(lambda: runtime.restores.is_restoring("s1"))
        async with asyncio.timeout(2):
            await runtime.terminate("s1")
        assert not runtime.restores.is_restoring("s1")
        await asyncio.sleep(0.05)
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
        assert held.hold_reason is HeldReason.IDLE
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
    runtime = make_runtime(tmp_path, **assistant_settings())
    monkeypatch.setattr(runtime, "_create_assistant_session", hang)

    async with started(runtime):
        await until(lambda: runtime._assistant_creating)
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


async def test_failed_assistant_creation_keeps_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime(tmp_path, **assistant_settings())
    add_assistant(runtime, status=SessionStatus.EXITED)

    async def fail(*_args: Any, **_kwargs: Any) -> SessionRecord:
        raise RuntimeError("cannot launch")

    monkeypatch.setattr(runtime, "_create_assistant_session", fail)

    async with started(runtime):
        task = runtime._assistant_bootstrap_task
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
            driver_timeout=0.1,
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
        assert "status_before_unavailable" not in session.transport_state


def test_plugin_that_fails_to_register_routes_does_not_stop_the_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: Any) -> None:
        raise RuntimeError("bad route")

    monkeypatch.setattr(get_registry().get("codex"), "register_routes", explode)

    app = create_app(Settings(data_dir=tmp_path / "data"))

    assert app.state.context.runtime.plugin_failures == {
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
        assert "status_before_unavailable" not in session.transport_state
