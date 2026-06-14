"""Unit tests for the claude_tty transcript normalizer."""

import pytest

from waypoint.backends.claude_tty.normalize import (
    TranscriptNormalizer,
    _is_injected_turn,
)
from waypoint.schemas import EventKind, SessionStatus

# ── helpers ───────────────────────────────────────────────────────────────────


def _assistant_record(
    message_id: str,
    content: list[dict],
    stop_reason: str = "end_turn",
    usage: dict | None = None,
) -> dict:
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "content": content,
            "stop_reason": stop_reason,
            "usage": usage or {"input_tokens": 10, "output_tokens": 5},
        },
    }


def _user_record(content: list[dict] | str) -> dict:
    return {"type": "user", "message": {"content": content}}


def _tool_use_block(tool_id: str, name: str, inp: dict) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _tool_result_block(tool_use_id: str, result: str, is_error: bool = False) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": result,
        "is_error": is_error,
    }


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


# ── _is_injected_turn ─────────────────────────────────────────────────────────


def test_is_injected_task_notification() -> None:
    assert _is_injected_turn("<task-notification>foo</task-notification>")


def test_is_injected_task_notification_leading_whitespace() -> None:
    assert _is_injected_turn("  \n<task-notification>bar</task-notification>")


def test_is_injected_context_summary() -> None:
    assert _is_injected_turn(
        "This session is being continued from a previous conversation."
    )


def test_not_injected_plain_text() -> None:
    assert not _is_injected_turn("Hello, world!")


def test_not_injected_list_content() -> None:
    assert not _is_injected_turn([{"type": "tool_result", "content": "ok"}])


def test_not_injected_none() -> None:
    assert not _is_injected_turn(None)


# ── TranscriptNormalizer: assistant records ────────────────────────────────────


def test_text_block_emits_agent_output() -> None:
    norm = TranscriptNormalizer()
    record = _assistant_record("msg1", [_text_block("Hello")])
    events = norm.process_record(record)
    # text block + synthesized result
    assert len(events) == 2
    text_ev = events[0]
    assert text_ev.kind == EventKind.AGENT_OUTPUT
    assert text_ev.text == "Hello"
    assert text_ev.status == SessionStatus.RUNNING
    assert text_ev.metadata["item_id"] == "msg1"


def test_tool_use_block_emits_tool_call() -> None:
    norm = TranscriptNormalizer()
    block = _tool_use_block("tu1", "Bash", {"command": "ls"})
    # stop_reason=tool_use → no synthesized result
    record = _assistant_record("msg1", [block], stop_reason="tool_use")
    events = norm.process_record(record)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == EventKind.TOOL_CALL
    assert "Bash" in ev.text
    assert ev.metadata["tool_use_id"] == "tu1"
    assert ev.metadata["tool_name"] == "Bash"
    assert ev.status == SessionStatus.RUNNING


def test_thinking_block_is_skipped() -> None:
    norm = TranscriptNormalizer()
    blocks = [
        {"type": "thinking", "thinking": "hmm"},
        _text_block("Answer"),
    ]
    record = _assistant_record("msg1", blocks)
    events = norm.process_record(record)
    kinds = [e.kind for e in events]
    assert EventKind.AGENT_OUTPUT in kinds
    assert all(e.kind != EventKind.TOOL_CALL for e in events)
    # no event emitted for thinking
    assert sum(1 for e in events if e.kind == EventKind.AGENT_OUTPUT) == 1


def test_result_synthesized_on_end_turn() -> None:
    norm = TranscriptNormalizer()
    record = _assistant_record("msg1", [_text_block("Done")], stop_reason="end_turn")
    events = norm.process_record(record)
    result_events = [e for e in events if e.metadata.get("method") == "result"]
    assert len(result_events) == 1
    result = result_events[0]
    assert result.kind == EventKind.SYSTEM_NOTE
    assert result.status == SessionStatus.IDLE
    assert result.metadata["stop_reason"] == "end_turn"


def test_no_result_when_stop_reason_is_tool_use() -> None:
    norm = TranscriptNormalizer()
    record = _assistant_record(
        "msg1", [_tool_use_block("tu1", "Read", {})], stop_reason="tool_use"
    )
    events = norm.process_record(record)
    assert not any(e.metadata.get("method") == "result" for e in events)


def test_no_result_when_tool_use_block_present_despite_end_turn() -> None:
    """A record with tool_use blocks must not synthesize a result regardless of stop_reason."""
    norm = TranscriptNormalizer()
    record = _assistant_record(
        "msg1",
        [_tool_use_block("tu1", "Read", {}), _text_block("ok")],
        stop_reason="end_turn",
    )
    events = norm.process_record(record)
    assert not any(e.metadata.get("method") == "result" for e in events)


# ── Usage deduplication ───────────────────────────────────────────────────────


def test_usage_counted_only_on_first_seen_message_id() -> None:
    norm = TranscriptNormalizer()
    usage = {"input_tokens": 100, "output_tokens": 50}
    # First record: usage should appear in result metadata
    r1 = _assistant_record(
        "msgX", [_text_block("a")], stop_reason="end_turn", usage=usage
    )
    events1 = norm.process_record(r1)
    result1 = next(e for e in events1 if e.metadata.get("method") == "result")
    assert result1.metadata["usage"] == usage

    # Second record with same message.id: usage in result should be empty
    r2 = _assistant_record(
        "msgX", [_text_block("b")], stop_reason="end_turn", usage=usage
    )
    events2 = norm.process_record(r2)
    result2 = next(e for e in events2 if e.metadata.get("method") == "result")
    assert result2.metadata["usage"] == {}


def test_usage_counted_for_different_message_ids() -> None:
    norm = TranscriptNormalizer()
    usage = {"input_tokens": 100, "output_tokens": 50}
    r1 = _assistant_record(
        "msgA", [_text_block("a")], stop_reason="end_turn", usage=usage
    )
    r2 = _assistant_record(
        "msgB", [_text_block("b")], stop_reason="end_turn", usage=usage
    )
    events1 = norm.process_record(r1)
    events2 = norm.process_record(r2)
    result1 = next(e for e in events1 if e.metadata.get("method") == "result")
    result2 = next(e for e in events2 if e.metadata.get("method") == "result")
    assert result1.metadata["usage"] == usage
    assert result2.metadata["usage"] == usage


# ── User record handling ──────────────────────────────────────────────────────


def test_tool_result_emits_tool_result_event() -> None:
    norm = TranscriptNormalizer()
    record = _user_record([_tool_result_block("tu1", "file contents")])
    events = norm.process_record(record)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == EventKind.TOOL_RESULT
    assert ev.text == "file contents"
    assert ev.metadata["tool_use_id"] == "tu1"
    assert not ev.metadata["is_error"]
    assert ev.status == SessionStatus.RUNNING


def test_error_tool_result_sets_is_error() -> None:
    norm = TranscriptNormalizer()
    record = _user_record([_tool_result_block("tu2", "ENOENT", is_error=True)])
    events = norm.process_record(record)
    assert events[0].metadata["is_error"] is True


def test_injected_task_notification_produces_no_events() -> None:
    norm = TranscriptNormalizer()
    record = _user_record("<task-notification>some harness turn</task-notification>")
    assert norm.process_record(record) == []


def test_injected_context_summary_produces_no_events() -> None:
    norm = TranscriptNormalizer()
    record = _user_record(
        "This session is being continued from a previous conversation."
    )
    assert norm.process_record(record) == []


def test_plain_text_user_turn_produces_no_events() -> None:
    # Non-tool-result, non-injected user text is already recorded by Waypoint
    # on input; the normalizer drops it to avoid duplication.
    norm = TranscriptNormalizer()
    record = _user_record([{"type": "text", "text": "hello"}])
    assert norm.process_record(record) == []


# ── TUI-only record types are dropped ────────────────────────────────────────


@pytest.mark.parametrize(
    "rec_type",
    [
        "mode",
        "permission-mode",
        "file-history-snapshot",
        "last-prompt",
        "queue-operation",
        "ai-title",
        "attachment",
        "pr-link",
        "system",
        "unknown-future-type",
    ],
)
def test_tui_only_records_dropped(rec_type: str) -> None:
    norm = TranscriptNormalizer()
    assert norm.process_record({"type": rec_type, "data": {}}) == []


# ── Interleaved record scenario ───────────────────────────────────────────────


def test_interleaved_tool_use_and_tool_result() -> None:
    """Simulate the real-transcript pattern: same message.id spans multiple
    assistant records interleaved with user tool_result records."""
    norm = TranscriptNormalizer()
    usage = {"input_tokens": 200, "output_tokens": 80}

    # First assistant record: tool_use block
    r1 = _assistant_record(
        "msgM",
        [_tool_use_block("tu1", "Read", {"file_path": "foo.py"})],
        stop_reason="tool_use",
        usage=usage,
    )
    # User record: tool_result
    r2 = _user_record([_tool_result_block("tu1", "# contents")])
    # Second assistant record: same message.id, text + end_turn
    r3 = _assistant_record(
        "msgM",
        [_text_block("I see.")],
        stop_reason="end_turn",
        usage=usage,
    )

    ev1 = norm.process_record(r1)
    ev2 = norm.process_record(r2)
    ev3 = norm.process_record(r3)

    # First record: one TOOL_CALL, no result
    assert len(ev1) == 1
    assert ev1[0].kind == EventKind.TOOL_CALL
    # User record: one TOOL_RESULT
    assert len(ev2) == 1
    assert ev2[0].kind == EventKind.TOOL_RESULT
    # Second record: AGENT_OUTPUT + synthesized SYSTEM_NOTE result
    kinds3 = [e.kind for e in ev3]
    assert EventKind.AGENT_OUTPUT in kinds3
    assert EventKind.SYSTEM_NOTE in kinds3
    # Usage must appear in second record's result (first_seen was True on r1, so
    # r3 with same id is not first_seen → usage dict should be empty)
    result3 = next(e for e in ev3 if e.metadata.get("method") == "result")
    assert result3.metadata["usage"] == {}


# ── result text formatting ────────────────────────────────────────────────────


def test_result_text_includes_output_tokens() -> None:
    norm = TranscriptNormalizer()
    record = _assistant_record(
        "msg1",
        [_text_block("ok")],
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 42},
    )
    events = norm.process_record(record)
    result = next(e for e in events if e.metadata.get("method") == "result")
    assert "42" in result.text


def test_result_text_non_end_turn_stop_reason() -> None:
    norm = TranscriptNormalizer()
    record = _assistant_record(
        "msg1",
        [_text_block("ok")],
        stop_reason="max_tokens",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    events = norm.process_record(record)
    result = next(e for e in events if e.metadata.get("method") == "result")
    assert "max_tokens" in result.text
