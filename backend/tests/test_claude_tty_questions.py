import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin
from waypoint.questions import QuestionLiveness
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    INPUT_ORIGIN_KEY,
    EventKind,
    HeldMessageOrigin,
    SessionInputRequest,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
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


async def cancel(runtime: SessionRuntime, tool_use_id: str) -> SessionRecord:
    return await runtime.cancel_question("s1", tool_use_id)


async def test_cancel_closes_only_the_proxy_without_touching_the_pane(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    transport.has_pending_approval = MagicMock(return_value=False)
    await ask(runtime, "q1")
    await ask(runtime, "q2")

    updated = await cancel(runtime, "q1")

    assert runtime.storage.open_question_tool_use_ids("s1") == ["q2"]
    transport.send_input.assert_not_called()
    note = runtime.storage.list_events("s1")[-1]
    assert note.kind is EventKind.SYSTEM_NOTE
    assert note.metadata["kind"] == "ask_user_question_cancelled"
    assert note.metadata["tool_use_id"] == "q1"
    # Another question is still waiting on the human.
    assert updated.status is SessionStatus.WAITING_INPUT


async def test_cancelling_the_last_question_marks_the_idle_tui_idle(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    transport.has_pending_approval = MagicMock(return_value=False)
    await ask(runtime, "q1")

    updated = await cancel(runtime, "q1")

    assert updated.status is SessionStatus.IDLE


async def test_cancel_keeps_waiting_while_an_approval_is_pending(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    transport.has_pending_approval = MagicMock(return_value=True)
    await ask(runtime, "q1")

    updated = await cancel(runtime, "q1")

    assert updated.status is SessionStatus.WAITING_INPUT


async def test_cancel_rejects_a_resolved_question(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    plugin = ClaudeTtyPlugin()
    await ask(runtime, "q1")
    await answer(plugin, runtime, "q1")

    with pytest.raises(HTTPException) as exc:
        await cancel(runtime, "q1")

    assert exc.value.status_code == 400
    assert not any(
        event.metadata.get("kind") == "ask_user_question_cancelled"
        for event in runtime.storage.list_events("s1")
    )


async def test_cancel_is_unavailable_while_the_pane_is_detached(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    await ask(runtime, "q1")
    runtime.storage.update_session("s1", status=SessionStatus.EXITED)

    with pytest.raises(HTTPException) as exc:
        await cancel(runtime, "q1")

    assert exc.value.status_code == 409
    assert runtime.storage.open_question_tool_use_ids("s1") == ["q1"]


async def test_cancel_conflicts_with_an_answer_in_flight(tmp_path, monkeypatch) -> None:
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
        await cancel(runtime, "q1")
    gate.set()
    await first

    assert exc.value.status_code == 409
    assert runtime.storage.open_question_tool_use_ids("s1") == []


# ── Turn ends while a question is open ──────────────────────────────────────


def input_transport(runtime: SessionRuntime, monkeypatch) -> MagicMock:
    transport = fake_transport(runtime, monkeypatch)
    transport.is_structured = False
    transport.input_blocked = AsyncMock(return_value=False)
    transport.has_pending_approval = MagicMock(return_value=False)
    return transport


async def task_notification_turn(runtime: SessionRuntime) -> None:
    """A Monitor event starts a fresh turn that ends without a reply."""
    await runtime._emit_adapter_event(
        "s1",
        EventKind.SYSTEM_NOTE,
        'Monitor event: "build finished"',
        {"method": "task_notification"},
        SessionStatus.RUNNING,
    )
    await end_turn(runtime)


async def end_turn(runtime: SessionRuntime) -> None:
    await runtime._emit_adapter_event(
        "s1",
        EventKind.SYSTEM_NOTE,
        "Turn complete",
        {"method": "result", "stop_reason": "end_turn"},
        SessionStatus.IDLE,
    )


def last_event(runtime: SessionRuntime):
    return runtime.storage.list_events("s1")[-1]


async def test_turn_after_an_open_question_settles_waiting(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    await ask(runtime, "q1")

    await task_notification_turn(runtime)

    assert runtime.get_session("s1").status is SessionStatus.WAITING_INPUT
    assert last_event(runtime).metadata["status"] == SessionStatus.WAITING_INPUT


async def test_agent_message_does_not_end_the_wait(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    input_transport(runtime, monkeypatch)
    await ask(runtime, "q1")

    await runtime.handle_input(
        "s1", SessionInputRequest(text="[wp-msg] status?"), HeldMessageOrigin.AGENT
    )
    assert runtime.get_session("s1").status is SessionStatus.RUNNING
    assert last_event(runtime).metadata[INPUT_ORIGIN_KEY] == HeldMessageOrigin.AGENT
    await end_turn(runtime)

    assert runtime.get_session("s1").status is SessionStatus.WAITING_INPUT


async def test_human_message_moves_past_the_question(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    input_transport(runtime, monkeypatch)
    await ask(runtime, "q1")

    await runtime.handle_input("s1", SessionInputRequest(text="do this instead"))
    assert INPUT_ORIGIN_KEY not in last_event(runtime).metadata
    await end_turn(runtime)

    assert runtime.get_session("s1").status is SessionStatus.IDLE


async def test_answered_question_lets_the_turn_end_idle(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    await ask(runtime, "q1")
    await answer(ClaudeTtyPlugin(), runtime, "q1")

    await end_turn(runtime)

    assert runtime.get_session("s1").status is SessionStatus.IDLE


async def test_closed_question_lets_the_turn_end_idle(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    await ask(runtime, "q1")
    monkeypatch.setattr(
        runtime.questions,
        "liveness",
        lambda session, ids: dict.fromkeys(ids, QuestionLiveness.CLOSED),
    )

    await task_notification_turn(runtime)

    assert runtime.get_session("s1").status is SessionStatus.IDLE


async def test_cancel_after_a_settled_wait_marks_idle(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    transport.has_pending_approval = MagicMock(return_value=False)
    await ask(runtime, "q1")
    await task_notification_turn(runtime)

    updated = await cancel(runtime, "q1")

    assert updated.status is SessionStatus.IDLE


async def test_relaunch_note_keeps_the_wait(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)
    await ask(runtime, "q1")

    await runtime._record_system_event(
        "s1", "Restarted Claude TUI session", status=SessionStatus.IDLE
    )

    assert runtime.get_session("s1").status is SessionStatus.WAITING_INPUT


async def test_input_a_plugin_records_carries_the_origin(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    transport = input_transport(runtime, monkeypatch)
    transport.is_structured = True
    plugin = runtime.registry.plugin_for(runtime.get_session("s1"))

    async def handle_command(runtime_, session, request):
        await runtime_._record_user_event(session.id, request.text, submit=True)
        return session

    monkeypatch.setattr(plugin, "maybe_handle_input", handle_command)
    await ask(runtime, "q1")

    await runtime.handle_input(
        "s1", SessionInputRequest(text="/compact"), HeldMessageOrigin.AGENT
    )
    assert last_event(runtime).metadata[INPUT_ORIGIN_KEY] == HeldMessageOrigin.AGENT
    await end_turn(runtime)

    assert runtime.get_session("s1").status is SessionStatus.WAITING_INPUT
