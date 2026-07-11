"""Tests for ``runtime.run_oneshot`` (CONTRACT-NL.md §2/§6).

Exercises the orchestration ``run_oneshot`` owns — await-turn-completion,
``finally`` teardown, the timeout bound, and the boot orphan sweep — by
stubbing the pieces it calls (``create_session``/``handle_input``/
``terminate``/``delete``) rather than driving a real backend plugin/CLI.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from waypoint import runtime as runtime_module
from waypoint.runtime import SessionRuntime, _assemble_agent_reply
from waypoint.schemas import (
    EventKind,
    EventRecord,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def _make_runtime(tmp_path: Path) -> tuple[SessionRuntime, Storage]:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    storage = Storage(settings.database_path)
    return SessionRuntime(settings, storage), storage


def _session_record(
    session_id: str,
    *,
    source: SessionSource = SessionSource.MANAGED,
    status: SessionStatus = SessionStatus.IDLE,
) -> SessionRecord:
    now = datetime.now(UTC)
    return SessionRecord(
        id=session_id,
        backend="codex",
        source=source,
        transport="tmux",
        title="t",
        cwd="/tmp",
        status=status,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        raw_log_path="/tmp/raw.log",
        structured_log_path="/tmp/events.jsonl",
    )


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real cadence (0.5s) would make the timeout test slow; the
    # orchestration logic under test doesn't depend on the exact interval.
    monkeypatch.setattr(runtime_module, "ONE_SHOT_POLL_INTERVAL_SECONDS", 0.01)


def test_assemble_agent_reply_concatenates_deltas_and_skips_reasoning() -> None:
    now = datetime.now(UTC)
    events = [
        EventRecord(
            session_id="s",
            ts=now,
            kind=EventKind.AGENT_OUTPUT,
            text="thinking...",
            metadata={"item_id": "r1", "item_kind": "reasoning"},
            sequence=1,
        ),
        EventRecord(
            session_id="s",
            ts=now,
            kind=EventKind.AGENT_OUTPUT,
            text="Hello ",
            metadata={"item_id": "m1"},
            sequence=2,
        ),
        EventRecord(
            session_id="s",
            ts=now,
            kind=EventKind.AGENT_OUTPUT,
            text="world",
            metadata={"item_id": "m1"},
            sequence=3,
        ),
        EventRecord(
            session_id="s",
            ts=now,
            kind=EventKind.TOOL_CALL,
            text="ignored",
            metadata={},
            sequence=4,
        ),
    ]
    assert _assemble_agent_reply(events) == "Hello world"


async def test_run_oneshot_returns_assembled_reply_and_tears_down(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)
    created_ids: list[str] = []
    torn_down: list[tuple[str, str]] = []

    async def fake_create_session(request: Any) -> SessionRecord:
        session = _session_record(f"{request.backend}-oneshot")
        storage.create_session(session)
        created_ids.append(session.id)
        return session

    async def fake_handle_input(session_id: str, request: Any) -> SessionRecord:
        storage.append_event(
            EventRecord(
                session_id=session_id,
                ts=datetime.now(UTC),
                kind=EventKind.AGENT_OUTPUT,
                text="Hello ",
                metadata={"item_id": "m1"},
                sequence=1,
            )
        )
        storage.append_event(
            EventRecord(
                session_id=session_id,
                ts=datetime.now(UTC),
                kind=EventKind.AGENT_OUTPUT,
                text="world",
                metadata={"item_id": "m1"},
                sequence=2,
            )
        )
        return storage.update_session(session_id, status=SessionStatus.IDLE)

    async def fake_terminate(session_id: str, **_kwargs: Any) -> None:
        torn_down.append(("terminate", session_id))

    async def fake_delete(session_id: str, **_kwargs: Any) -> None:
        torn_down.append(("delete", session_id))
        storage.delete_session(session_id)

    runtime.create_session = fake_create_session  # type: ignore[method-assign, assignment]
    runtime.handle_input = fake_handle_input  # type: ignore[method-assign]
    runtime.terminate = fake_terminate  # type: ignore[method-assign, assignment]
    runtime.delete = fake_delete  # type: ignore[method-assign]

    reply = await runtime.run_oneshot(
        backend="codex",
        transport=None,
        model=None,
        account_profile=None,
        prompt="hi",
        timeout_s=5,
    )

    assert reply == "Hello world"
    assert created_ids
    session_id = created_ids[0]
    # Relabeled to the dedicated source before teardown.
    assert torn_down == [("terminate", session_id), ("delete", session_id)]
    assert storage.get_session(session_id) is None


async def test_run_oneshot_relabels_session_to_telemetry_source(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)

    async def fake_create_session(request: Any) -> SessionRecord:
        session = _session_record("codex-oneshot")
        storage.create_session(session)
        return session

    seen_source_at_input: list[SessionSource] = []

    async def fake_handle_input(session_id: str, request: Any) -> SessionRecord:
        current = storage.get_session(session_id)
        assert current is not None
        seen_source_at_input.append(current.source)
        return storage.update_session(session_id, status=SessionStatus.IDLE)

    async def fake_terminate(session_id: str, **_kwargs: Any) -> None:
        pass

    async def fake_delete(session_id: str, **_kwargs: Any) -> None:
        storage.delete_session(session_id)

    runtime.create_session = fake_create_session  # type: ignore[method-assign, assignment]
    runtime.handle_input = fake_handle_input  # type: ignore[method-assign]
    runtime.terminate = fake_terminate  # type: ignore[method-assign, assignment]
    runtime.delete = fake_delete  # type: ignore[method-assign]

    await runtime.run_oneshot(
        backend="codex",
        transport=None,
        model=None,
        account_profile=None,
        prompt="hi",
        timeout_s=5,
    )

    assert seen_source_at_input == [SessionSource.TELEMETRY]


async def test_run_oneshot_times_out_and_still_tears_down(tmp_path: Path) -> None:
    runtime, storage = _make_runtime(tmp_path)
    torn_down: list[str] = []

    async def fake_create_session(request: Any) -> SessionRecord:
        session = _session_record("stuck-oneshot")
        storage.create_session(session)
        return session

    async def fake_handle_input(session_id: str, request: Any) -> SessionRecord:
        # Flips to RUNNING and never settles — simulates a hung turn.
        return storage.update_session(session_id, status=SessionStatus.RUNNING)

    async def fake_terminate(session_id: str, **_kwargs: Any) -> None:
        torn_down.append(f"terminate:{session_id}")

    async def fake_delete(session_id: str, **_kwargs: Any) -> None:
        torn_down.append(f"delete:{session_id}")

    runtime.create_session = fake_create_session  # type: ignore[method-assign, assignment]
    runtime.handle_input = fake_handle_input  # type: ignore[method-assign]
    runtime.terminate = fake_terminate  # type: ignore[method-assign, assignment]
    runtime.delete = fake_delete  # type: ignore[method-assign]

    reply = await runtime.run_oneshot(
        backend="codex",
        transport=None,
        model=None,
        account_profile=None,
        prompt="hi",
        timeout_s=0.05,
    )

    assert reply is None
    assert torn_down == ["terminate:stuck-oneshot", "delete:stuck-oneshot"]


async def test_run_oneshot_returns_none_on_backend_error_status(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)

    async def fake_create_session(request: Any) -> SessionRecord:
        session = _session_record("erroring-oneshot")
        storage.create_session(session)
        return session

    async def fake_handle_input(session_id: str, request: Any) -> SessionRecord:
        return storage.update_session(session_id, status=SessionStatus.ERROR)

    async def fake_terminate(session_id: str, **_kwargs: Any) -> None:
        pass

    async def fake_delete(session_id: str, **_kwargs: Any) -> None:
        storage.delete_session(session_id)

    runtime.create_session = fake_create_session  # type: ignore[method-assign, assignment]
    runtime.handle_input = fake_handle_input  # type: ignore[method-assign]
    runtime.terminate = fake_terminate  # type: ignore[method-assign, assignment]
    runtime.delete = fake_delete  # type: ignore[method-assign]

    reply = await runtime.run_oneshot(
        backend="codex",
        transport=None,
        model=None,
        account_profile=None,
        prompt="hi",
        timeout_s=5,
    )
    assert reply is None


async def test_sweep_orphaned_oneshot_sessions_reaps_only_telemetry_source(
    tmp_path: Path,
) -> None:
    runtime, storage = _make_runtime(tmp_path)
    storage.create_session(
        _session_record(
            "orphan-1", source=SessionSource.TELEMETRY, status=SessionStatus.RUNNING
        )
    )
    storage.create_session(
        _session_record(
            "normal-1", source=SessionSource.MANAGED, status=SessionStatus.IDLE
        )
    )

    deleted: list[str] = []

    async def fake_terminate(session_id: str, **_kwargs: Any) -> None:
        pass

    async def fake_delete(session_id: str, **_kwargs: Any) -> None:
        deleted.append(session_id)
        storage.delete_session(session_id)

    runtime.terminate = fake_terminate  # type: ignore[method-assign, assignment]
    runtime.delete = fake_delete  # type: ignore[method-assign]

    await runtime._sweep_orphaned_oneshot_sessions()

    assert deleted == ["orphan-1"]
    assert storage.get_session("normal-1") is not None
    assert storage.get_session("orphan-1") is None
