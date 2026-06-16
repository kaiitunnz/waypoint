"""Detect whether the Codex TUI composer still holds unsent input.

Used by the tmux transport's submit-confirm loop. Codex renders input — and
attachment paths — as literal text, and an empty composer shows a *rotating*
ghost suggestion rather than a fixed placeholder, so submission can't be keyed
on matching a placeholder. Instead it is keyed on the sent text no longer
occupying the composer prompt line. The composer prompt (``›``) is the
bottom-most prompt line; a submitted message echoes above it with the same
glyph, so only the last ``›`` line is the live composer.
"""

import re

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)
_WHITESPACE_RE = re.compile(r"\s+")
_PROMPT = "›"
# Enough of the message to be distinctive without tripping over the TUI
# wrapping/truncating a long composer line.
_PROBE_LEN = 24


def _compact(text: str) -> str:
    return _WHITESPACE_RE.sub("", _ANSI_RE.sub("", text))


def composer_submitted(pane_text: str, sent_text: str) -> bool:
    """Whether ``sent_text`` has left the Codex composer (i.e. was submitted).

    Returns ``True`` when the composer prompt line no longer carries the start
    of the message. If the message is empty or the composer can't be located,
    returns ``True`` so the caller doesn't retry blindly (its loop is bounded).
    """
    probe = _compact(sent_text)[:_PROBE_LEN]
    if not probe:
        return True
    prompt_lines = [line for line in pane_text.splitlines() if _PROMPT in line]
    if not prompt_lines:
        return True
    return probe not in _compact(prompt_lines[-1])
