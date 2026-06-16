"""Unit tests for the ContextUsageSource lifecycle wiring in SessionRuntime.

Verifies that:
- A plugin returning a non-None source has it started as a tracked asyncio task
  when the active transport has is_structured=False.
- A plugin returning a non-None source has nothing started when the active
  transport has is_structured=True.
- The task is cancelled by _cancel_context_usage_source.
- A plugin returning None from the hook starts no task.
- A source is cancelled when the session transitions to EXITED/ERROR via
  _record_system_event (natural tmux exits).
- A source is cancelled by delete() even when the session is already EXITED.
"""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from waypoint.backends.context_usage_source import ContextUsageSource
from waypoint.runtime import SessionRuntime
from waypoint.schemas import SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings
from waypoint.storage import Storage

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _RunningSource(ContextUsageSource):
    """Blocks forever until cancelled; records that it was started."""

    started = False

    async def run(self) -> None:
        _RunningSource.started = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise


class _FakeTransport:
    def __init__(self, *, is_structured: bool) -> None:
        self.is_structured = is_structured


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(tmp_path: Path) -> SessionRecord:
    now = datetime.now(UTC)
    raw = tmp_path / "raw.log"
    raw.touch()
    # Use "tmux" as backend/transport — always registered in the default registry.
    return SessionRecord(
        id="sess-1",
        backend="tmux",
        source=SessionSource.MANAGED,
        transport="tmux",
        title="t",
        cwd=str(tmp_path),
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path=str(raw),
        structured_log_path=str(tmp_path / "events.jsonl"),
    )


def _make_runtime(
    tmp_path: Path,
    source: ContextUsageSource | None,
    *,
    is_structured: bool,
) -> SessionRuntime:
    """Create a SessionRuntime with a fully isolated mock registry and transport."""
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)

    plugin = MagicMock()
    plugin.create_context_usage_source.return_value = source

    registry = MagicMock()
    registry.all.return_value = []  # prevents _transports from calling real plugins
    registry.plugin_for.return_value = plugin

    runtime = SessionRuntime(settings, storage, registry=registry)
    # Inject the fake transport for the session's transport id.
    runtime._transports["tmux"] = _FakeTransport(is_structured=is_structured)  # type: ignore[assignment]
    return runtime


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_source_started_for_unstructured_transport(tmp_path: Path) -> None:
    """A plugin returning a source has it started when transport.is_structured=False."""
    _RunningSource.started = False
    source = _RunningSource()

    runtime = _make_runtime(tmp_path, source, is_structured=False)
    session = _make_session(tmp_path)
    runtime.storage.create_session(session)

    runtime._start_context_usage_source(session)

    assert session.id in runtime._context_usage_sources
    task = runtime._context_usage_sources[session.id]
    assert not task.done()
    await asyncio.sleep(0)
    assert _RunningSource.started

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def test_source_not_started_for_structured_transport(tmp_path: Path) -> None:
    """A plugin returning a source has nothing started when transport.is_structured=True."""
    source = _RunningSource()

    runtime = _make_runtime(tmp_path, source, is_structured=True)
    session = _make_session(tmp_path)
    runtime.storage.create_session(session)

    runtime._start_context_usage_source(session)

    assert session.id not in runtime._context_usage_sources


async def test_source_cancelled_on_cancel(tmp_path: Path) -> None:
    """_cancel_context_usage_source cancels and removes the task."""
    source = _RunningSource()

    runtime = _make_runtime(tmp_path, source, is_structured=False)
    session = _make_session(tmp_path)
    runtime.storage.create_session(session)

    runtime._start_context_usage_source(session)
    assert session.id in runtime._context_usage_sources

    await runtime._cancel_context_usage_source(session.id)

    assert session.id not in runtime._context_usage_sources


async def test_none_source_starts_nothing(tmp_path: Path) -> None:
    """A plugin returning None from create_context_usage_source starts no task."""
    runtime = _make_runtime(tmp_path, None, is_structured=False)
    session = _make_session(tmp_path)
    runtime.storage.create_session(session)

    runtime._start_context_usage_source(session)

    assert session.id not in runtime._context_usage_sources


async def test_source_cancelled_on_natural_exit_via_record_system_event(
    tmp_path: Path,
) -> None:
    """Natural exits (tmux pane dead, lost target, monitor failure) route through
    _record_system_event; the source must be cancelled there, not only on terminate().
    """
    source = _RunningSource()

    runtime = _make_runtime(tmp_path, source, is_structured=False)
    session = _make_session(tmp_path)
    runtime.storage.create_session(session)
    runtime._start_context_usage_source(session)
    assert session.id in runtime._context_usage_sources

    await runtime._record_system_event(
        session.id, "tmux pane closed", status=SessionStatus.EXITED
    )

    assert session.id not in runtime._context_usage_sources


async def test_source_cancelled_on_delete_already_exited(tmp_path: Path) -> None:
    """delete() of an already-EXITED session must cancel any lingering source
    since terminate() is skipped for EXITED sessions."""
    source = _RunningSource()

    runtime = _make_runtime(tmp_path, source, is_structured=False)
    session = _make_session(tmp_path)
    runtime.storage.create_session(session)

    # Manually start the source and mark the session EXITED without routing
    # through _record_system_event, simulating a source that leaked.
    runtime._start_context_usage_source(session)
    runtime.storage.update_session(session.id, status=SessionStatus.EXITED)
    assert session.id in runtime._context_usage_sources

    await runtime.delete(session.id)

    assert session.id not in runtime._context_usage_sources
