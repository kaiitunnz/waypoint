"""Offline validation of OpenCode composer-submit detection used by the tmux
submit-confirm loop. Captured from a live OpenCode 1.17.7 pane: the composer is
the run of ``┃`` lines directly above the *only* ``╹▀▀`` border (its bottom),
with the typed message at the top of the box and the model/status footer at the
bottom. Submitted messages echo in ``┃`` boxes higher up, but those carry no
``╹`` border, so submission is keyed on the sent text leaving the composer box."""

from waypoint.backends.opencode.pane import composer_ready, composer_submitted

SENT = "Reply with exactly the token X and nothing else.\n\nAttached files:\n- /tmp/a"

# Live capture with the message typed but not yet submitted: a prior turn echoes
# in a borderless ``┃`` box at the top, the composer box sits at the bottom
# closed by the lone ``╹`` border.
POPULATED = "\n".join(
    [
        "",
        "  ┃",
        "  ┃  Say PING-1 and stop.",
        "  ┃",
        "",
        "     PING-1",
        "",
        "",
        "  ┃",
        "  ┃  Reply with exactly the token X and nothing else.",
        "  ┃",
        "  ┃  Attached files:",
        "  ┃  - /tmp/a",
        "  ┃  Build · Gemini 3.1 Pro Preview Google · high",
        "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀",
        "                                            9.8K (1%)",
    ]
)

# Same pane just after submitting: the composer is empty and the just-sent
# message now echoes in the transcript box at the top.
AFTER_SUBMIT = "\n".join(
    [
        "",
        "  ┃",
        "  ┃  Reply with exactly the token X and nothing else.",
        "  ┃",
        "",
        "     X",
        "",
        "",
        "  ┃",
        "  ┃",
        "  ┃",
        "  ┃  Build · Gemini 3.1 Pro Preview Google · high",
        "  ╹▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀",
        "                                            9.8K (1%)",
    ]
)


def test_not_submitted_while_message_in_composer_box() -> None:
    assert composer_submitted(POPULATED, SENT) is False


def test_submitted_when_composer_is_empty_after_send() -> None:
    # The just-sent message echoes in the transcript box at the top; the scan
    # must stay within the composer box so that echo is not mistaken for an
    # unsent message (the regression that made every send burn the full retry
    # budget).
    assert composer_submitted(AFTER_SUBMIT, SENT) is True


def test_submitted_when_composer_shows_placeholder() -> None:
    pane = "\n".join(
        [
            "  ┃",
            "  ┃  Reply with exactly the token X and nothing else.",  # transcript echo
            "  ┃",
            "",
            "  ┃",
            '  ┃  Ask anything... "Fix broken tests"',
            "  ┃",
            "  ┃  Build · Gemini 3.1 Pro Preview Google · high",
            "  ╹▀▀▀▀▀▀▀▀",
            "                                            9.8K (1%)",
        ]
    )
    assert composer_submitted(pane, SENT) is True


def test_not_submitted_when_message_taller_than_a_fixed_window() -> None:
    # The message start renders at the TOP of the composer box, far from the
    # bottom border; scanning the whole box (not a fixed window) keeps it found.
    pane = "\n".join(
        [
            "  ┃",
            "  ┃  earlier conversation",
            "  ┃",
            "",
            "  ┃",
            "  ┃  Reply with exactly the token X and nothing else.",
            "  ┃  line 2 of the pasted message",
            "  ┃  line 3 of the pasted message",
            "  ┃  line 4 of the pasted message",
            "  ┃  Attached files:",
            "  ┃  - /tmp/a",
            "  ┃  Build · Gemini 3.1 Pro Preview Google · high",
            "  ╹▀▀▀▀▀▀▀▀",
            "                                            9.8K (1%)",
        ]
    )
    assert composer_submitted(pane, SENT) is False


def test_not_submitted_when_no_border_found() -> None:
    # No composer box drawn → booting/dead pane, nothing submitted; keep trying.
    assert composer_submitted("booting opencode...", SENT) is False


def test_empty_message_treated_as_submitted() -> None:
    assert composer_submitted("┃  Ask anything...\n╹▀▀▀", "") is True


def test_composer_ready_when_box_drawn() -> None:
    assert composer_ready("┃  Ask anything...\n╹▀▀▀▀▀▀▀▀") is True


def test_composer_not_ready_while_booting() -> None:
    assert composer_ready("starting opencode...\nno box yet") is False
