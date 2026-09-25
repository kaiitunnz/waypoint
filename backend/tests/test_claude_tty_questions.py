import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.runtime import SessionRuntime
from waypoint.schemas import EventKind, SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings
from waypoint.storage import Storage


def make_runtime(tmp_path: Path) -> SessionRuntime:
    settings = Settings(data_dir=tmp_path / "data")
    settings.ensure_dirs()
    runtime = SessionRuntime(settings, Storage(settings.database_path))
    session_dir = settings.sessions_dir / "s1"
    session_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    runtime.storage.create_session(
        SessionRecord(
            id="s1",
            backend="claude_code",
            source=SessionSource.MANAGED,
            transport="claude_tty",
            title="tty",
            cwd="/tmp",
            status=SessionStatus.WAITING_INPUT,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
        )
    )
    return runtime


def fake_transport(runtime: SessionRuntime, monkeypatch) -> MagicMock:
    transport = MagicMock()
    transport.send_input = AsyncMock()
    monkeypatch.setattr(runtime, "transport_for", lambda session: transport)
    return transport


async def ask(runtime: SessionRuntime, tool_use_id: str) -> None:
    await runtime._emit_adapter_event(
        "s1",
        EventKind.TOOL_CALL,
        "AskUserQuestion",
        {"tool_name": "AskUserQuestion", "tool_use_id": tool_use_id},
        SessionStatus.WAITING_INPUT,
    )


async def answer(
    plugin: ClaudeTtyPlugin, runtime: SessionRuntime, tool_use_id: str | None
) -> None:
    await plugin.answer_question(
        runtime, runtime.get_session("s1"), '"Q"="A"', tool_use_id, None
    )


async def test_open_questions_follow_the_transcript_rule(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    for tool_use_id in ("answered", "closed", "open"):
        await ask(runtime, tool_use_id)
    await runtime._record_user_event(
        "s1",
        "a",
        submit=True,
        extra_metadata={"kind": "ask_user_question_answer", "tool_use_id": "answered"},
    )
    await runtime._emit_adapter_event(
        "s1",
        EventKind.TOOL_RESULT,
        "InputValidationError",
        {"tool_use_id": "closed", "is_error": True},
        SessionStatus.RUNNING,
    )

    assert runtime.storage.open_question_tool_use_ids("s1") == ["open"]
    assert runtime.storage.open_question_tool_use_ids("other") == []


async def test_open_questions_lookup_is_index_driven(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    await ask(runtime, "q1")
    connection = runtime.storage.connection
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    try:
        runtime.storage.open_question_tool_use_ids("s1")
    finally:
        connection.set_trace_callback(None)
    (query,) = [sql for sql in statements if "AskUserQuestion" in sql]

    plan = " | ".join(
        row["detail"] for row in connection.execute(f"EXPLAIN QUERY PLAN {query}")
    )

    # A per-question scan of the session's events made answers take seconds.
    assert "USING INDEX idx_events_tool_name" in plan
    assert "USING INDEX idx_events_tool_use_id" in plan
    assert "idx_events_session_seq" not in plan
    assert "TEMP B-TREE" not in plan


async def test_older_question_stays_answerable_after_a_newer_one(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    plugin = ClaudeTtyPlugin()
    await ask(runtime, "q1")
    await ask(runtime, "q2")

    await answer(plugin, runtime, "q1")
    assert runtime.storage.open_question_tool_use_ids("s1") == ["q2"]
    await answer(plugin, runtime, "q2")

    assert runtime.storage.open_question_tool_use_ids("s1") == []
    assert transport.send_input.await_count == 2


async def test_answer_survives_a_fresh_plugin(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    await ask(runtime, "q1")

    await answer(ClaudeTtyPlugin(), runtime, "q1")

    events = runtime.storage.list_events("s1")
    assert events[-1].kind is EventKind.TOOL_RESULT
    assert events[-1].metadata["tool_use_id"] == "q1"
    assert events[-2].metadata["kind"] == "ask_user_question_answer"


async def test_answer_without_id_targets_latest_open(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    plugin = ClaudeTtyPlugin()
    await ask(runtime, "q1")
    await ask(runtime, "q2")

    await answer(plugin, runtime, None)

    assert runtime.storage.open_question_tool_use_ids("s1") == ["q1"]


@pytest.mark.parametrize("tool_use_id", [None, "unknown"])
async def test_answer_rejects_without_an_open_question(
    tmp_path, monkeypatch, tool_use_id
) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await answer(ClaudeTtyPlugin(), runtime, tool_use_id)

    assert exc.value.status_code == 400
    transport.send_input.assert_not_called()


async def test_failed_send_keeps_the_question_open(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    transport.send_input.side_effect = HTTPException(status_code=400, detail="x")
    plugin = ClaudeTtyPlugin()
    await ask(runtime, "q1")

    with pytest.raises(HTTPException):
        await answer(plugin, runtime, "q1")

    assert runtime.storage.open_question_tool_use_ids("s1") == ["q1"]
    transport.send_input.side_effect = None
    await answer(plugin, runtime, "q1")
    assert runtime.storage.open_question_tool_use_ids("s1") == []


async def test_concurrent_answers_send_once(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    gate = asyncio.Event()

    async def slow_send(*args, **kwargs) -> None:
        await gate.wait()

    transport.send_input.side_effect = slow_send
    plugin = ClaudeTtyPlugin()
    await ask(runtime, "q1")

    first = asyncio.create_task(answer(plugin, runtime, "q1"))
    await asyncio.sleep(0)
    with pytest.raises(HTTPException) as exc:
        await answer(plugin, runtime, "q1")
    gate.set()
    await first

    assert exc.value.status_code == 409
    assert transport.send_input.await_count == 1
