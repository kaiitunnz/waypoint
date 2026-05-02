from waypoint.backends.opencode.normalize import map_event
from waypoint.schemas import EventKind, SessionStatus


def test_permission_asked_maps_to_approval_request() -> None:
    kind, text, metadata = map_event(
        "permission.asked",
        {
            "id": "perm_1",
            "sessionID": "ses_1",
            "permission": "bash",
            "patterns": ["npm test"],
            "metadata": {
                "tool": "Bash",
                "command": "npm test",
            },
        },
    )

    assert kind == EventKind.APPROVAL_REQUEST
    assert text == "Bash: bash (npm test)"
    assert metadata["status"] == SessionStatus.WAITING_INPUT
    assert metadata["approval_id"] == "perm_1"
    assert metadata["tool_name"] == "Bash"
    assert metadata["tool_input"] == {"tool": "Bash", "command": "npm test"}
    assert metadata["approval"]["decisions"] == [
        "approve",
        "acceptForSession",
        "decline",
    ]


def test_question_asked_maps_to_ask_user_question_tool_call() -> None:
    kind, text, metadata = map_event(
        "question.asked",
        {
            "id": "q_1",
            "sessionID": "ses_1",
            "questions": [
                {
                    "header": "Deploy",
                    "question": "Where should this go?",
                    "multiple": True,
                    "options": [
                        {"label": "staging", "description": "safer"},
                        {"label": "prod", "description": "live traffic"},
                    ],
                }
            ],
        },
    )

    assert kind == EventKind.TOOL_CALL
    assert text == "Need your input"
    assert metadata["tool_name"] == "AskUserQuestion"
    assert metadata["tool_use_id"] == "q_1"
    assert metadata["item_id"] == "q_1"
    assert metadata["status"] == SessionStatus.WAITING_INPUT
    assert metadata["payload"] == {
        "input": {
            "questions": [
                {
                    "header": "Deploy",
                    "question": "Where should this go?",
                    "multiSelect": True,
                    "options": [
                        {"label": "staging", "description": "safer"},
                        {"label": "prod", "description": "live traffic"},
                    ],
                }
            ]
        }
    }


def test_step_finish_uses_part_payload_fields() -> None:
    kind, text, metadata = map_event(
        "message.part.updated",
        {
            "sessionID": "ses_1",
            "part": {
                "id": "part_1",
                "sessionID": "ses_1",
                "type": "step-finish",
                "reason": "completed",
                "cost": 0.125,
                "tokens": {"output": 42},
            },
        },
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == "Step finished: completed ($0.1250) 42 tokens"
    assert metadata["status"] == SessionStatus.IDLE


def test_message_part_delta_streams_agent_output() -> None:
    kind, text, metadata = map_event(
        "message.part.delta",
        {
            "sessionID": "ses_1",
            "messageID": "msg_1",
            "partID": "part_1",
            "field": "text",
            "delta": "hello",
        },
    )

    assert kind == EventKind.AGENT_OUTPUT
    assert text == "hello"
    assert metadata["item_id"] == "part_1"
    assert metadata["status"] == SessionStatus.RUNNING


def test_message_part_delta_ignores_non_text_fields() -> None:
    kind, text, metadata = map_event(
        "message.part.delta",
        {"sessionID": "ses_1", "partID": "p", "field": "reasoning", "delta": "x"},
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == ""
    assert metadata == {}


def test_tool_completed_event_carries_attachments() -> None:
    kind, text, metadata = map_event(
        "message.part.updated",
        {
            "sessionID": "ses_1",
            "part": {
                "id": "p1",
                "sessionID": "ses_1",
                "type": "tool",
                "tool": "Read",
                "callID": "call_1",
                "state": {
                    "status": "completed",
                    "input": {"path": "/etc/hosts"},
                    "output": "127.0.0.1 localhost",
                    "attachments": [{"path": "/etc/hosts"}],
                },
            },
        },
    )

    assert kind == EventKind.TOOL_RESULT
    assert text.startswith("Result for Read:")
    assert metadata["tool_use_id"] == "call_1"
    assert metadata["attachments"] == [{"path": "/etc/hosts"}]


def test_tool_error_event_uses_tool_result_kind() -> None:
    kind, text, metadata = map_event(
        "message.part.updated",
        {
            "sessionID": "ses_1",
            "part": {
                "id": "p1",
                "sessionID": "ses_1",
                "type": "tool",
                "tool": "Bash",
                "callID": "call_1",
                "state": {"status": "error", "error": "command not found"},
            },
        },
    )

    assert kind == EventKind.TOOL_RESULT
    assert text == "Error: command not found"
    assert metadata["tool_name"] == "Bash"


def test_session_status_busy_marks_session_running() -> None:
    kind, text, metadata = map_event(
        "session.status",
        {"sessionID": "ses_1", "status": {"type": "busy"}},
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == "Session busy"
    assert metadata["status"] == SessionStatus.RUNNING


def test_session_error_without_payload_falls_back_to_generic_note() -> None:
    kind, text, metadata = map_event(
        "session.error",
        {"sessionID": "ses_1"},
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == "Session error"
    assert metadata["status"] == SessionStatus.ERROR


def test_unknown_event_returns_empty_system_note() -> None:
    kind, text, metadata = map_event("unknown.thing", {"sessionID": "ses_1"})

    assert kind == EventKind.SYSTEM_NOTE
    assert text == ""
    assert metadata == {}


def test_message_updated_is_suppressed_to_avoid_duplicate_user_input() -> None:
    # Runtime._record_user_event already records the user message; re-emitting
    # message.updated for role=user would surface a second user_input entry.
    kind, text, metadata = map_event(
        "message.updated",
        {"sessionID": "ses_1", "info": {"role": "user"}},
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == ""
    assert metadata == {}


def test_message_updated_assistant_is_suppressed_to_avoid_duplicate_output() -> None:
    # Assistant text is streamed via message.part.delta; the message.updated
    # snapshot would re-append the full body and double the transcript entry.
    kind, text, metadata = map_event(
        "message.updated",
        {"sessionID": "ses_1", "info": {"role": "assistant", "finish": "stop"}},
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == ""
    assert metadata == {}


def test_message_part_updated_text_is_suppressed_when_streamed() -> None:
    kind, text, metadata = map_event(
        "message.part.updated",
        {
            "sessionID": "ses_1",
            "part": {
                "id": "p1",
                "sessionID": "ses_1",
                "type": "text",
                "text": "the full body would duplicate the streamed deltas",
            },
        },
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == ""
    assert metadata == {}


def test_message_part_updated_reasoning_is_suppressed_when_streamed() -> None:
    kind, text, metadata = map_event(
        "message.part.updated",
        {
            "sessionID": "ses_1",
            "part": {
                "id": "p1",
                "sessionID": "ses_1",
                "type": "reasoning",
                "text": "scratchpad already streamed via deltas",
            },
        },
    )

    assert kind == EventKind.SYSTEM_NOTE
    assert text == ""
    assert metadata == {}
