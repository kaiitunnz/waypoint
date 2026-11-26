"""OpenCode 2.x shapes as Waypoint events."""

from datetime import UTC, datetime
from typing import Any

from waypoint.backends.opencode import normalize
from waypoint.backends.opencode.forms import parse_form
from waypoint.backends.opencode.sessions import Permission
from waypoint.schemas import EventKind, SessionStatus

PATCH = {
    "file": "a.txt",
    "patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-alpha\n+beta\n",
    "status": "modified",
    "additions": 1,
    "deletions": 1,
}


def test_tools_take_the_names_and_paths_clients_know() -> None:
    kind, _, call = normalize.tool_call("c1", "shell", {"command": "ls"})
    assert (kind, call["tool_name"]) == (EventKind.TOOL_CALL, "bash")
    assert normalize.tool_name("subagent") == "task"

    _, _, call = normalize.tool_call("c2", "edit", {"path": "a.txt"})
    assert call["tool_input"] == {"path": "a.txt", "file_path": "a.txt"}
    _, _, call = normalize.tool_call("c3", "glob", {"path": "src"})
    assert call["tool_input"] == {"path": "src"}


def test_a_result_joins_its_text_and_shows_the_diffs_it_applied() -> None:
    kind, text, metadata = normalize.tool_result(
        "c1",
        "edit",
        {"path": "a.txt"},
        content=[
            {"type": "text", "text": "Edited"},
            {"type": "file", "uri": "data:..."},
            {"type": "text", "text": "done"},
        ],
        files=[PATCH],
    )

    assert (kind, text) == (EventKind.TOOL_RESULT, "Result for edit:\nEdited\ndone")
    assert metadata["is_error"] is False
    assert metadata["item_id"] == metadata["tool_use_id"] == "c1"
    assert metadata["diff_preview"]["phase"] == "applied"
    assert "attachments" not in metadata


def test_a_failed_result_carries_the_error_and_no_diff() -> None:
    _, text, metadata = normalize.tool_result(
        "c1", "shell", {}, error={"type": "x", "message": "not now"}, files=[PATCH]
    )

    assert text == "Error: not now"
    assert metadata["is_error"] is True
    assert "diff_preview" not in metadata


def _request(**extra: Any) -> dict[str, Any]:
    return {
        "id": "per_1",
        "sessionID": "ses_1",
        "action": "external_directory",
        "resources": ["/a/*", "/b/*"],
        "source": {"type": "tool", "messageID": "m", "id": "call_1"},
        **extra,
    }


def test_an_approval_without_its_tool_call_describes_the_request() -> None:
    permission = Permission.parse(_request())
    assert permission is not None
    kind, text, metadata = normalize.approval_request(
        permission, name=None, raw_input=None, from_subagent=False
    )

    assert kind == EventKind.APPROVAL_REQUEST
    assert text == "External Directory: external_directory (/a/*, /b/*)"
    assert metadata["approval_id"] == "per_1"
    assert metadata["approval"]["approval_id"] == "per_1"
    assert metadata["approval"]["decisions"] == [
        "approve",
        "acceptForSession",
        "decline",
    ]
    assert metadata["interaction"]["request_id"] == "per_1"
    assert metadata["item_id"] == "call_1"
    assert metadata["tool_input"] == {
        "action": "external_directory",
        "resources": ["/a/*", "/b/*"],
    }
    assert "diff_preview" not in metadata


def test_a_form_is_an_ask_user_question() -> None:
    form = parse_form(
        {
            "id": "frm_1",
            "sessionID": "ses_1",
            "fields": [{"key": "q0", "type": "string", "description": "Name?"}],
        }
    )
    assert form is not None

    emission = normalize.question(form)

    assert emission is not None
    kind, _, metadata = emission
    assert kind == EventKind.TOOL_CALL
    assert metadata["tool_name"] == "AskUserQuestion"
    assert metadata["tool_use_id"] == "frm_1"
    assert metadata["payload"]["input"]["questions"][0]["question"] == "Name?"
    assert metadata["interaction"]["kind"] == "question"


def test_question_endings_correlate_with_their_card() -> None:
    for answered, reason in ((True, "provider_replied"), (False, "provider_rejected")):
        _, _, metadata = normalize.question_closed("frm_1", answered=answered)
        assert metadata["kind"] == "ask_user_question_closed"
        assert (metadata["tool_use_id"], metadata["reason"]) == ("frm_1", reason)


def test_a_turns_changes_are_an_aggregate_preview() -> None:
    emission = normalize.turn_changes([PATCH])

    assert emission is not None
    _, text, metadata = emission
    assert text == "Changes: +1 -1"
    assert metadata["diff_preview"]["phase"] == "aggregate"
    assert normalize.turn_changes([]) is None


def test_history_replays_the_conversation() -> None:
    messages = [
        {"type": "agent-switched", "id": "m0", "agent": "plan"},
        {"type": "user", "id": "m1", "text": "fix it", "time": {"created": 1000}},
        {"type": "synthetic", "id": "m2", "text": "<background-result/>"},
        {"type": "system", "id": "m3", "text": "system"},
        {
            "type": "assistant",
            "id": "m4",
            "time": {"created": 2000, "completed": 5000},
            "content": [
                {"type": "reasoning", "text": "thinking"},
                {
                    "type": "tool",
                    "id": "c1",
                    "name": "edit",
                    "time": {"created": 3000, "completed": 4000},
                    "state": {
                        "status": "completed",
                        "input": {"path": "a.txt"},
                        "content": [{"type": "text", "text": "Edited"}],
                        "metadata": {"files": [PATCH]},
                    },
                },
                {
                    "type": "tool",
                    "id": "c2",
                    "name": "shell",
                    "state": {
                        "status": "error",
                        "input": {},
                        "error": {"message": "no"},
                    },
                },
                {
                    "type": "tool",
                    "id": "c3",
                    "name": "shell",
                    "state": {"status": "running", "input": {}},
                },
                {"type": "text", "text": "done"},
                {"type": "text", "text": "and more"},
            ],
        },
        {
            "type": "shell",
            "id": "m5",
            "shellID": "sh_1",
            "command": "ls",
            "output": {"output": "a.txt\n"},
            "time": {"created": 6000, "completed": 7000},
        },
        {"type": "idle", "id": "m6", "outcome": "succeeded"},
    ]

    events = normalize.historical_events_from_messages(messages)

    assert [(kind, text.split("\n")[0]) for _, kind, text, _ in events] == [
        (EventKind.USER_INPUT, "fix it"),
        (EventKind.AGENT_OUTPUT, "thinking"),
        (EventKind.TOOL_CALL, "edit({"),
        (EventKind.TOOL_RESULT, "Result for edit:"),
        (EventKind.TOOL_CALL, "bash({})"),
        (EventKind.TOOL_RESULT, "Error: no"),
        (EventKind.TOOL_CALL, "bash({})"),
        (EventKind.AGENT_OUTPUT, "done"),
        (EventKind.AGENT_OUTPUT, "and more"),
        (EventKind.TOOL_CALL, "bash({"),
        (EventKind.TOOL_RESULT, "Result for bash:"),
    ]
    assert events[0][0] == datetime.fromtimestamp(1, tz=UTC)
    assert events[3][0] == datetime.fromtimestamp(4, tz=UTC)
    assert events[1][3]["item_kind"] == "reasoning"
    assert [events[i][3]["item_id"] for i in (1, 7, 8)] == [
        "m4:reasoning:0",
        "m4:text:0",
        "m4:text:1",
    ]
    assert events[3][3]["diff_preview"]["phase"] == "applied"
    assert all(item[3]["status"] == SessionStatus.RUNNING for item in events)


def test_a_user_turn_without_text_is_skipped() -> None:
    messages = [{"type": "user", "id": "m1", "text": "", "files": [{"name": "x"}]}]

    assert normalize.historical_events_from_messages(messages) == []
