"""The ``summary`` a tool call carries: the agent's own description of it, taken
from the field each agent writes for that purpose and absent otherwise."""

import asyncio
import shlex
from typing import Any

import pytest
from openai_codex.generated.v2_all import Turn

from waypoint.backends.claude_code.adapter import ClaudeCliAdapter, ClaudeSessionState
from waypoint.backends.claude_code.history import convert_transcript_records
from waypoint.backends.claude_code.normalize import call_summary
from waypoint.backends.claude_tty.normalize import TranscriptNormalizer
from waypoint.backends.codex import normalize as codex_normalize
from waypoint.backends.codex.event_registry import render_item_started
from waypoint.backends.codex.history import turns_to_events
from waypoint.backends.codex.normalize import approval_summary, command_summary
from waypoint.backends.events import set_summary
from waypoint.backends.opencode import normalize as opencode
from waypoint.backends.opencode.sessions import Permission
from waypoint.schemas import EventKind

BASH_INPUT = {"command": "git status --short", "description": "Check for changes"}


def _tool_use(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """An assistant record as Claude Code writes it, with synthetic ids."""
    return {
        "type": "assistant",
        "uuid": "record-1",
        "timestamp": "2026-04-29T15:47:10.000Z",
        "message": {
            "id": "message-1",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu-1",
                    "name": name,
                    "input": tool_input,
                    "caller": {"type": "direct"},
                }
            ],
            "stop_reason": "tool_use",
        },
    }


@pytest.mark.parametrize("tool", ["Bash", "Agent", "Task", "Monitor", "PowerShell"])
def test_claude_tools_with_a_description_of_the_call(tool: str) -> None:
    assert call_summary(tool, {"description": "Do the thing"}) == "Do the thing"


@pytest.mark.parametrize(
    "tool",
    ["Write", "Artifact", "Workflow", "TaskCreate", "mcp__calendar__create_event"],
)
def test_a_description_argument_of_another_tool_is_not_a_summary(tool: str) -> None:
    assert call_summary(tool, {"description": "Quarterly review with finance"}) is None


@pytest.mark.parametrize(
    "tool_input", [{}, {"description": ""}, {"description": 3}, None, "ls"]
)
def test_claude_without_a_description_gives_none(tool_input: Any) -> None:
    metadata = set_summary({}, call_summary("Bash", tool_input))
    assert "summary" not in metadata


def test_summary_is_trimmed_and_absent_when_blank() -> None:
    assert set_summary({}, "  Run tests \n") == {"summary": "Run tests"}
    assert set_summary({}, "Run the tests\r\n\n  rm -rf a") == {
        "summary": "Run the tests rm -rf a"
    }
    assert set_summary({}, "   ") == {}
    assert set_summary({}, None) == {}


def _claude_emitted() -> tuple[ClaudeCliAdapter, list[tuple[EventKind, dict]]]:
    emitted: list[tuple[EventKind, dict]] = []

    async def emit(session_id, kind, text, metadata, status):
        emitted.append((kind, metadata))

    return ClaudeCliAdapter(emit), emitted


def _claude_state(adapter: ClaudeCliAdapter) -> ClaudeSessionState:
    state = ClaudeSessionState(
        session_id="sess",
        cwd="/tmp",
        process=None,  # type: ignore[arg-type]
        claude_session_id="claude-session",
        stdout_task=asyncio.create_task(asyncio.sleep(0)),
        stderr_task=asyncio.create_task(asyncio.sleep(0)),
        wait_task=asyncio.create_task(asyncio.sleep(0)),
    )
    adapter._sessions["sess"] = state
    return state


async def test_structured_claude_call_and_approval_carry_the_summary() -> None:
    adapter, emitted = _claude_emitted()
    state = _claude_state(adapter)

    await adapter._handle_assistant(state, _tool_use("Bash", BASH_INPUT))
    await adapter._handle_can_use_tool(
        state,
        {
            "type": "control_request",
            "request_id": "request-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "tool_use_id": "toolu-1",
                "input": BASH_INPUT,
            },
        },
    )

    kinds = {kind: metadata for kind, metadata in emitted}
    assert kinds[EventKind.TOOL_CALL]["summary"] == "Check for changes"
    assert kinds[EventKind.APPROVAL_REQUEST]["summary"] == "Check for changes"


async def test_structured_claude_mcp_call_has_no_summary() -> None:
    adapter, emitted = _claude_emitted()
    state = _claude_state(adapter)
    event = {"title": "Standup", "description": "Daily sync with the team"}

    await adapter._handle_assistant(
        state, _tool_use("mcp__calendar__create_event", event)
    )
    await adapter._handle_can_use_tool(
        state,
        {
            "type": "control_request",
            "request_id": "request-1",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "mcp__calendar__create_event",
                "tool_use_id": "toolu-1",
                "input": event,
            },
        },
    )

    assert [kind for kind, _ in emitted] == [
        EventKind.TOOL_CALL,
        EventKind.APPROVAL_REQUEST,
    ]
    assert all("summary" not in metadata for _, metadata in emitted)


@pytest.mark.parametrize(
    ("tool_input", "expected"),
    [(BASH_INPUT, "Check for changes"), ({"command": "ls"}, None)],
)
def test_claude_tty_and_history_calls(
    tool_input: dict[str, Any], expected: str | None
) -> None:
    record = _tool_use("Bash", tool_input)

    [tty] = [
        event
        for event in TranscriptNormalizer().process_record(record)
        if event.kind is EventKind.TOOL_CALL
    ]
    [imported] = [
        event
        for event in convert_transcript_records("sess", [record])
        if event.kind is EventKind.TOOL_CALL
    ]

    assert tty.metadata.get("summary") == expected
    assert imported.metadata.get("summary") == expected
    assert ("summary" in tty.metadata) is (expected is not None)


def test_claude_agent_call_is_summarized() -> None:
    record = _tool_use(
        "Agent",
        {"description": "Find callers", "prompt": "List them", "subagent_type": "x"},
    )

    [event] = [
        event
        for event in TranscriptNormalizer().process_record(record)
        if event.kind is EventKind.TOOL_CALL
    ]

    assert event.metadata["summary"] == "Find callers"


SUBAGENT_INPUT = {"description": "Map the callers", "prompt": "List them"}


def test_opencode_subagent_calls_carry_their_description() -> None:
    _, _, call = opencode.tool_call("call-1", "subagent", SUBAGENT_INPUT)
    [history_call, _] = [
        metadata
        for _, _, _, metadata in opencode.historical_events_from_messages(
            [
                {
                    "type": "assistant",
                    "id": "message-1",
                    "time": {"created": 1_700_000_000_000},
                    "content": [
                        {
                            "type": "tool",
                            "id": "call-1",
                            "name": "subagent",
                            "state": {"status": "completed", "input": SUBAGENT_INPUT},
                        }
                    ],
                }
            ]
        )
    ]

    assert call["summary"] == history_call["summary"] == "Map the callers"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("shell", {"command": "ls", "description": "List files"}),
        ("shell", {"command": "ls"}),
        ("read", {"path": "a.txt"}),
        ("subagent", {"prompt": "List them"}),
        ("calendar_create", {"description": "Daily sync with the team"}),
    ],
)
def test_opencode_other_calls_have_no_summary(
    tool: str, tool_input: dict[str, str]
) -> None:
    _, _, call = opencode.tool_call("call-1", tool, tool_input)

    assert "summary" not in call


def _permission() -> Permission:
    return Permission(
        id="permission-1",
        session_id="session-1",
        action="subagent",
        resources=("general",),
        save=(),
        call_id="call-1",
    )


def test_opencode_approval_carries_the_call_summary() -> None:
    _, _, described = opencode.approval_request(
        _permission(), name="subagent", raw_input=SUBAGENT_INPUT, from_subagent=False
    )
    _, _, bare = opencode.approval_request(
        _permission(), name="shell", raw_input={"command": "ls"}, from_subagent=False
    )

    assert described["summary"] == "Map the callers"
    assert "summary" not in bare


def _read(name: str) -> dict[str, str]:
    return {"type": "read", "command": f"cat {name}", "name": name, "path": name}


SEARCH = {
    "type": "search",
    "command": "rg -n x src",
    "query": "permission mode",
    "path": "src",
}
UNKNOWN = {"type": "unknown", "command": "git status --short"}


def _wrapped(script: str) -> str:
    return shlex.join(["/bin/bash", "-lc", script])


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (_read("SKILL.md"), "Read `SKILL.md`"),
        (SEARCH, "Search `permission mode`"),
        ({**SEARCH, "query": None}, "Search `src`"),
        ({"type": "search", "command": "rg x"}, "Search files"),
        ({"type": "listFiles", "command": "ls src", "path": "src"}, "List `src`"),
        ({"type": "listFiles", "command": "ls"}, "List files"),
        (UNKNOWN, None),
        # A name that would break the backticks marking it.
        ({"type": "read", "command": "cat 'a`b'", "name": "a`b"}, None),
        ({"type": "write", "command": "tee a"}, None),
    ],
)
def test_codex_command_summary_names_the_action(
    action: dict[str, Any], expected: str | None
) -> None:
    assert command_summary(_wrapped(action["command"]), [action]) == expected
    # A command line Codex did not wrap in a shell reads the same.
    assert command_summary(action["command"], [action]) == expected


@pytest.mark.parametrize("actions", [[], None, [_read("a"), _read("b")]])
def test_codex_command_summary_needs_one_action(actions: Any) -> None:
    assert command_summary(_wrapped("cat a"), actions) is None


@pytest.mark.parametrize(
    "script",
    [
        "cat README.md",
        "cat README.md | wc -l",
        "cat README.md | head -n 50",
        "cat README.md | head -50",
        "cat README.md|tail -n +2|nl -ba",
        "cat README.md | head --lines=5 | wc -lw",
    ],
)
def test_codex_command_summary_allows_trimmed_output(script: str) -> None:
    summary = command_summary(_wrapped(script), [_read("README.md")])

    assert summary == "Read `README.md`"


@pytest.mark.parametrize(
    "script",
    [
        # Codex reports only the read for each of these.
        "cat README.md | tee /dev/null",
        "cat README.md && printf ok",
        "cat README.md | xargs echo found",
        "cat README.md | tr a-z A-Z",
        "cat README.md | sort",
        "cat README.md || true",
        "cat README.md; printf ok",
        "cat README.md\nprintf ok",
        "cat README.md &",
        "cat README.md |& head",
        "cat README.md |",
        "cat README.md | head > out",
        "cat README.md > out",
        "cat README.md 2>/dev/null",
        "cat < README.md",
        "cat README.md | head -n 5 b",
        "cat README.md | head b",
        "cat README.md | wc -l 5",
        "cat README.md | wc --files0-from=names",
        "cat README.md | head -n",
        "cat README.md $(printf b)",
        "cat README.md `printf b`",
        "(cat README.md)",
        'cat README.md "$HOME"',
        "cat README.md # note",
        "cat README.md *",
        "cat README.md 'unterminated",
        "cat other.md",
    ],
)
def test_codex_command_summary_needs_the_whole_command(script: str) -> None:
    assert command_summary(_wrapped(script), [_read("README.md")]) is None


@pytest.mark.parametrize(
    ("script", "parsed", "covered"),
    [
        ("rg x | head -n 50", "rg x", True),
        ("rg -l hello . | xargs echo found", "rg -l hello .", False),
        ("rg -n 'a && b' src | head -n 5", "rg -n 'a && b' src", True),
        ('rg -g "*.py" x', "rg -g '*.py' x", True),
    ],
)
def test_codex_command_summary_reads_quoting(
    script: str, parsed: str, covered: bool
) -> None:
    action = {"type": "search", "command": parsed, "query": "x"}

    assert (command_summary(_wrapped(script), [action]) is not None) is covered


def _command_item(actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "commandExecution",
        "id": "item-1",
        "command": "/bin/bash -lc 'cat SKILL.md'",
        "cwd": "/work",
        "status": "completed",
        "source": "agent",
        "commandActions": actions,
        "aggregatedOutput": "text",
        "exitCode": 0,
    }


@pytest.mark.parametrize(
    ("actions", "expected"),
    [([_read("SKILL.md")], "Read `SKILL.md`"), ([UNKNOWN], None)],
)
def test_codex_live_and_imported_commands(
    actions: list[dict[str, Any]], expected: str | None
) -> None:
    item = _command_item(actions)
    turn = Turn.model_validate(
        {
            "id": "turn-1",
            "status": "completed",
            "startedAt": 1_700_000_000,
            "completedAt": 1_700_000_010,
            "items": [item],
        }
    )

    started = render_item_started(item)
    [imported, result] = turns_to_events([turn], "sess")

    assert started is not None
    assert started.metadata.get("summary") == expected
    assert imported.kind is EventKind.TOOL_CALL
    assert imported.metadata.get("summary") == expected
    assert "summary" not in result.metadata


# Command lines Codex parses as one read or search although their program runs more,
# or reads more than the file it names. Captured from live sessions.
PROGRAM_NAMED = [
    (
        '/bin/bash -lc "awk \'BEGIN{system(\\"echo ran\\")}\' README.md"',
        {"type": "read", "name": "README.md"},
        "Read `README.md`",
    ),
    (
        "/bin/bash -lc \"sed -n -e 1p -e 'e echo ran' README.md\"",
        {"type": "read", "name": "README.md"},
        "Read `README.md`",
    ),
    (
        "/bin/bash -lc 'rg --pre=cat hello .'",
        {"type": "search", "query": "hello"},
        "Search `hello`",
    ),
    (
        "/bin/bash -lc 'head -n 2 README.md notes.txt'",
        {"type": "read", "name": "README.md"},
        "Read `README.md`",
    ),
]


@pytest.mark.parametrize(("command", "action", "heading"), PROGRAM_NAMED)
def test_codex_heads_a_call_by_its_parse_and_never_its_approval(
    command: str, action: dict[str, str], heading: str
) -> None:
    actions = [{**action, "command": shlex.split(command)[2]}]
    params = {"command": command, "commandActions": actions}

    assert command_summary(command, actions) == heading
    assert approval_summary("item/commandExecution/requestApproval", params) is None


def test_codex_approval_takes_no_summary() -> None:
    params = {"command": "cat SKILL.md", "commandActions": [_read("SKILL.md")]}

    assert approval_summary("item/commandExecution/requestApproval", params) is None


def test_codex_approval_summaries_switched_on_keep_the_reason_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codex_normalize, "SUMMARIZE_APPROVALS", True)
    method = "item/commandExecution/requestApproval"
    params: dict[str, Any] = {
        "command": "cat SKILL.md",
        "reason": None,
        "commandActions": [_read("SKILL.md")],
    }

    assert approval_summary(method, params) == "Read `SKILL.md`"
    assert approval_summary(method, {**params, "commandActions": [UNKNOWN]}) is None
    assert approval_summary(method, {**params, "commandActions": None}) is None
    assert approval_summary(method, {**params, "command": None}) is None
    assert approval_summary("item/fileChange/requestApproval", params) is None
    # A reason is the only trace of an ask beyond the command.
    assert approval_summary(method, {**params, "reason": "Run unsandboxed"}) is None
