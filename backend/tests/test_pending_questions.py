import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from waypoint.backends.base import QuestionCancelling
from waypoint.backends.claude_code.plugin import ClaudeCodePlugin
from waypoint.backends.opencode.plugin import OpenCodePlugin
from waypoint.questions import QuestionLiveness
from waypoint.runtime import SessionRuntime
from waypoint.schemas import (
    EventKind,
    SessionRecord,
    SessionSource,
    SessionStatus,
)
from waypoint.settings import Settings
from waypoint.storage import Storage


class FakeQuestionPlugin:
    def __init__(self) -> None:
        self.liveness: dict[str, QuestionLiveness] = {}
        self.cancelled: list[str] = []

    def question_liveness(
        self, runtime: Any, session: SessionRecord, tool_use_ids: list[str]
    ) -> dict[str, QuestionLiveness]:
        return {
            tid: self.liveness.get(tid, QuestionLiveness.ACTIONABLE)
            for tid in tool_use_ids
        }

    async def cancel_question(
        self, runtime: SessionRuntime, session: SessionRecord, tool_use_id: str
    ) -> SessionRecord:
        self.cancelled.append(tool_use_id)
        await runtime.questions.record_cancelled(session.id, tool_use_id)
        return runtime.get_session(session.id)


def make_runtime(tmp_path: Path, monkeypatch, plugin: object) -> SessionRuntime:
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
            title="q",
            cwd="/tmp",
            status=SessionStatus.WAITING_INPUT,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
        )
    )
    monkeypatch.setattr(runtime.registry, "plugin_for", lambda session: plugin)
    return runtime


async def ask(runtime: SessionRuntime, tool_use_id: str) -> None:
    await runtime._emit_adapter_event(
        "s1",
        EventKind.TOOL_CALL,
        "AskUserQuestion",
        {"tool_name": "AskUserQuestion", "tool_use_id": tool_use_id},
        SessionStatus.WAITING_INPUT,
    )


async def note(runtime: SessionRuntime, kind: str, tool_use_id: str) -> None:
    await runtime._record_system_event(
        "s1", kind, metadata={"kind": kind, "tool_use_id": tool_use_id}
    )


def subscribe(runtime: SessionRuntime) -> asyncio.Queue[dict[str, Any]]:
    return runtime.broadcast.subscribe_session("s1")


def drain_question_envelopes(
    queue: asyncio.Queue[dict[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    while not queue.empty():
        message = queue.get_nowait()
        if message["type"] == "pending_questions":
            out.append(message["payload"])
    return out


async def test_cancel_and_closure_notes_resolve_only_their_question(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path, monkeypatch, FakeQuestionPlugin())
    for tool_use_id in ("", "cancelled", "closed", "open"):
        await ask(runtime, tool_use_id)
    # A re-sent call for the same id must not duplicate the open card.
    await ask(runtime, "open")
    await note(runtime, "ask_user_question_cancelled", "cancelled")
    await note(runtime, "ask_user_question_closed", "closed")

    events = runtime.storage.open_question_events("s1")

    assert [event.metadata["tool_use_id"] for event in events] == ["open"]
    assert events[0].sequence == 4


async def test_snapshot_covers_questions_outside_the_tail_page(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path, monkeypatch, FakeQuestionPlugin())
    await ask(runtime, "old")
    for index in range(5):
        await runtime._record_user_event("s1", f"filler {index}", submit=True)

    page = runtime.session_events_page("s1", message_limit=2)
    older = runtime.session_events_page("s1", message_limit=2, before_sequence=3)

    assert all(event.metadata.get("tool_use_id") != "old" for event in page.events)
    assert page.pending_questions is not None
    assert [q.tool_use_id for q in page.pending_questions.questions] == ["old"]
    assert page.pending_questions.as_of_sequence == runtime.storage.max_sequence("s1")
    assert older.pending_questions is None


async def test_snapshot_reports_availability_and_hides_closed(
    tmp_path, monkeypatch
) -> None:
    plugin = FakeQuestionPlugin()
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    for tool_use_id in ("a", "b", "c"):
        await ask(runtime, tool_use_id)
    plugin.liveness = {
        "b": QuestionLiveness.STARTING,
        "c": QuestionLiveness.CLOSED,
    }

    snapshot = runtime.questions.compute("s1")

    assert [(q.tool_use_id, q.availability) for q in snapshot.questions] == [
        ("a", "actionable"),
        ("b", "starting"),
    ]
    # compute is read-only: the closed question is not resolved yet.
    assert runtime.storage.open_question_tool_use_ids("s1") == ["a", "b", "c"]


async def test_revision_bumps_only_when_the_snapshot_changes(
    tmp_path, monkeypatch
) -> None:
    plugin = FakeQuestionPlugin()
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "a")

    first = runtime.questions.compute("s1")
    same = runtime.questions.compute("s1")
    plugin.liveness = {"a": QuestionLiveness.UNAVAILABLE}
    changed = runtime.questions.compute("s1")

    assert same.revision == first.revision
    assert changed.revision > first.revision
    assert changed.as_of_sequence == first.as_of_sequence


async def test_reconcile_closes_provider_ended_questions_once(
    tmp_path, monkeypatch
) -> None:
    plugin = FakeQuestionPlugin()
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "gone")
    await ask(runtime, "live")
    plugin.liveness = {"gone": QuestionLiveness.CLOSED}

    await asyncio.gather(
        runtime.questions.reconcile("s1"), runtime.questions.reconcile("s1")
    )

    closures = [
        event
        for event in runtime.storage.list_events("s1")
        if event.metadata.get("kind") == "ask_user_question_closed"
    ]
    assert [event.metadata["tool_use_id"] for event in closures] == ["gone"]
    assert runtime.storage.open_question_tool_use_ids("s1") == ["live"]


async def test_reconcile_skips_a_question_with_an_operation_in_flight(
    tmp_path, monkeypatch
) -> None:
    plugin = FakeQuestionPlugin()
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "q1")
    plugin.liveness = {"q1": QuestionLiveness.CLOSED}

    with runtime.questions.operation("s1", "q1"):
        await runtime.questions.reconcile("s1")
        with pytest.raises(HTTPException) as exc:
            with runtime.questions.operation("s1", "q1"):
                pass

    assert exc.value.status_code == 409
    assert runtime.storage.open_question_tool_use_ids("s1") == ["q1"]


async def test_flush_publishes_only_changed_snapshots(tmp_path, monkeypatch) -> None:
    plugin = FakeQuestionPlugin()
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    queue = subscribe(runtime)
    await ask(runtime, "q1")

    await runtime.questions.flush("s1")
    await runtime.questions.flush("s1")
    plugin.liveness = {"q1": QuestionLiveness.STARTING}
    await runtime.questions.flush("s1")
    await note(runtime, "ask_user_question_cancelled", "q1")
    await runtime.questions.flush("s1")

    published = drain_question_envelopes(queue)
    assert [
        [(q["tool_use_id"], q["availability"]) for q in payload["questions"]]
        for payload in published
    ] == [[("q1", "actionable")], [("q1", "starting")], []]
    revisions = [payload["revision"] for payload in published]
    assert revisions == sorted(revisions)


async def test_flush_ignores_sessions_without_question_activity(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path, monkeypatch, FakeQuestionPlugin())
    queue = subscribe(runtime)
    await runtime._record_system_event("s1", "unrelated")

    await runtime.questions.flush("s1")

    assert drain_question_envelopes(queue) == []


async def test_flusher_survives_a_failing_liveness_check(tmp_path, monkeypatch) -> None:
    plugin = FakeQuestionPlugin()
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "q1")

    def boom(*args: Any, **kwargs: Any) -> dict[str, QuestionLiveness]:
        raise RuntimeError("liveness failed")

    monkeypatch.setattr(plugin, "question_liveness", boom)
    states: list[str] = []

    async def record_state(session_id: str) -> None:
        states.append(session_id)

    monkeypatch.setattr(runtime, "_broadcast_session_state", record_state)
    flusher = asyncio.create_task(runtime._session_broadcast_loop())
    try:
        runtime._publish_session_state("s1")
        await asyncio.sleep(0.4)
        runtime._publish_session_state("s1")
        await asyncio.sleep(0.4)
    finally:
        flusher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await flusher

    assert states == ["s1", "s1"]


async def test_unsupported_plugin_has_no_actionable_questions(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path, monkeypatch, object())
    await ask(runtime, "q1")

    snapshot = runtime.questions.compute("s1")
    with pytest.raises(HTTPException) as exc:
        await runtime.cancel_question("s1", "q1")

    assert [q.availability for q in snapshot.questions] == ["unavailable"]
    assert exc.value.status_code == 400


async def test_runtime_routes_cancel_to_the_plugin(tmp_path, monkeypatch) -> None:
    plugin = FakeQuestionPlugin()
    assert isinstance(plugin, QuestionCancelling)
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "q1")

    await runtime.cancel_question("s1", "q1")

    assert plugin.cancelled == ["q1"]
    assert runtime.storage.open_question_tool_use_ids("s1") == []


def native_claude_plugin(deny_ok: bool, pending_approval: bool = False) -> Any:
    plugin = ClaudeCodePlugin()
    adapter = MagicMock()
    adapter.ask_question_liveness = lambda session_id, ids: dict.fromkeys(
        ids, QuestionLiveness.ACTIONABLE
    )
    adapter.deny_ask_question = AsyncMock(return_value=deny_ok)
    adapter.has_pending_approval = MagicMock(return_value=pending_approval)
    plugin.adapter = adapter
    return plugin


async def test_native_cancel_declines_then_records(tmp_path, monkeypatch) -> None:
    plugin = native_claude_plugin(deny_ok=True)
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "q1")

    updated = await runtime.cancel_question("s1", "q1")

    plugin.adapter.deny_ask_question.assert_awaited_once_with("s1", "q1")
    assert runtime.storage.open_question_tool_use_ids("s1") == []
    assert updated.status is SessionStatus.RUNNING


async def test_native_cancel_stays_waiting_on_another_request(
    tmp_path, monkeypatch
) -> None:
    plugin = native_claude_plugin(deny_ok=True, pending_approval=True)
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "q1")

    updated = await runtime.cancel_question("s1", "q1")

    assert updated.status is SessionStatus.WAITING_INPUT


async def test_failed_native_decline_records_nothing(tmp_path, monkeypatch) -> None:
    plugin = native_claude_plugin(deny_ok=False)
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "q1")

    with pytest.raises(HTTPException) as exc:
        await runtime.cancel_question("s1", "q1")

    assert exc.value.status_code == 502
    assert runtime.storage.open_question_tool_use_ids("s1") == ["q1"]


class _AdapterMap:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    def get(self, key: Any) -> Any:
        return self.adapter


def opencode_plugin(outcome: str) -> Any:
    plugin = OpenCodePlugin()
    adapter = MagicMock()
    adapter.question_liveness = lambda session_id, ids: dict.fromkeys(
        ids, QuestionLiveness.ACTIONABLE
    )
    adapter.reject_question = AsyncMock(return_value=outcome)
    adapter.current_question_id = MagicMock(return_value=None)
    plugin._adapters = _AdapterMap(adapter)  # type: ignore[assignment]
    return plugin


@pytest.mark.parametrize(
    ("outcome", "status_code", "closure"),
    [("missing", 400, True), ("error", 502, False)],
)
async def test_failed_opencode_reject_never_records_a_cancel(
    tmp_path, monkeypatch, outcome: str, status_code: int, closure: bool
) -> None:
    plugin = opencode_plugin(outcome)
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    await ask(runtime, "q1")

    with pytest.raises(HTTPException) as exc:
        await runtime.cancel_question("s1", "q1")

    kinds = [event.metadata.get("kind") for event in runtime.storage.list_events("s1")]
    assert exc.value.status_code == status_code
    assert "ask_user_question_cancelled" not in kinds
    assert ("ask_user_question_closed" in kinds) is closure


async def test_opencode_cancel_rejects_then_records(tmp_path, monkeypatch) -> None:
    plugin = opencode_plugin("ok")
    runtime = make_runtime(tmp_path, monkeypatch, plugin)
    monkeypatch.setattr(
        runtime,
        "transport_for",
        lambda session: MagicMock(has_pending_approval=MagicMock(return_value=False)),
    )
    await ask(runtime, "q1")

    updated = await runtime.cancel_question("s1", "q1")

    assert runtime.storage.open_question_tool_use_ids("s1") == []
    assert updated.status is SessionStatus.RUNNING
