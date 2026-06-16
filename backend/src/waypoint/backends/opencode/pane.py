"""Detect whether the OpenCode TUI composer still holds unsent input.

Used by the tmux transport's submit-confirm loop. The composer is a bordered
box at the bottom of the pane: ``┃`` content lines closed by a ``╹▀▀`` bottom
border. An empty composer shows the ``Ask anything…`` placeholder, a populated
one the typed text (OpenCode renders input, and attachment paths, literally).
Submitted messages echo in ``┃`` boxes higher up, so the live composer is the
``┃`` region directly above the *last* box border; submission is confirmed by
the sent text no longer occupying it.
"""

import re

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)
_WHITESPACE_RE = re.compile(r"\s+")
_BORDER = "╹"
# Content rows of the composer box sit just above its bottom border.
_COMPOSER_SPAN = 5
_PROBE_LEN = 24


def _strip(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _compact(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def composer_submitted(pane_text: str, sent_text: str) -> bool:
    """Whether ``sent_text`` has left the OpenCode composer (i.e. was submitted).

    Returns ``True`` when the composer box no longer carries the start of the
    message. If the message is empty or the composer box can't be located,
    returns ``True`` so the caller doesn't retry blindly (its loop is bounded).
    """
    probe = _compact(sent_text)[:_PROBE_LEN]
    if not probe:
        return True
    lines = _strip(pane_text).splitlines()
    borders = [i for i, line in enumerate(lines) if _BORDER in line]
    if not borders:
        return True
    bottom = borders[-1]
    region = _compact(" ".join(lines[max(0, bottom - _COMPOSER_SPAN) : bottom]))
    return probe not in region
