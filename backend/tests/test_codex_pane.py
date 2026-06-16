"""Offline validation of Codex composer-submit detection used by the tmux
submit-confirm loop. Codex renders input literally and shows a rotating ghost
placeholder when empty, so submission is keyed on the sent text leaving the
last ``›`` prompt line (the live composer; submitted turns echo above it)."""

from waypoint.backends.codex.pane import composer_ready, composer_submitted

SENT = "Reply with exactly the token X and nothing else.\n\nAttached files:\n- /tmp/a"


def test_not_submitted_while_message_in_composer() -> None:
    pane = "\n".join(
        [
            "› Reply with exactly the token X and nothing else.",
            "  Attached files:",
            "  - /tmp/a",
            "  gpt-5.5 xhigh · ~/waypoint",
        ]
    )
    assert composer_submitted(pane, SENT) is False


def test_submitted_when_composer_shows_rotating_placeholder() -> None:
    # After submit the message echoes above (same glyph) and the live composer
    # is the last ``›`` line — a ghost suggestion, never the sent text.
    pane = "\n".join(
        [
            "› Reply with exactly the token X and nothing else.",  # transcript echo
            "● working…",
            "› Summarize recent commits",  # composer placeholder (rotates)
            "  gpt-5.5 xhigh · ~/waypoint",
        ]
    )
    assert composer_submitted(pane, SENT) is True


def test_not_submitted_when_no_prompt_line() -> None:
    # No composer drawn → booting/dead pane, nothing submitted; keep retrying.
    assert composer_submitted("just booting\nno prompt yet", SENT) is False


def test_empty_message_treated_as_submitted() -> None:
    assert composer_submitted("› anything", "") is True


def test_composer_ready_when_prompt_drawn() -> None:
    assert composer_ready("› Summarize recent commits\n  gpt-5.5 · ~/waypoint") is True


def test_composer_not_ready_while_booting() -> None:
    assert composer_ready("starting codex...\nno prompt yet") is False
