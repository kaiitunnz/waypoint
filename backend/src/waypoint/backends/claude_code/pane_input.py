"""Typing parameters for the Claude Code TUI in a tmux pane.

Claude Code wraps pasted text in ``<pasted_content>`` tags, which its system
prompt and auto-mode classifier treat as content from elsewhere, not the user's
own instructions; messages are therefore typed.

Relies on undocumented input handling observed on Claude Code 2.1.175-2.1.282.
Recheck if messages arrive tagged or with stray ``[I``:

- A key event over 800 UTF-16 units is handled as a paste.
- The key parser splits input at escape sequences, so a separator keeps each
  piece its own key event when tmux writes coalesce.
- Focus-in (``ESC [ I``) is consumed without effect; tmux sends it when a pane
  gains focus.
"""

from waypoint.backends.base import PaneTypingSpec

CLAUDE_PANE_TYPING = PaneTypingSpec(max_event_units=760, event_separator=b"\x1b[I")
