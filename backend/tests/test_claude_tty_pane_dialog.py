"""Offline validation of claude_tty pane-dialog detection against captured
fixtures (Claude Code 2.1.177). Proves detection/parsing before any live
keystroke wiring.
"""

import re
from pathlib import Path

import pytest

from waypoint.backends.claude_tty.pane_dialog import (
    PaneScreen,
    classify,
    composer_is_empty,
    parse_approval,
    parse_model_selector,
)

FIXTURES = Path(__file__).parent / "fixtures" / "claude_tty_pane"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("approval_write.txt", PaneScreen.APPROVAL),
        ("approval_bash.txt", PaneScreen.APPROVAL),
        ("question_dialog.txt", PaneScreen.QUESTION),
        ("question_dialog_single.txt", PaneScreen.QUESTION),
        ("question_dialog_notes.txt", PaneScreen.QUESTION),
        ("trust_dialog.txt", PaneScreen.TRUST),
        ("model_selector.txt", PaneScreen.MODEL_SELECTOR),
        ("effort_popup.txt", PaneScreen.EFFORT_POPUP),
        ("ready.txt", PaneScreen.OTHER),
        ("slash_menu.txt", PaneScreen.OTHER),
    ],
)
def test_classify(fixture: str, expected: PaneScreen) -> None:
    assert classify(_load(fixture)) is expected


def test_question_footers_across_variants_classify() -> None:
    # Detection keys only on the stable "Enter to select"/"Esc to cancel" footer.
    # Everything else varies: the nav hint reads "↑/↓ to navigate" for one
    # question vs "Tab/Arrow keys to navigate" for several, and a dialog with
    # option previews offers free-text via "n to add notes" with no inline
    # "Type something" option — so requiring "Type something" misses it.
    single = _load("question_dialog_single.txt")
    multi = _load("question_dialog.txt")
    notes = _load("question_dialog_notes.txt")
    assert "↑/↓ to navigate" in single
    assert "Tab/Arrow keys to navigate" in multi
    assert "n to add notes" in notes and "Type something" not in notes
    assert classify(single) is PaneScreen.QUESTION
    assert classify(multi) is PaneScreen.QUESTION
    assert classify(notes) is PaneScreen.QUESTION


def test_question_dialog_not_mistaken_for_approval() -> None:
    # The AskUserQuestion popup carries options and a navigation footer but is
    # not a permission prompt; parse_approval must reject it so the tailer Escs
    # it rather than firing an approve/decline digit at it.
    assert parse_approval(_load("question_dialog.txt")) is None


def test_parse_write_approval() -> None:
    dialog = parse_approval(_load("approval_write.txt"))
    assert dialog is not None
    assert dialog.tool_name == "Write"
    assert dialog.target == "probe_out.txt"
    assert dialog.question == "Do you want to create probe_out.txt?"
    assert [(o.number, o.label) for o in dialog.options] == [
        (1, "Yes"),
        (2, "Yes, allow all edits during this session (shift+tab)"),
        (3, "No"),
    ]
    assert dialog.approve_option is not None and dialog.approve_option.number == 1
    assert dialog.approve_option.selected is True
    assert dialog.decline_option is not None and dialog.decline_option.number == 3


def test_parse_bash_approval() -> None:
    dialog = parse_approval(_load("approval_bash.txt"))
    assert dialog is not None
    assert dialog.tool_name == "Bash"
    assert dialog.target == "mkdir /tmp/cc-tty-probe/probe_subdir"
    assert dialog.question == "Do you want to proceed?"
    assert dialog.approve_option is not None and dialog.approve_option.number == 1
    assert dialog.decline_option is not None and dialog.decline_option.number == 3
    # The conditional always-allow option must not be mistaken for approve/decline.
    assert dialog.options[1].label.startswith("Yes, and always allow")


def test_diff_preview_numbers_are_not_options() -> None:
    # The Write dialog renders a numbered diff line ("  1 waypoint-probe") that
    # must not be parsed as a fourth option.
    dialog = parse_approval(_load("approval_write.txt"))
    assert dialog is not None
    assert [o.number for o in dialog.options] == [1, 2, 3]


def test_parse_approval_returns_none_for_non_approval() -> None:
    assert parse_approval(_load("ready.txt")) is None
    assert parse_approval(_load("trust_dialog.txt")) is None
    assert parse_approval(_load("model_selector.txt")) is None


def test_parse_model_selector() -> None:
    options = parse_model_selector(_load("model_selector.txt"))
    assert options is not None
    labels = [o.label for o in options]
    assert any(label.startswith("Default") for label in labels)
    assert any(label.startswith("Sonnet") for label in labels)
    selected = [o for o in options if o.selected]
    assert len(selected) == 1 and selected[0].number == 1


def test_parse_model_selector_returns_none_off_screen() -> None:
    assert parse_model_selector(_load("ready.txt")) is None


def test_classify_robust_to_spacing_variation() -> None:
    # A reflowed dialog with collapsed/expanded inter-word spacing must still
    # classify via the whitespace-compact anchor match.
    screen = _load("approval_write.txt")
    squeezed = re.sub(r" {2,}", " ", screen)
    padded = screen.replace("Tab to amend", "Tab   to   amend")
    assert classify(squeezed) is PaneScreen.APPROVAL
    assert classify(padded) is PaneScreen.APPROVAL


def test_parse_real_ansi_capture() -> None:
    # A pane captured with `tmux capture-pane -e` (as the live tailer does)
    # carries ANSI colour/cursor codes interleaved with the text. The detector
    # must strip them. This fixture is a real capture from a running session
    # whose Bash dialog rendered no "Bash(...)" header — tool/target must come
    # from the dialog body.
    screen = _load("approval_bash_ansi.txt")
    assert "\x1b" in screen  # guard: fixture really contains escapes
    assert classify(screen) is PaneScreen.APPROVAL
    dialog = parse_approval(screen)
    assert dialog is not None
    assert dialog.tool_name == "Bash"
    assert dialog.target == "mkdir /tmp/cctty-e2e-made"
    assert dialog.approve_option is not None and dialog.approve_option.number == 1
    assert dialog.decline_option is not None and dialog.decline_option.number == 3


def test_body_extraction_ignores_earlier_scrollback_label() -> None:
    # A line equal to a body label ("Bash command") sitting earlier in the
    # scrollback (e.g. echoed transcript text) must not win over the actual
    # dialog block at the bottom of the pane.
    real = _load("approval_bash.txt")
    decoy = "● I ran a Bash command\n Bash command\n   rm -rf /decoy\n\n"
    dialog = parse_approval(decoy + real)
    assert dialog is not None
    assert dialog.tool_name == "Bash"
    assert dialog.target == "mkdir /tmp/cc-tty-probe/probe_subdir"
    assert dialog.target != "rm -rf /decoy"


def test_classify_robust_to_wrapped_footer() -> None:
    # The footer wrapping across two lines breaks a raw substring match but not
    # the compact one.
    screen = _load("approval_bash.txt").replace(
        "Esc to cancel · Tab to amend", "Esc to cancel · Tab to\namend"
    )
    assert classify(screen) is PaneScreen.APPROVAL


def test_composer_is_empty_for_bare_prompt() -> None:
    # After a real submit the composer clears to a bare ``❯`` above the footer.
    screen = "\n".join(
        [
            "● Done",
            "────────────────────────────",
            "❯ ",
            "────────────────────────────",
            "  ⏸ plan mode on (shift+tab to cycle)",
        ]
    )
    assert composer_is_empty(screen) is True


def test_composer_not_empty_while_message_pending() -> None:
    # The pasted message (incl. an image chip) sits on the last ``❯`` line; a
    # prior submitted turn echoes above with the same glyph and must be ignored.
    screen = "\n".join(
        [
            "❯ earlier submitted message",
            "────────────────────────────",
            "❯ [Image #1]Describe the image.",
            "  Attached files:",
            "  - /tmp/x.png",
            "────────────────────────────",
            "  ⏸ plan mode on (shift+tab to cycle)",
        ]
    )
    assert composer_is_empty(screen) is False


def test_composer_is_empty_tolerates_ansi_and_nbsp() -> None:
    # capture-pane -e carries colour codes, and the prompt is padded with a
    # non-breaking space; both must be normalized before the emptiness check.
    screen = "\x1b[2m❯\xa0\x1b[0m   "
    assert composer_is_empty(screen) is True


def test_composer_is_empty_when_no_prompt_found() -> None:
    # No locatable composer → treat as empty so the caller does not retry
    # blindly (its loop is bounded regardless).
    assert composer_is_empty("just some text\nno prompt here") is True


def test_composer_is_empty_for_idle_placeholder() -> None:
    # An idle composer renders dim ghost placeholder text after the prompt
    # (e.g. ``❯ Try "fix typecheck errors"``); it is empty for submit-confirm
    # purposes, so the confirm loop must not keep firing Enter at it.
    assert composer_is_empty(_load("ready.txt")) is True
