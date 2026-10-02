import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from openai_codex.generated.v2_all import Turn

from waypoint.backends.codex.history import turns_to_events
from waypoint.backends.codex.plugin import CodexPlugin
from waypoint.backends.codex.questions import (
    async_questions,
    build_reply,
    parse_reply,
)
from waypoint.questions import QuestionDecline, QuestionLiveness
from waypoint.runtime import SessionRuntime
from waypoint.schemas import EventKind, SessionRecord, SessionSource, SessionStatus
from waypoint.settings import Settings
from waypoint.storage import Storage

QUESTIONS: list[dict[str, Any]] = [
    {"question": "Which identity?", "options": [{"label": "A"}, {"label": "B"}]},
    {"question": "Anything else?", "options": []},
]


def _reply_body(text: str) -> list[dict[str, str]]:
    assert text.startswith("<send_user_message_question_reply>\n")
    assert text.endswith("\n</send_user_message_question_reply>")
    return json.loads(text.split("\n", 1)[1].rsplit("\n", 1)[0])


def test_async_questions_map_to_the_canonical_shape() -> None:
    item = {
        "type": "agentMessage",
        "id": "call_1",
        "delivery": "async",
        "questions": [
            {"title": "Which identity?", "options": ["A", "B"]},
            {"title": "Anything else?"},
            {"title": "  "},
        ],
    }
    assert async_questions(item) == QUESTIONS
    assert async_questions({**item, "delivery": None}) is None
    assert async_questions({**item, "questions": []}) is None


def test_build_reply_answers_each_question_by_index() -> None:
    reply = build_reply(
        "call_1",
        QUESTIONS,
        [
            {"question": "Anything else?", "answer": None, "notes": "Ship it"},
            {"question": "Which identity?", "answer": "B", "notes": "work email"},
        ],
        "ignored",
    )

    assert _reply_body(reply) == [
        {
            "questionItemId": '["request_user_input_async","call_1",1]',
            "question": "Anything else?",
            "answer": "Ship it",
        },
        {
            "questionItemId": '["request_user_input_async","call_1",0]',
            "question": "Which identity?",
            "answer": "B\n\nwork email",
        },
    ]


def test_build_reply_without_structured_answers_answers_the_whole_item() -> None:
    reply = build_reply("call_1", QUESTIONS, None, " use B ")
    assert _reply_body(reply) == [
        {"questionItemId": "call_1", "question": "Which identity?", "answer": "use B"}
    ]


def test_build_reply_bounds_the_quoted_question() -> None:
    title = "é\n" * 400
    reply = build_reply(
        "call_1",
        [{"question": title, "options": []}],
        [{"question": title, "answer": "x"}],
        "",
    )
    quoted = _reply_body(reply)[0]["question"]
    assert "\n" not in quoted
    assert len(quoted.encode()) <= 512
    assert title.replace("\n", " ").startswith(quoted)


def test_parse_reply_round_trips_and_rejects_other_text() -> None:
    reply = build_reply(
        "call_1", QUESTIONS, [{"question": "Which identity?", "answer": "A"}], ""
    )
    (entry,) = parse_reply(reply) or []
    assert (entry.tool_use_id, entry.index, entry.answer) == ("call_1", 0, "A")

    bare = parse_reply(
        '<send_user_message_question_reply>{"questionItemId":"call_2",'
        '"question":"Q","answer":"yes"}</send_user_message_question_reply>'
    )
    assert bare is not None and bare[0].tool_use_id == "call_2"
    assert bare[0].index is None

    for text in (
        "plain message",
        "Quoted: " + reply,
        "<send_user_message_question_reply>[]</send_user_message_question_reply>",
        "<send_user_message_question_reply>[null]</send_user_message_question_reply>",
    ):
        assert parse_reply(text) is None


# ── Plugin ──────────────────────────────────────────────────────────────


def make_runtime(
    tmp_path: Path, status: SessionStatus = SessionStatus.RUNNING
) -> SessionRuntime:
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
            title="codex",
            cwd="/tmp",
            status=status,
            created_at=now,
            updated_at=now,
            last_event_at=now,
            raw_log_path=str(session_dir / "raw.log"),
            structured_log_path=str(session_dir / "events.jsonl"),
            transport_state={"thread_id": "thread-1"},
        )
    )
    return runtime


def fake_transport(runtime: SessionRuntime, monkeypatch) -> MagicMock:
    transport = MagicMock()
    transport.send_input = AsyncMock()
    transport.has_pending_approval = MagicMock(return_value=False)
    monkeypatch.setattr(runtime, "transport_for", lambda session: transport)
    return transport


async def ask(
    runtime: SessionRuntime, tool_use_id: str, thread_id: str = "thread-1"
) -> None:
    await runtime._emit_adapter_event(
        "s1",
        EventKind.TOOL_CALL,
        "Need your input",
        {
            "tool_name": "AskUserQuestion",
            "tool_use_id": tool_use_id,
            "tool_input": {"questions": QUESTIONS},
            "payload": {"input": {"questions": QUESTIONS}, "threadId": thread_id},
        },
        SessionStatus.RUNNING,
    )


async def test_answer_sends_the_reply_envelope_and_closes_the_card(
    tmp_path, monkeypatch
) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    await ask(runtime, "call_1")
    answers = [{"question": "Which identity?", "answer": "A"}]

    await CodexPlugin().answer_question(
        runtime, runtime.get_session("s1"), '"Which identity?"="A"', "call_1", answers
    )

    (sent,) = transport.send_input.await_args.args[1:]
    assert parse_reply(sent)[0].tool_use_id == "call_1"  # type: ignore[index]
    assert runtime.storage.open_question_tool_use_ids("s1") == []
    answer_event = runtime.storage.list_events("s1")[-2]
    assert answer_event.metadata["kind"] == "ask_user_question_answer"
    assert answer_event.metadata["answers"] == answers


async def test_answer_waits_for_a_pending_approval(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    transport = fake_transport(runtime, monkeypatch)
    transport.has_pending_approval.return_value = True
    await ask(runtime, "call_1")

    with pytest.raises(HTTPException) as exc:
        await CodexPlugin().answer_question(
            runtime, runtime.get_session("s1"), "A", "call_1", None
        )

    assert exc.value.status_code == 409
    transport.send_input.assert_not_called()
    assert runtime.storage.open_question_tool_use_ids("s1") == ["call_1"]


async def test_answer_rejects_a_closed_question(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path)
    fake_transport(runtime, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await CodexPlugin().answer_question(
            runtime, runtime.get_session("s1"), "A", "call_1", None
        )
    assert exc.value.status_code == 400


async def test_liveness_closes_questions_from_another_thread(tmp_path) -> None:
    runtime = make_runtime(tmp_path)
    await ask(runtime, "here")
    await ask(runtime, "parent", thread_id="thread-parent")
    plugin = CodexPlugin()

    liveness = plugin.question_liveness(
        runtime, runtime.get_session("s1"), ["here", "parent"]
    )
    assert liveness == {
        "here": QuestionLiveness.ACTIONABLE,
        "parent": QuestionLiveness.CLOSED,
    }

    runtime.storage.update_session("s1", status=SessionStatus.EXITED)
    exited = plugin.question_liveness(runtime, runtime.get_session("s1"), ["here"])
    assert exited == {"here": QuestionLiveness.UNAVAILABLE}


async def test_cancel_sends_nothing_and_settles_idle(tmp_path, monkeypatch) -> None:
    runtime = make_runtime(tmp_path, status=SessionStatus.WAITING_INPUT)
    transport = fake_transport(runtime, monkeypatch)
    await ask(runtime, "call_1")
    runtime.storage.update_session("s1", status=SessionStatus.WAITING_INPUT)
    monkeypatch.setattr(runtime.registry, "plugin_for", lambda session: CodexPlugin())

    outcome = await CodexPlugin().decline_question(
        runtime, runtime.get_session("s1"), "call_1"
    )
    assert outcome is QuestionDecline.AGENT_IDLE
    session = await runtime.questions.cancel(runtime.get_session("s1"), "call_1")

    transport.send_input.assert_not_called()
    assert session.status is SessionStatus.IDLE
    assert runtime.storage.open_question_tool_use_ids("s1") == []


# ── History import ──────────────────────────────────────────────────────


def _turn(turn_id: str, items: list[dict[str, object]]) -> Turn:
    return Turn.model_validate(
        {
            "id": turn_id,
            "status": "completed",
            "startedAt": 1_700_000_000,
            "completedAt": 1_700_000_010,
            "items": items,
        }
    )


def _question_item(item_id: str) -> dict[str, object]:
    return {
        "type": "agentMessage",
        "id": item_id,
        "text": "Which identity?\n- A\n- B",
        "delivery": "async",
        "questions": [{"title": "Which identity?", "options": ["A", "B"]}],
    }


def _user(text: str) -> dict[str, object]:
    return {
        "type": "userMessage",
        "id": "u",
        "content": [{"type": "text", "text": text}],
    }


def test_history_maps_questions_answers_and_closes_abandoned_ones() -> None:
    reply = build_reply(
        "answered", QUESTIONS, [{"question": "Which identity?", "answer": "B"}], ""
    )
    events = turns_to_events(
        [
            _turn("t1", [_question_item("answered"), _question_item("abandoned")]),
            _turn("t2", [_user(reply), _question_item("pending")]),
        ],
        "s1",
    )

    cards = [event for event in events if event.kind == EventKind.TOOL_CALL]
    assert [card.metadata["tool_use_id"] for card in cards] == [
        "answered",
        "abandoned",
        "pending",
    ]
    assert cards[0].metadata["payload"]["input"]["questions"][0]["options"] == [
        {"label": "A"},
        {"label": "B"},
    ]
    (answer,) = [event for event in events if event.kind == EventKind.USER_INPUT]
    assert answer.metadata["kind"] == "ask_user_question_answer"
    assert answer.metadata["tool_use_id"] == "answered"
    assert answer.text == '"Which identity?"="B"'
    closures = [
        event.metadata["tool_use_id"]
        for event in events
        if event.metadata.get("kind") == "ask_user_question_closed"
    ]
    assert closures == ["abandoned"]


def test_history_keeps_free_text_async_messages() -> None:
    events = turns_to_events(
        [
            _turn(
                "t1",
                [
                    {
                        "type": "agentMessage",
                        "id": "m1",
                        "text": "Progress update",
                        "delivery": "async",
                    }
                ],
            )
        ],
        "s1",
    )
    assert [(event.kind, event.text) for event in events] == [
        (EventKind.AGENT_OUTPUT, "Progress update")
    ]
