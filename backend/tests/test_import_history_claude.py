import json
from datetime import UTC, datetime

from waypoint.backends.claude_code.history import (
    convert_transcript_records,
    read_local_claude_history,
    token_usage_records_from_history,
)
from waypoint.backends.claude_code.threads import read_local_claude_transcript
from waypoint.schemas import EventKind


def _assistant_text(
    msg_id: str,
    text: str,
    stop_reason: str = "tool_use",
    ts: str = "2026-04-29T15:47:09.826Z",
) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
        },
    }


def _assistant_tool_use(
    msg_id: str,
    tool_use_id: str,
    name: str,
    inp: dict,
    ts: str = "2026-04-29T15:47:10.000Z",
) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": inp}
            ],
            "stop_reason": "tool_use",
        },
    }


def _user_text(text: str, ts: str = "2026-04-29T15:47:08.000Z") -> dict:
    return {"type": "user", "timestamp": ts, "message": {"content": text}}


def _user_tool_result(
    tool_use_id: str,
    result: str,
    is_error: bool = False,
    ts: str = "2026-04-29T15:47:11.000Z",
) -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result,
                    "is_error": is_error,
                }
            ]
        },
    }


def _injected_notification(ts: str = "2026-04-29T15:47:12.000Z") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"content": "<task-notification>ping</task-notification>"},
    }


def test_convert_transcript_records_orders_and_pairs_tool_use() -> None:
    records = [
        _user_text("Please fix the bug"),
        _assistant_tool_use("msg1", "tu1", "Bash", {"command": "ls"}),
        _user_tool_result("tu1", "file1\nfile2"),
        _assistant_text("msg2", "Done", stop_reason="end_turn"),
    ]

    events = convert_transcript_records("sess-1", records)

    assert [e.kind for e in events] == [
        EventKind.USER_INPUT,
        EventKind.TOOL_CALL,
        EventKind.TOOL_RESULT,
        EventKind.AGENT_OUTPUT,
    ]
    assert events[0].text == "Please fix the bug"

    call, result = events[1], events[2]
    assert call.metadata["tool_use_id"] == "tu1"
    assert call.metadata["item_id"] == "tu1"
    assert call.metadata["tool_name"] == "Bash"
    assert result.metadata["tool_use_id"] == "tu1"
    assert result.metadata["item_id"] == "tu1"
    assert result.text == "file1\nfile2"

    assert events[3].text == "Done"
    assert all(e.session_id == "sess-1" for e in events)


def test_convert_transcript_records_skips_injected_turns() -> None:
    records = [
        _injected_notification(),
        _assistant_text("msg1", "hi", stop_reason="end_turn"),
    ]

    events = convert_transcript_records("sess-1", records)

    assert [e.kind for e in events] == [EventKind.AGENT_OUTPUT]


def test_convert_transcript_records_preserves_source_timestamps() -> None:
    records = [_user_text("hello", ts="2026-01-01T00:00:00Z")]

    events = convert_transcript_records("sess-1", records)

    assert events[0].ts == datetime(2026, 1, 1, tzinfo=UTC)


def test_convert_transcript_records_marks_tool_error() -> None:
    records = [
        _assistant_tool_use("msg1", "tu1", "Bash", {"command": "false"}),
        _user_tool_result("tu1", "boom", is_error=True),
    ]

    events = convert_transcript_records("sess-1", records)

    result = events[1]
    assert result.kind == EventKind.TOOL_RESULT
    assert result.metadata["is_error"] is True


def test_convert_transcript_records_skips_non_chat_record_types() -> None:
    records = [
        {"type": "summary", "summary": "prior context"},
        {"type": "system", "subtype": "compact_boundary"},
        _assistant_text("msg1", "hi", stop_reason="end_turn"),
    ]

    events = convert_transcript_records("sess-1", records)

    assert [e.kind for e in events] == [EventKind.AGENT_OUTPUT]


def test_read_local_claude_transcript_reads_entire_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    thread_id = "11111111-1111-1111-1111-111111111111"
    project_dir = tmp_path / "claude" / "projects" / "-home-user-project"
    project_dir.mkdir(parents=True)
    transcript = project_dir / f"{thread_id}.jsonl"
    records = [_user_text(f"turn {i}") for i in range(250)]
    transcript.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )

    read_records = read_local_claude_transcript(thread_id)

    # Longer than the 200-line cap `_read_thread_info` uses for metadata
    # sniffing — history import must not inherit that cap.
    assert len(read_records) == 250


def _assistant_with_usage(
    msg_id: str,
    model: str,
    usage: dict,
    text: str = "hi",
    ts: str = "2026-04-29T15:47:09.826Z",
) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "usage": usage,
        },
    }


def test_token_usage_records_from_history_threads_model_no_effort() -> None:
    records = [
        _assistant_with_usage(
            "msg1",
            "claude-sonnet-4-5",
            {"input_tokens": 10, "output_tokens": 5},
        )
    ]

    token_records = token_usage_records_from_history(records)

    assert len(token_records) == 1
    record = token_records[0]
    assert record.record_id == "msg1"
    assert record.model == "claude-sonnet-4-5"
    # A transcript never records reasoning effort per message — a replayed
    # turn always surfaces it as unknown rather than guessed.
    assert record.effort is None
    assert record.totals == {"input_tokens": 10, "output_tokens": 5}


def test_token_usage_records_from_history_skips_unresolvable_model() -> None:
    # Model not in the Claude catalogue — context window unknown, so the
    # snapshot gate (shared with the live path) drops it rather than guess.
    records = [
        _assistant_with_usage("msg1", "gpt-4", {"input_tokens": 10, "output_tokens": 5})
    ]

    assert token_usage_records_from_history(records) == []


def test_token_usage_records_from_history_skips_non_assistant_and_no_usage() -> None:
    records = [
        _user_text("hello"),
        {"type": "assistant", "message": {"id": "msg1", "model": "claude-sonnet-4-5"}},
    ]

    assert token_usage_records_from_history(records) == []


def test_token_usage_records_from_history_multiple_turns() -> None:
    records = [
        _assistant_with_usage(
            "msg1", "claude-sonnet-4-5", {"input_tokens": 10, "output_tokens": 5}
        ),
        _assistant_with_usage(
            "msg2", "claude-opus-4-8", {"input_tokens": 20, "output_tokens": 8}
        ),
    ]

    token_records = token_usage_records_from_history(records)

    assert [r.record_id for r in token_records] == ["msg1", "msg2"]
    assert [r.model for r in token_records] == ["claude-sonnet-4-5", "claude-opus-4-8"]


async def test_read_local_claude_history_converts_full_file(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    thread_id = "22222222-2222-2222-2222-222222222222"
    project_dir = tmp_path / "claude" / "projects" / "-home-user-project"
    project_dir.mkdir(parents=True)
    transcript = project_dir / f"{thread_id}.jsonl"
    records = [_user_text(f"turn {i}") for i in range(210)]
    transcript.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )

    events = await read_local_claude_history("sess-1", thread_id)

    assert len(events) == 210
    assert all(event.kind == EventKind.USER_INPUT for event in events)
