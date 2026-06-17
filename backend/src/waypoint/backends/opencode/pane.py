"""Detect whether the OpenCode TUI composer still holds unsent input.

Used by the tmux transport's submit-confirm loop. The composer is a bordered
box at the bottom of the pane: ``┃`` content lines closed by a ``╹▀▀`` bottom
border. An empty composer shows the ``Ask anything…`` placeholder, a populated
one the typed text (OpenCode renders input, and attachment paths, literally).
Submitted messages echo in ``┃`` boxes higher up, but only the composer box is
closed by a ``╹`` bottom border; the transcript boxes are not. So the composer
is the run of ``┃`` lines directly above the *last* ``╹`` border, and submission
is confirmed by the sent text no longer occupying it.
"""

from waypoint.backends.pane_text import strip_ansi, strip_whitespace

_BORDER = "╹"
_CONTENT = "┃"
_PROBE_LEN = 24


def composer_ready(pane_text: str) -> bool:
    """Whether the OpenCode composer box is drawn and able to take input.

    True once the ``╹`` box border exists. A freshly relaunched pane (reattach
    or restart) shows the boot screen with no box for a beat; the tmux transport
    waits on this before pasting so the keystrokes are not dropped.
    """
    return any(_BORDER in line for line in strip_ansi(pane_text).splitlines())


def composer_submitted(pane_text: str, sent_text: str) -> bool:
    """Whether ``sent_text`` has left the OpenCode composer (i.e. was submitted).

    Returns ``True`` when the composer box no longer carries the start of the
    message. An empty message counts as submitted. If the composer box can't be
    located the pane is booting or dead, so nothing has been submitted — return
    ``False`` so the bounded confirm loop keeps retrying.
    """
    probe = strip_whitespace(strip_ansi(sent_text))[:_PROBE_LEN]
    if not probe:
        return True
    lines = strip_ansi(pane_text).splitlines()
    borders = [i for i, line in enumerate(lines) if _BORDER in line]
    if not borders:
        return False
    # Walk up from the border over the composer box's contiguous ``┃`` content
    # lines, stopping at the blank gap above it. This covers the full typed
    # message — whose start renders at the top of the box, however tall it grows
    # with wrapping or attachment paths — without bleeding into the transcript,
    # whose echoed user-message boxes carry no ``╹`` border to scan past.
    bottom = borders[-1]
    top = bottom
    while top > 0 and _CONTENT in lines[top - 1]:
        top -= 1
    region = strip_whitespace(" ".join(lines[top:bottom]))
    return probe not in region
