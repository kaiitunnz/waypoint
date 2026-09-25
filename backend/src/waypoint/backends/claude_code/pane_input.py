"""How messages are typed into the Claude Code TUI running in a tmux pane.

Claude Code wraps pasted text in ``<pasted_content>`` tags, and its system
prompt (and the auto-mode classifier's) treats tagged text as content the user
pasted from elsewhere, whose instructions are not the user's own. A message
delivered as a paste therefore reaches the model as untrusted text, so the tmux
transport types it instead.

This relies on undocumented Claude Code input handling, observed on 2.1.175 -
2.1.282. If messages start arriving tagged (or with stray ``[I`` text), recheck:

- A single key event longer than 800 UTF-16 units is handled as a paste, so
  each typed piece stays under that.
- The key parser splits input at escape sequences, so a separator between
  pieces keeps each its own key event even when tmux writes coalesce.
- The separator is focus-in (``ESC [ I``), which the TUI consumes without
  effect; tmux sends the same sequence when a pane gains focus.
"""

from waypoint.backends.base import PaneTypingSpec

CLAUDE_PANE_TYPING = PaneTypingSpec(max_event_units=760, event_separator=b"\x1b[I")
