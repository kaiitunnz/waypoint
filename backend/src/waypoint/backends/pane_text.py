"""Shared text helpers for parsing captured tmux pane snapshots.

A pane captured with ``tmux capture-pane -e`` carries CSI / OSC / two-byte
escape sequences interleaved with the text, and wraps or pads composer lines —
both of which shred the substring and regex anchors the per-backend pane probes
rely on. Strip them before any matching.
"""

import re

_ANSI_RE = re.compile(
    r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))"
)
_WHITESPACE_RE = re.compile(r"\s+")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def strip_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)
