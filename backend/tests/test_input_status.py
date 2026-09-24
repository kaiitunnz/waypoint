import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from waypoint.backends.approvals import open_approval_requests
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


def make_runtime(tmp_path: Path, status: SessionStatus) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    runtime = SessionRuntime(settings, Storage(settings.database_path))
    session_dir = settings.sessions_dir / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    runtime.storage.create_session(
        SessionRecord(
            id="s1",
            backend="codex",
            source=SessionSource.MANAGED,
            transport="codex_app_server",
            title="s",
            cwd="/tmp",
            status=status,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
        )
    )
    return runtime


def fake_transport(runtime: SessionRuntime, monkeypatch, pending: bool) -> MagicMock:
    transport = MagicMock()
    transport.is_structured = False
    transport.send_input = AsyncMock()
    transport.input_blocked = AsyncMock(return_value=False)
    transport.has_pending_approval.return_value = pending
    monkeypatch.setattr(runtime, "transport_for", lambda session: transport)
    return transport


async def test_statusless_adapter_event_keeps_stored_status(tmp_path) -> None:
    runtime = make_runtime(tmp_path, SessionStatus.WAITING_INPUT)

    await runtime._emit_adapter_event("s1", EventKind.SYSTEM_NOTE, "note", {}, None)

    assert runtime.get_session("s1").status is SessionStatus.WAITING_INPUT
    assert "status" not in runtime.storage.list_events("s1")[-1].metadata


@pytest.mark.parametrize(
    ("status", "pending", "expected"),
    [
        (SessionStatus.WAITING_INPUT, True, SessionStatus.WAITING_INPUT),
        (SessionStatus.WAITING_INPUT, False, SessionStatus.RUNNING),
        (SessionStatus.IDLE, False, SessionStatus.RUNNING),
    ],
)
async def test_input_queued_behind_pending_approval_keeps_waiting(
    tmp_path, monkeypatch, status, pending, expected
) -> None:
    runtime = make_runtime(tmp_path, status)
    fake_transport(runtime, monkeypatch, pending)

    await runtime.handle_input("s1", SessionInputRequest(text="hi"))

    assert runtime.get_session("s1").status is expected
    assert runtime.storage.list_events("s1")[-1].metadata["status"] == expected


async def test_open_approval_requests_follow_the_pager_rule(tmp_path) -> None:
    runtime = make_runtime(tmp_path, SessionStatus.WAITING_INPUT)
    for approval_id in ("answered", "invalidated", "idless", "open"):
        await runtime._emit_adapter_event(
            "s1",
            EventKind.APPROVAL_REQUEST,
            "Approve",
            {"approval_id": approval_id},
            SessionStatus.WAITING_INPUT,
        )
    await runtime._record_system_event(
        "s1", "Approval response sent: approve", metadata={"approval_id": "answered"}
    )
    await runtime._record_system_event(
        "s1",
        "Pending approval expired",
        metadata={"method": "approval.invalidated", "approval_id": "invalidated"},
    )
    await runtime._record_system_event("s1", "Approval timed out")
    await runtime._record_system_event("s1", "Unrelated note")

    events = runtime.storage.list_approval_events("s1")

    assert [e.metadata["approval_id"] for e in open_approval_requests(events)] == [
        "open"
    ]


async def test_sends_into_one_session_do_not_overlap(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path, SessionStatus.IDLE)
    transport = fake_transport(runtime, monkeypatch, pending=False)
    active: list[int] = []
    overlap: list[bool] = []

    async def slow_send(*args, **kwargs) -> None:
        overlap.append(bool(active))
        active.append(1)
        await asyncio.sleep(0.01)
        active.pop()

    transport.send_input.side_effect = slow_send

    await asyncio.gather(
        runtime.handle_input("s1", SessionInputRequest(text="a")),
        runtime.handle_input("s1", SessionInputRequest(text="b")),
    )

    assert overlap == [False, False]
