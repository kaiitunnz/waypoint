"""Unit tests for the claude_tty transcript normalizer."""

import pytest

from waypoint.backends.claude_tty.normalize import (
    NormalizedEvent,
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


def _edit_result_record(
    tool_use_id: str, tool_use_result: dict, is_error: bool = False
) -> dict:
    return {
        "type": "user",
        "message": {
            "content": [_tool_result_block(tool_use_id, "ok", is_error=is_error)]
        },
        "toolUseResult": tool_use_result,
    }


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


def test_result_note_emitted_once_per_message_id() -> None:
    # A turn's content can arrive as several same-id end_turn records; the
    # synthesized note (and the usage it carries) must fire exactly once, not
    # once per record, or it duplicates and inflates token totals.
    norm = TranscriptNormalizer()
    usage = {"input_tokens": 100, "output_tokens": 50}
    r1 = _assistant_record(
        "msgX", [_text_block("a")], stop_reason="end_turn", usage=usage
    )
    events1 = norm.process_record(r1)
    result1 = next(e for e in events1 if e.metadata.get("method") == "result")
    assert result1.metadata["usage"] == usage

    # Second record, same message.id, also end_turn → no second note.
    r2 = _assistant_record(
        "msgX", [_text_block("b")], stop_reason="end_turn", usage=usage
    )
    events2 = norm.process_record(r2)
    assert not any(e.metadata.get("method") == "result" for e in events2)


def test_split_thinking_then_text_emits_single_result_after_text() -> None:
    # The real-world duplicate: a turn's final message arrives as a thinking
    # record then a text record, both stamped end_turn. Exactly one note must
    # fire, and after the visible text — not prematurely off the thinking record
    # (which also flickered the status idle→running→idle).
    norm = TranscriptNormalizer()
    r_think = _assistant_record(
        "msgT", [{"type": "thinking", "thinking": "hmm"}], stop_reason="end_turn"
    )
    r_text = _assistant_record("msgT", [_text_block("Got it")], stop_reason="end_turn")

    assert norm.process_record(r_think) == []

    ev_text = norm.process_record(r_text)
    assert [e.kind for e in ev_text] == [
        EventKind.AGENT_OUTPUT,
        EventKind.SYSTEM_NOTE,
    ]
    note = ev_text[1]
    assert note.metadata["method"] == "result"
    assert note.status == SessionStatus.IDLE


def test_thinking_only_abnormal_stop_emits_note_immediately() -> None:
    # Only an end_turn message splits thinking/text across records. An abnormal
    # termination (max_tokens hit mid-thinking) has no text sibling coming, so
    # the note must fire on the thinking record rather than be deferred forever.
    norm = TranscriptNormalizer()
    record = _assistant_record(
        "msgK",
        [{"type": "thinking", "thinking": "long thought"}],
        stop_reason="max_tokens",
    )
    note = next(
        e for e in norm.process_record(record) if e.metadata.get("method") == "result"
    )
    assert note.status == SessionStatus.IDLE
    assert note.metadata["stop_reason"] == "max_tokens"


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


def test_generic_error_tool_result_stays_running() -> None:
    # A failing tool (not a user decline) does not end the turn — the agent
    # gets the error and continues — so status must stay RUNNING.
    norm = TranscriptNormalizer()
    record = _user_record([_tool_result_block("tu2", "ENOENT", is_error=True)])
    events = norm.process_record(record)
    assert all(e.status == SessionStatus.RUNNING for e in events)
    assert not any(e.kind == EventKind.SYSTEM_NOTE for e in events)


def test_user_rejected_tool_resolves_session_to_idle() -> None:
    # A declined tool aborts the turn back to the prompt with no terminal
    # stop_reason record, so the normalizer must synthesize an idle result or
    # the session stays stuck running.
    norm = TranscriptNormalizer()
    record = _user_record(
        [_tool_result_block("tu3", "The tool use was rejected", is_error=True)]
    )
    record["toolUseResult"] = "User rejected tool use"
    events = norm.process_record(record)

    note = next(e for e in events if e.kind == EventKind.SYSTEM_NOTE)
    assert note.status == SessionStatus.IDLE
    assert note.metadata["stop_reason"] == "tool_rejected"


# ── AskUserQuestion surfacing ───────────────────────────────────────────────


def _ask_question_block(tool_id: str) -> dict:
    return _tool_use_block(
        tool_id,
        "AskUserQuestion",
        {
            "questions": [
                {
                    "question": "Tabs or spaces?",
                    "header": "Indent",
                    "multiSelect": False,
                    "options": [{"label": "Tabs"}, {"label": "Spaces"}],
                }
            ]
        },
    )


def test_armed_ask_question_surfaces_as_waiting_input() -> None:
    norm = TranscriptNormalizer()
    norm.arm_question_dismissal()
    record = _assistant_record(
        "msg1", [_ask_question_block("auq1")], stop_reason="tool_use"
    )
    events = norm.process_record(record)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == EventKind.TOOL_CALL
    assert ev.metadata["tool_name"] == "AskUserQuestion"
    assert ev.status == SessionStatus.WAITING_INPUT
    # Full questions payload is carried so the frontend renders the card.
    assert ev.metadata["payload"]["input"]["questions"][0]["question"] == (
        "Tabs or spaces?"
    )


def test_armed_ask_question_swallows_rejection_and_stays_waiting() -> None:
    # Esc-ing the popup to surface it makes the TUI write a "user rejected"
    # result; it must be dropped so the card stays answerable and the session
    # does not flip to idle.
    norm = TranscriptNormalizer()
    norm.arm_question_dismissal()
    norm.process_record(
        _assistant_record("msg1", [_ask_question_block("auq1")], stop_reason="tool_use")
    )
    rejection = _user_record(
        [_tool_result_block("auq1", "The tool use was rejected", is_error=True)]
    )
    rejection["toolUseResult"] = "User rejected tool use"
    events = norm.process_record(rejection)
    assert events == []


def test_unarmed_ask_question_is_a_plain_tool_call() -> None:
    # Without the dismissal latch (e.g. a historical record), AskUserQuestion
    # is just a normal tool_call and a genuine rejection still resolves to idle.
    norm = TranscriptNormalizer()
    events = norm.process_record(
        _assistant_record("msg1", [_ask_question_block("auq1")], stop_reason="tool_use")
    )
    assert len(events) == 1
    assert events[0].status == SessionStatus.RUNNING
    rejection = _user_record([_tool_result_block("auq1", "rejected")])
    rejection["toolUseResult"] = "User rejected tool use"
    note = next(
        e for e in norm.process_record(rejection) if e.kind == EventKind.SYSTEM_NOTE
    )
    assert note.status == SessionStatus.IDLE


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


# ── file-edit diff previews ───────────────────────────────────────────────────


def test_write_result_attaches_add_diff_preview() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msgW",
            [_tool_use_block("tu1", "Write", {"file_path": "a.py"})],
            stop_reason="tool_use",
        )
    )
    events = norm.process_record(
        _edit_result_record(
            "tu1",
            {"type": "create", "filePath": "/repo/a.py", "content": "x = 1\n"},
        )
    )
    result = next(e for e in events if e.kind == EventKind.TOOL_RESULT)
    preview = result.metadata["diff_preview"]
    assert preview["phase"] == "applied"
    file = preview["files"][0]
    assert file["change_type"] == "add"
    assert file["path"] == "/repo/a.py"
    assert file["additions"] == 1


def test_edit_result_attaches_update_diff_from_structured_patch() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msgE",
            [_tool_use_block("tu1", "Edit", {"file_path": "a.py"})],
            stop_reason="tool_use",
        )
    )
    events = norm.process_record(
        _edit_result_record(
            "tu1",
            {
                "type": "update",
                "filePath": "/repo/a.py",
                "structuredPatch": [
                    {
                        "oldStart": 1,
                        "oldLines": 1,
                        "newStart": 1,
                        "newLines": 2,
                        "lines": [" keep", "+added"],
                    }
                ],
            },
        )
    )
    result = next(e for e in events if e.kind == EventKind.TOOL_RESULT)
    file = result.metadata["diff_preview"]["files"][0]
    assert file["change_type"] == "update"
    assert "@@ -1,1 +1,2 @@" in file["diff"]
    assert "+added" in file["diff"]
    assert file["additions"] == 1


def test_non_edit_tool_result_has_no_diff_preview() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msgR",
            [_tool_use_block("tu1", "Read", {"file_path": "a.py"})],
            stop_reason="tool_use",
        )
    )
    events = norm.process_record(_user_record([_tool_result_block("tu1", "data")]))
    result = next(e for e in events if e.kind == EventKind.TOOL_RESULT)
    assert "diff_preview" not in result.metadata


def test_failed_edit_result_has_no_diff_preview() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msgF",
            [_tool_use_block("tu1", "Edit", {"file_path": "a.py"})],
            stop_reason="tool_use",
        )
    )
    events = norm.process_record(
        _edit_result_record(
            "tu1",
            {"type": "update", "filePath": "/repo/a.py", "structuredPatch": []},
            is_error=True,
        )
    )
    result = next(e for e in events if e.kind == EventKind.TOOL_RESULT)
    assert "diff_preview" not in result.metadata


def test_rejected_file_edit_has_no_diff_preview_and_aborts_turn() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msgX",
            [_tool_use_block("tu1", "Edit", {"file_path": "a.py"})],
            stop_reason="tool_use",
        )
    )
    rejection = {
        "type": "user",
        "message": {"content": [_tool_result_block("tu1", "denied", is_error=True)]},
        "toolUseResult": "User rejected tool use",
    }
    events = norm.process_record(rejection)
    result = next(e for e in events if e.kind == EventKind.TOOL_RESULT)
    assert "diff_preview" not in result.metadata
    assert any(
        e.kind == EventKind.SYSTEM_NOTE
        and e.metadata.get("stop_reason") == "tool_rejected"
        for e in events
    )


# ── compaction ────────────────────────────────────────────────────────────────


def _compact_boundary_record(trigger: str, pre_tokens: int | None = None) -> dict:
    meta: dict = {"trigger": trigger}
    if pre_tokens is not None:
        meta["preTokens"] = pre_tokens
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "content": "Conversation compacted",
        "compactMetadata": meta,
    }


def test_manual_compact_resolves_session_to_idle() -> None:
    norm = TranscriptNormalizer()
    events = norm.process_record(_compact_boundary_record("manual", pre_tokens=89941))
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == EventKind.SYSTEM_NOTE
    assert ev.status == SessionStatus.IDLE
    assert ev.metadata["stop_reason"] == "compact"
    assert "89941" in ev.text


def test_manual_compact_without_pre_tokens_uses_plain_text() -> None:
    norm = TranscriptNormalizer()
    events = norm.process_record(_compact_boundary_record("manual"))
    assert events[0].text == "Context compacted"


def test_auto_compact_emits_no_event() -> None:
    norm = TranscriptNormalizer()
    assert (
        norm.process_record(_compact_boundary_record("auto", pre_tokens=120000)) == []
    )


def test_compact_boundary_without_metadata_emits_no_event() -> None:
    # Missing compactMetadata means no trigger, so it cannot be confirmed manual.
    norm = TranscriptNormalizer()
    record = {"type": "system", "subtype": "compact_boundary"}
    assert norm.process_record(record) == []


def test_compact_summary_user_record_still_dropped() -> None:
    norm = TranscriptNormalizer()
    record = _user_record(
        "This session is being continued from a previous conversation"
    )
    assert norm.process_record(record) == []


def test_local_command_resolves_session_to_idle_with_stdout() -> None:
    # A rejected /compact (and other builtin local commands) prints a
    # local_command record and runs no turn, leaving the send-flipped RUNNING
    # status unresolved without this.
    norm = TranscriptNormalizer()
    record = {
        "type": "system",
        "subtype": "local_command",
        "content": "<local-command-stdout>Not enough messages to compact.</local-command-stdout>",
    }
    events = norm.process_record(record)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == EventKind.SYSTEM_NOTE
    assert ev.status == SessionStatus.IDLE
    assert ev.metadata["stop_reason"] == "local_command"
    assert ev.text == "Not enough messages to compact."


def test_local_command_without_stdout_falls_back_to_generic_note() -> None:
    norm = TranscriptNormalizer()
    record = {"type": "system", "subtype": "local_command", "content": ""}
    events = norm.process_record(record)
    assert events[0].text == "Command complete"
    assert events[0].status == SessionStatus.IDLE


def test_local_command_stdout_is_truncated() -> None:
    norm = TranscriptNormalizer()
    body = "x" * 1000
    record = {
        "type": "system",
        "subtype": "local_command",
        "content": f"<local-command-stdout>{body}</local-command-stdout>",
    }
    text = norm.process_record(record)[0].text
    assert len(text) <= 500
    assert text.endswith("…")


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


# ── Task tool handling ────────────────────────────────────────────────────────


def _task_create_block(tool_id: str, subject: str, status: str = "pending") -> dict:
    return _tool_use_block(
        tool_id, "TaskCreate", {"subject": subject, "status": status}
    )


def _task_update_block(
    tool_id: str, task_id: str, status: str, subject: str | None = None
) -> dict:
    inp: dict = {"taskId": task_id, "status": status}
    if subject is not None:
        inp["subject"] = subject
    return _tool_use_block(tool_id, "TaskUpdate", inp)


def _task_create_result(tool_use_id: str, task_id: str) -> dict:
    return _tool_result_block(tool_use_id, f"Task #{task_id} created successfully")


def _snapshot_event(events: list) -> "NormalizedEvent":
    return next(
        e for e in events if e.metadata.get("method") == "assistant.task_update"
    )


def test_task_create_assistant_emits_no_event() -> None:
    norm = TranscriptNormalizer()
    record = _assistant_record(
        "msg1", [_task_create_block("tc1", "Write tests")], stop_reason="tool_use"
    )
    events = norm.process_record(record)
    assert events == []


def test_task_create_result_emits_snapshot() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msg1", [_task_create_block("tc1", "Write tests")], stop_reason="tool_use"
        )
    )
    events = norm.process_record(_user_record([_task_create_result("tc1", "42")]))
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == EventKind.TOOL_RESULT
    assert ev.metadata["method"] == "assistant.task_update"
    assert ev.metadata["tool_name"] == "TodoWrite"
    assert ev.metadata["item_type"] == "todo_list"
    todos = ev.metadata["payload"]["input"]["todos"]
    assert len(todos) == 1
    assert todos[0]["content"] == "Write tests"
    assert todos[0]["status"] == "pending"


def test_task_create_result_not_emitted_as_raw_tool_result() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msg1", [_task_create_block("tc1", "Do work")], stop_reason="tool_use"
        )
    )
    events = norm.process_record(_user_record([_task_create_result("tc1", "1")]))
    assert not any(e.metadata.get("method") == "user.tool_result" for e in events)


def test_task_update_emits_snapshot_with_status_change() -> None:
    norm = TranscriptNormalizer()
    # Create a task first
    norm.process_record(
        _assistant_record(
            "msg1", [_task_create_block("tc1", "Fix bug")], stop_reason="tool_use"
        )
    )
    norm.process_record(_user_record([_task_create_result("tc1", "1")]))
    # Now update it
    events = norm.process_record(
        _assistant_record(
            "msg2",
            [_task_update_block("tu2", "1", "in_progress", "Fix bug now")],
            stop_reason="tool_use",
        )
    )
    snapshot_ev = _snapshot_event(events)
    todos = snapshot_ev.metadata["payload"]["input"]["todos"]
    assert todos[0]["status"] == "in_progress"
    assert todos[0]["content"] == "Fix bug now"


def test_task_update_result_is_suppressed() -> None:
    norm = TranscriptNormalizer()
    norm.process_record(
        _assistant_record(
            "msg1", [_task_create_block("tc1", "Task A")], stop_reason="tool_use"
        )
    )
    norm.process_record(_user_record([_task_create_result("tc1", "1")]))
    norm.process_record(
        _assistant_record(
            "msg2",
            [_task_update_block("tu2", "1", "completed")],
            stop_reason="tool_use",
        )
    )
    # The tool_result for the TaskUpdate should be silently dropped
    events = norm.process_record(
        _user_record([_tool_result_block("tu2", "Task updated")])
    )
    assert events == []


def test_task_get_list_suppressed_no_snapshot() -> None:
    for tool_name in ("TaskGet", "TaskList"):
        n = TranscriptNormalizer()
        events = n.process_record(
            _assistant_record(
                "msg1",
                [_tool_use_block("tg1", tool_name, {"taskId": "1"})],
                stop_reason="tool_use",
            )
        )
        assert events == [], f"{tool_name} should emit no events"
        # result is also suppressed
        result_events = n.process_record(
            _user_record([_tool_result_block("tg1", "some result")])
        )
        assert result_events == [], f"{tool_name} result should be suppressed"


def test_task_tool_use_not_emitted_as_tool_call() -> None:
    for tool_name in ("TaskCreate", "TaskUpdate", "TaskGet", "TaskList"):
        n = TranscriptNormalizer()
        events = n.process_record(
            _assistant_record(
                "msg1",
                [_tool_use_block("x1", tool_name, {"subject": "t", "taskId": "1"})],
                stop_reason="tool_use",
            )
        )
        assert not any(
            e.kind == EventKind.TOOL_CALL for e in events
        ), f"{tool_name} must not emit TOOL_CALL"


def test_task_card_item_id_stable_within_group() -> None:
    norm = TranscriptNormalizer()
    # Create two tasks
    norm.process_record(
        _assistant_record(
            "msg1",
            [
                _task_create_block("tc1", "Task A"),
                _task_create_block("tc2", "Task B"),
            ],
            stop_reason="tool_use",
        )
    )
    ev1 = norm.process_record(_user_record([_task_create_result("tc1", "1")]))
    ev2 = norm.process_record(_user_record([_task_create_result("tc2", "2")]))
    id1 = ev1[0].metadata["item_id"]
    id2 = ev2[0].metadata["item_id"]
    assert id1 == id2, "item_id must be stable across snapshots in same group"


def test_task_card_item_id_rotates_for_new_group() -> None:
    norm = TranscriptNormalizer()
    # First group: one task, completed
    norm.process_record(
        _assistant_record(
            "msg1", [_task_create_block("tc1", "Task A")], stop_reason="tool_use"
        )
    )
    ev1 = norm.process_record(_user_record([_task_create_result("tc1", "1")]))
    # Mark as deleted (empties tracker)
    norm.process_record(
        _assistant_record(
            "msg2",
            [_task_update_block("tu2", "1", "deleted")],
            stop_reason="tool_use",
        )
    )
    norm.process_record(_user_record([_tool_result_block("tu2", "ok")]))
    # Second group: new task
    norm.process_record(
        _assistant_record(
            "msg3", [_task_create_block("tc3", "Task B")], stop_reason="tool_use"
        )
    )
    ev2 = norm.process_record(_user_record([_task_create_result("tc3", "2")]))
    id1 = ev1[0].metadata["item_id"]
    id2 = ev2[0].metadata["item_id"]
    assert id1 != id2, "item_id must rotate when a new task group starts"


def test_non_task_tool_use_in_same_record_still_emits_tool_call() -> None:
    norm = TranscriptNormalizer()
    record = _assistant_record(
        "msg1",
        [
            _task_create_block("tc1", "Task A"),
            _tool_use_block("bash1", "Bash", {"command": "ls"}),
        ],
        stop_reason="tool_use",
    )
    events = norm.process_record(record)
    tool_call_events = [e for e in events if e.kind == EventKind.TOOL_CALL]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].metadata["tool_name"] == "Bash"
