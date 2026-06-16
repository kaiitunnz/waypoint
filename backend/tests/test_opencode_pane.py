"""Offline validation of OpenCode composer-submit detection used by the tmux
submit-confirm loop. The composer is a ``┃`` box closed by a ``╹▀▀`` border;
submitted messages echo in ``┃`` boxes above, so the live composer is the
region directly above the *last* border. Submission is keyed on the sent text
leaving that region (an empty composer shows the ``Ask anything…`` placeholder)."""

from waypoint.backends.opencode.pane import composer_ready, composer_submitted

SENT = "Reply with exactly the token X and nothing else.\n\nAttached files:\n- /tmp/a"


def test_not_submitted_while_message_in_composer_box() -> None:
    pane = "\n".join(
        [
            "┃  earlier conversation",
            "╹▀▀▀▀▀▀▀▀",
            "  tab agents  ctrl+p commands",
            "┃",
            "┃  Reply with exactly the token X and nothing else.",
            "┃",
            "┃  Build · Gemini 3.1 Pro Preview Google · high",
            "╹▀▀▀▀▀▀▀▀",
            "  tab agents  ctrl+p commands",
        ]
    )
    assert composer_submitted(pane, SENT) is False


def test_submitted_when_composer_shows_placeholder() -> None:
    # The message echoes in a box above; the live composer (above the last
    # border) is back to the placeholder.
    pane = "\n".join(
        [
            "┃  Reply with exactly the token X and nothing else.",  # transcript echo
            "╹▀▀▀▀▀▀▀▀",
            "┃",
            '┃  Ask anything... "Fix broken tests"',
            "┃",
            "┃  Build · Gemini 3.1 Pro Preview Google · high",
            "╹▀▀▀▀▀▀▀▀",
            "  tab agents  ctrl+p commands",
        ]
    )
    assert composer_submitted(pane, SENT) is True


def test_not_submitted_when_message_taller_than_a_fixed_window() -> None:
    # A long pasted message renders its start at the TOP of the composer box,
    # far from the bottom border; the scan must cover the whole box (between the
    # previous border and the last) so the probe is still found and the loop
    # does not declare a premature success.
    pane = "\n".join(
        [
            "┃  earlier conversation",
            "╹▀▀▀▀▀▀▀▀",
            "┃",
            "┃  Reply with exactly the token X and nothing else.",
            "┃  line 2 of the pasted message",
            "┃  line 3 of the pasted message",
            "┃  line 4 of the pasted message",
            "┃  Attached files:",
            "┃  - /tmp/a",
            "┃  Build · Gemini 3.1 Pro Preview Google · high",
            "╹▀▀▀▀▀▀▀▀",
            "  tab agents  ctrl+p commands",
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
