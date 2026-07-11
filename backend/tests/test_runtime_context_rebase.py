"""Runtime dispatch of the context-usage rebase hook.

Exercises ``SessionRuntime._rebase_context_usage``: it dispatches on the agent
plugin (so one implementation covers every Claude transport), is idempotent when
the window is unchanged, and no-ops for agents that don't implement the hook.
"""

from datetime import UTC, datetime

import pytest

from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    SessionContextUsage,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _runtime(tmp_path) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage), storage


def _get(storage: Storage, sid: str) -> SessionRecord:
    session = storage.get_session(sid)
    assert session is not None
    return session


def _window(storage: Storage, sid: str) -> int | None:
    usage = _get(storage, sid).context_usage
    assert usage is not None
    return usage.context_window_tokens


def _snapshot(window: int | None) -> SessionContextUsage:
    return SessionContextUsage(
        used_tokens=999,
        context_window_tokens=window,
        updated_at=datetime.now(UTC),
        source="claude_code",
        breakdown={"input_tokens": 999},
    )


def _insert(
    storage: Storage,
    *,
    backend: str,
    transport: str,
    model: str | None,
    window: int | None,
) -> str:
    now = datetime.now(UTC)
    session = SessionRecord(
        id=f"{transport}-1",
        backend=backend,
        source=SessionSource.MANAGED,
        transport=transport,
        title="t",
        cwd="/tmp",
        status=SessionStatus.IDLE,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
        model=model,
        context_usage=_snapshot(window),
    )
    storage.create_session(session)
    return session.id


@pytest.mark.asyncio
async def test_rebase_promotes_window_for_claude_tty_session(tmp_path) -> None:
    runtime, storage = _runtime(tmp_path)
    sid = _insert(
        storage,
        backend="claude_code",
        transport="claude_tty",
        model="opus",
        window=200_000,
    )
    await runtime._rebase_context_usage(sid, model="opus[1m]")
    assert _window(storage, sid) == 1_000_000


@pytest.mark.asyncio
async def test_rebase_is_idempotent_when_window_unchanged(tmp_path) -> None:
    runtime, storage = _runtime(tmp_path)
    sid = _insert(
        storage,
        backend="claude_code",
        transport="claude_cli",
        model="opus[1m]",
        window=1_000_000,
    )
    before = _get(storage, sid).updated_at
    await runtime._rebase_context_usage(sid)
    after = _get(storage, sid)
    # No storage write when the window already matches.
    assert after.updated_at == before
    assert _window(storage, sid) == 1_000_000


@pytest.mark.asyncio
async def test_rebase_clears_window_for_legacy_model_less_session(tmp_path) -> None:
    runtime, storage = _runtime(tmp_path)
    sid = _insert(
        storage,
        backend="claude_code",
        transport="claude_tty",
        model=None,
        window=200_000,
    )
    await runtime._rebase_context_usage(sid)
    assert _window(storage, sid) is None


@pytest.mark.asyncio
async def test_rebase_noop_for_non_rebasing_agent(tmp_path) -> None:
    runtime, storage = _runtime(tmp_path)
    sid = _insert(
        storage,
        backend="codex",
        transport="codex_app_server",
        model="gpt-5",
        window=200_000,
    )
    await runtime._rebase_context_usage(sid, model="something")
    # Codex doesn't implement ContextUsageRebasing → untouched.
    assert _window(storage, sid) == 200_000
