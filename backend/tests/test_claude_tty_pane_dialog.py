"""Offline validation of claude_tty pane-dialog detection against captured
fixtures (Claude Code 2.1.177). Proves detection/parsing before any live
keystroke wiring.
"""

from pathlib import Path

import pytest

from waypoint.backends.claude_tty.pane_dialog import (
    PaneScreen,
    classify,
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
        ("trust_dialog.txt", PaneScreen.TRUST),
        ("model_selector.txt", PaneScreen.MODEL_SELECTOR),
        ("effort_popup.txt", PaneScreen.EFFORT_POPUP),
        ("ready.txt", PaneScreen.OTHER),
        ("slash_menu.txt", PaneScreen.OTHER),
    ],
)
def test_classify(fixture: str, expected: PaneScreen) -> None:
    assert classify(_load(fixture)) is expected


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
