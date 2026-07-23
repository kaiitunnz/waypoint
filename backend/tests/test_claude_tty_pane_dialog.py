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
    composer_ready,
    parse_approval,
    parse_model_selector,
    parse_plan_dialog,
    shows_blocking_dialog,
)

FIXTURES = Path(__file__).parent / "fixtures" / "claude_tty_pane"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize(
    "fixture, expected",
    [
        ("approval_write.txt", PaneScreen.APPROVAL),
        ("approval_bash.txt", PaneScreen.APPROVAL),
        ("plan_approval.txt", PaneScreen.PLAN),
        ("question_dialog.txt", PaneScreen.QUESTION),
        ("question_dialog_single.txt", PaneScreen.QUESTION),
        ("question_dialog_notes.txt", PaneScreen.QUESTION),
        ("question_with_one_queued.txt", PaneScreen.QUESTION),
        ("question_with_two_queued.txt", PaneScreen.QUESTION),
        ("approval_with_queued.txt", PaneScreen.APPROVAL),
        ("trust_dialog.txt", PaneScreen.TRUST),
        ("model_selector.txt", PaneScreen.MODEL_SELECTOR),
        ("effort_popup.txt", PaneScreen.EFFORT_POPUP),
        ("ready.txt", PaneScreen.OTHER),
        ("slash_menu.txt", PaneScreen.OTHER),
    ],
)
def test_classify(fixture: str, expected: PaneScreen) -> None:
    assert classify(_load(fixture)) is expected


@pytest.mark.parametrize(
    "fixture, blocks",
    [
        ("approval_write.txt", True),
        ("approval_bash.txt", True),
        ("plan_approval.txt", True),
        ("question_dialog.txt", True),
        ("question_with_one_queued.txt", True),
        ("question_with_two_queued.txt", True),
        ("approval_with_queued.txt", True),
        ("trust_dialog.txt", True),
        ("model_selector.txt", True),
        ("effort_popup.txt", True),
        ("ready.txt", False),
        ("slash_menu.txt", False),
    ],
)
def test_shows_blocking_dialog(fixture: str, blocks: bool) -> None:
    # The composer glyph ❯ also marks a dialog's selected option, so the tmux
    # transport must treat any non-composer screen as blocking and refuse to
    # paste/Enter into it (which would auto-select — e.g. approve a tool).
    assert shows_blocking_dialog(_load(fixture)) is blocks


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
    # The plan dialog is its own screen, not a tool-permission prompt.
    assert parse_approval(_load("plan_approval.txt")) is None


def test_parse_plan_dialog() -> None:
    dialog = parse_plan_dialog(_load("plan_approval.txt"))
    assert dialog is not None
    assert [(o.number, o.label) for o in dialog.options] == [
        (1, "Yes, and use auto mode"),
        (2, "Yes, manually approve edits"),
        (3, "No, refine with Ultraplan on Claude Code on the web"),
        (4, "Tell Claude what to change"),
    ]
    # Options are selected by label, not position: manual exits to default,
    # auto exits to the "auto" permission mode.
    assert dialog.manual_option is not None and dialog.manual_option.number == 2
    assert dialog.auto_option is not None and dialog.auto_option.number == 1
    assert dialog.plan_path == "~/.claude/plans/make-a-plan-to-linear-hennessy.md"


def test_plan_dialog_options_selected_by_label_not_position() -> None:
    # A plan whose subscription omits the "auto mode" option: manual must still
    # resolve (by label, here at position 1) and auto must be absent.
    screen = "\n".join(
        [
            "Claude is ready to execute. Would you like to proceed?",
            "  ❯ 1. Yes, manually approve edits",
            "    2. No, keep planning",
            "       shift+tab to approve with this feedback",
        ]
    )
    dialog = parse_plan_dialog(screen)
    assert dialog is not None
    assert dialog.auto_option is None
    assert dialog.manual_option is not None and dialog.manual_option.number == 1


def test_manual_option_never_falls_back_to_auto_option() -> None:
    # No option says "manual" but an auto option exists: manual_option must skip
    # it and pick the other "Yes", so a default-target approval can't press
    # "use auto mode" and silently widen permissions.
    screen = "\n".join(
        [
            "Claude is ready to execute. Would you like to proceed?",
            "  ❯ 1. Yes, and use auto mode",
            "    2. Yes, proceed",
            "       shift+tab to approve with this feedback",
        ]
    )
    dialog = parse_plan_dialog(screen)
    assert dialog is not None
    assert dialog.auto_option is not None and dialog.auto_option.number == 1
    assert dialog.manual_option is not None and dialog.manual_option.number == 2


def test_parse_plan_dialog_returns_none_off_screen() -> None:
    assert parse_plan_dialog(_load("approval_write.txt")) is None
    assert parse_plan_dialog(_load("ready.txt")) is None


def test_plan_markers_quoted_above_live_composer_do_not_classify() -> None:
    # The plan question quoted in settled transcript, with a live composer below.
    # The active-region scope drops everything above the composer, so a session
    # merely discussing a plan dialog is not read as having one open.
    screen = "\n".join(
        [
            "I am ready to execute. Would you like to proceed? (from the docs)",
            "  ❯ 1. Yes, and use auto mode",
            "    2. Yes, manually approve edits",
            "",
            '❯ Try "edit a file"',
        ]
    )
    assert classify(screen) is PaneScreen.OTHER
    assert parse_plan_dialog(screen) is None


def test_parse_approval_ignores_quoted_question_above_live_dialog() -> None:
    # A "Do you want to …?" quoted at line-start in the transcript, above a real
    # approval. classify() is region-scoped, so the screen is APPROVAL; the parser
    # must return the live dialog's question (the bottom-most), not the quote.
    screen = "\n".join(
        [
            "Do you want to delete the_old_file.txt?",
            "",
            " Create file",
            "   probe_out.txt",
            " Do you want to create probe_out.txt?",
            " ❯ 1. Yes",
            "   2. No",
            " Esc to cancel · Tab to amend",
        ]
    )
    dialog = parse_approval(screen)
    assert dialog is not None
    assert dialog.question == "Do you want to create probe_out.txt?"
    assert (dialog.tool_name, dialog.target) == ("Write", "probe_out.txt")


def test_parse_model_selector_ignores_quoted_options_above_live_popup() -> None:
    # A quoted "Select model" + option above the live /model popup must not pull
    # spurious rows into the parse; anchor to the bottom-most "Select model".
    screen = "\n".join(
        [
            "Select model",
            "  ❯ 1. Fake Quoted Model    bogus",
            "❯ /model",
            "  Select model",
            "  ❯ 1. Default (recommended) real",
            "    2. Sonnet                real",
            "  Enter to set as default · Esc to cancel",
        ]
    )
    options = parse_model_selector(screen)
    assert options is not None
    assert [o.label for o in options] == [
        "Default (recommended) real",
        "Sonnet                real",
    ]


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


def test_quoted_dialog_markers_above_composer_do_not_block() -> None:
    # Regression: a session reasoning about this very parser printed the approval
    # markers into its transcript, then a live composer (with a typed reply) sat
    # below them. Whole-pane matching read the quoted markers as a live dialog and
    # refused every send, wedging the session. Detection must scope to the active
    # region at/below the composer, so quoted markers above it are inert.
    screen = _load("approval_quoted_in_transcript.txt")
    assert "Do you want to" in screen and "Esc to cancel · Tab to amend" in screen
    assert classify(screen) is PaneScreen.OTHER
    assert shows_blocking_dialog(screen) is False
    assert parse_approval(screen) is None


def test_classify_scopes_to_region_below_live_composer() -> None:
    # A constructed pane: approval markers quoted in the transcript, then the live
    # composer below with the user's reply. The composer is the bottom-most prompt,
    # so the quoted markers are settled transcript and must not classify.
    screen = "\n".join(
        [
            "● Discussing the approval dialog parser:",
            '  question marker is "Do you want to" and the footer is',
            "  Esc to cancel · Tab to amend",
            "────────────────────────────",
            "❯ yes, go ahead and implement it",
            "────────────────────────────",
            "  ? for shortcuts",
        ]
    )
    assert classify(screen) is PaneScreen.OTHER
    assert shows_blocking_dialog(screen) is False


def test_popup_below_slash_command_echo_still_classifies() -> None:
    # The /model and /effort popups render below the composer's own slash-command
    # echo (a free-text ❯ line). The active region must extend from that echo
    # downward so the popup — which is below it — is still detected.
    assert classify(_load("model_selector.txt")) is PaneScreen.MODEL_SELECTOR
    assert classify(_load("effort_popup.txt")) is PaneScreen.EFFORT_POPUP


def test_real_dialog_below_quoted_marker_still_classifies() -> None:
    # A quoted marker on a composer-prompt line — and as the bottom-most such line
    # above a genuine dialog that renders below it — must not suppress detection:
    # the region runs from that line down and still contains the live dialog.
    screen = "\n".join(
        [
            "● Earlier I explained the footer reads Esc to cancel · Tab to amend",
            "❯ ok, now actually delete the file",
            "",
            " Do you want to delete probe_out.txt?",
            " ❯ 1. Yes",
            "   2. No",
            "",
            " Esc to cancel · Tab to amend",
        ]
    )
    assert classify(screen) is PaneScreen.APPROVAL


def test_glyph_in_dialog_body_does_not_truncate_region() -> None:
    # Regression: a ❯ rendered *inside* the dialog (a diff-preview row, a command,
    # an option label quoting the glyph) must not be read as a composer prompt.
    # Treating a mid-line ❯ as a region boundary would slice the dialog between
    # its question and footer, dropping a real approval to OTHER — a false
    # negative that would let a send bulldoze a live prompt.
    # The glyph is injected *below* the option rows so it would be the bottom-most
    # ❯ under a naive "any ❯ is the composer" rule, slicing off the question above
    # — the test fails without the leading-prompt anchor.
    screen = _load("approval_write.txt").replace(
        "   3. No",
        '   3. No\n   1 print("❯ prompt")',
    )
    assert classify(screen) is PaneScreen.APPROVAL
    assert shows_blocking_dialog(screen) is True
    dialog = parse_approval(screen)
    assert (
        dialog is not None and dialog.question == "Do you want to create probe_out.txt?"
    )


def test_queued_messages_below_footer_do_not_mask_dialog() -> None:
    # Captured live (Claude Code 2.1.x): a human who sends a follow-up while the
    # turn is still generating has it queued and rendered as a leading-❯ line below
    # the live dialog's footer. The region selector must skip that trailing run
    # (any depth) rather than slice the pane to it, or the dialog above is dropped
    # and classify returns OTHER — the session hangs on an unanswerable prompt.
    one = _load("question_with_one_queued.txt")
    two = _load("question_with_two_queued.txt")
    assert "❯ QMSG-ONE" in one
    assert "❯ QMSG-ONE" in two and "❯ QMSG-TWO" in two
    assert classify(one) is PaneScreen.QUESTION
    assert classify(two) is PaneScreen.QUESTION
    assert shows_blocking_dialog(one) is True
    assert shows_blocking_dialog(two) is True


def test_approval_with_queued_still_parses() -> None:
    # A tool-approval dialog with queued messages beneath its footer must both
    # classify as APPROVAL and parse the live tool/target/options — the tailer
    # surfaces the card and the transport still refuses to send into it.
    dialog = parse_approval(_load("approval_with_queued.txt"))
    assert dialog is not None
    assert dialog.tool_name == "Write"
    assert dialog.target == "probe.txt"
    assert dialog.question == "Do you want to create probe.txt?"
    assert [o.number for o in dialog.options] == [1, 2, 3]
    assert dialog.approve_option is not None and dialog.approve_option.number == 1
    assert dialog.decline_option is not None and dialog.decline_option.number == 3


def test_freetext_answer_field_below_footer_classifies() -> None:
    # The AskUserQuestion "Type something" option opens a free-text ❯ field that
    # renders below the footer exactly like a single queued message (the N=1 case).
    # It must not be read as the live composer.
    screen = "\n".join(
        [
            " ☐ Fav color",
            "What is your favorite color?",
            "❯ 1. Red",
            "  2. Green",
            "  3. Blue",
            "  4. Type something.",
            "Enter to select · ↑/↓ to navigate · Esc to cancel",
            "",
            "  ❯ ",
        ]
    )
    assert classify(screen) is PaneScreen.QUESTION
    assert shows_blocking_dialog(screen) is True


def test_queued_messages_with_no_dialog_do_not_classify() -> None:
    # The no-dialog counterpart: queued messages sit in the transcript flow above
    # the *true* composer (the dim "Press up to edit queued messages" ghost),
    # whose status footer is present below it. No dialog footer rests under the
    # trailing run, so the region bounds at the true composer and stays OTHER.
    screen = "\n".join(
        [
            "❯ QMSG-ONE queued during the turn",
            "",
            "❯ QMSG-TWO queued during the turn",
            "────────────────────────────",
            "❯ Press up to edit queued messages",
            "────────────────────────────",
            "  ⏸ manual mode on · esc to interrupt · ← for agents",
        ]
    )
    assert classify(screen) is PaneScreen.OTHER
    assert shows_blocking_dialog(screen) is False


def test_quoted_footer_directly_above_composer_does_not_mask_it() -> None:
    # FR4 guard under the new run-aware skip: a dialog footer quoted in transcript,
    # directly above a live composer holding a typed reply (its status footer
    # below), must not be read as a live dialog. The composer does not rest on the
    # quoted footer — a rule line and the status footer sit between — so the skip
    # never fires and the region bounds at the composer.
    screen = "\n".join(
        [
            "● As I said the footer reads Esc to cancel · Tab to amend",
            "────────────────────────────",
            "❯ yes, go ahead and implement it",
            "────────────────────────────",
            "  ⏸ manual mode on · ? for shortcuts · ← for agents",
        ]
    )
    assert classify(screen) is PaneScreen.OTHER
    assert shows_blocking_dialog(screen) is False


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
    # No locatable composer → the pane is booting or dead, so nothing has been
    # submitted; report not-empty so the confirm loop keeps retrying.
    assert composer_is_empty("just some text\nno prompt here") is False


def test_composer_is_empty_for_idle_placeholder() -> None:
    # An idle composer renders dim ghost placeholder text after the prompt
    # (e.g. ``❯ Try "fix typecheck errors"``); it is empty for submit-confirm
    # purposes, so the confirm loop must not keep firing Enter at it.
    assert composer_is_empty(_load("ready.txt")) is True


def test_composer_ready_when_prompt_drawn() -> None:
    # The composer prompt is present once the TUI has booted.
    assert composer_ready("❯ ") is True
    assert composer_ready(_load("ready.txt")) is True


def test_composer_not_ready_while_booting() -> None:
    # A freshly relaunched pane has no prompt yet; the transport must wait
    # rather than paste into the boot screen.
    assert composer_ready("loading Claude...\nno prompt yet") is False
