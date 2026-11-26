"""Shared setup for the startup, restore and plugin availability tests."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from waypoint.backends import get_registry
from waypoint.runtime import PreparedInput, SessionRuntime
from waypoint.schemas import (
    EventKind,
    LaunchSettingsUpdateRequest,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import AssistantConfig, Settings
from waypoint.storage import Storage

# Longer than the broadcast debounce, which releases held sends.
RELEASE_SETTLE_SECONDS = 0.4

type Restore = Callable[[SessionRuntime, SessionRecord], Awaitable[None]]


def make_runtime(tmp_path: Path, **overrides: Any) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data", **overrides)
    settings.ensure_dirs()
    return SessionRuntime(settings, Storage(settings.database_path))


@asynccontextmanager
async def started(runtime: SessionRuntime) -> AsyncIterator[SessionRuntime]:
    async with asyncio.timeout(5):
        await runtime.start()
    try:
        yield runtime
    finally:
        await runtime.stop()


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


def set_column(
    runtime: SessionRuntime, session_id: str, column: str, value: str
) -> None:
    """Store a value the runtime itself would refuse to write."""
    runtime.storage.connection.execute(
        f"UPDATE sessions SET {column} = ? WHERE id = ?", (value, session_id)
    )
    runtime.storage.connection.commit()


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


def held_texts(storage: Storage, session_id: str) -> list[str]:
    return [message.text for message in storage.list_held_messages(session_id)]


async def until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def assert_refused(
    request: Awaitable[Any], status_code: int, detail: str = ""
) -> None:
    with pytest.raises(HTTPException) as refused:
        await request
    assert refused.value.status_code == status_code
    assert detail in refused.value.detail


async def hang(*_args: Any, **_kwargs: Any) -> Any:
    await asyncio.Event().wait()


async def noop(*_args: Any) -> None:
    return None


def raising(message: str) -> Callable[..., Any]:
    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(message)

    return explode


def raising_async(message: str) -> Callable[..., Awaitable[Any]]:
    async def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(message)

    return explode


def ignoring_cancellation(entered: asyncio.Event, release: asyncio.Event) -> Restore:
    """A restore that only ``release`` ends."""

    async def restore(*_args: Any) -> None:
        entered.set()
        while not release.is_set():
            with suppress(asyncio.CancelledError):
                await release.wait()

    return restore


async def restore_idle(runtime: SessionRuntime, session: SessionRecord) -> None:
    await runtime._record_system_event(
        session.id, "Restored", status=SessionStatus.IDLE
    )


def gated_restore(release: asyncio.Event, outcome: Restore = restore_idle) -> Restore:
    async def restore(runtime: SessionRuntime, session: SessionRecord) -> None:
        await release.wait()
        await outcome(runtime, session)

    return restore


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


def stub_launch(
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    create: Callable[..., Awaitable[SessionRecord]],
) -> None:
    """Launch codex sessions over ``transport`` with ``create``."""
    monkeypatch.setattr(
        get_registry().for_transport(transport), "create_session", create
    )
    monkeypatch.setattr(
        get_registry().get("codex"),
        "validate_new_session_selection",
        None,
        raising=False,
    )


def peer_send(text: str) -> SessionInputRequest:
    return SessionInputRequest(text=text, sender_session_id="peer")


def record_dispatches(
    runtime: SessionRuntime, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    sent: list[str] = []

    async def dispatch(prepared: PreparedInput) -> SessionRecord:
        sent.append(prepared.request.text)
        return prepared.session

    monkeypatch.setattr(runtime, "dispatch_input", dispatch)
    return sent


async def reconnect(runtime: SessionRuntime) -> SessionRecord:
    return await runtime.reattach("s1")


async def change_settings(runtime: SessionRuntime) -> SessionRecord:
    return await runtime.update_launch_settings(
        "s1", LaunchSettingsUpdateRequest(args=["--verbose"], restart=True)
    )
