"""Claude Code permission-mode catalogue.

The CLI accepts free-text strings, but only the names below are surfaced
in Waypoint's UI and validated for scheduled launches. ``acceptEdits`` and
``auto``/``bypassPermissions``/``dontAsk`` short-circuit the PreToolUse
approval card; the rest still surface it.
"""

from waypoint.backends.capabilities import PermissionModeSpec

CLAUDE_PERMISSION_MODES: tuple[str, ...] = (
    "default",
    "plan",
    "acceptEdits",
    "auto",
    "bypassPermissions",
    "dontAsk",
)

CLAUDE_PERMISSION_MODE_SPECS: tuple[PermissionModeSpec, ...] = (
    PermissionModeSpec("default", "Default"),
    PermissionModeSpec("plan", "Plan"),
    PermissionModeSpec("acceptEdits", "Accept edits"),
    PermissionModeSpec("auto", "Auto"),
    PermissionModeSpec("bypassPermissions", "Bypass"),
    PermissionModeSpec("dontAsk", "Don't ask"),
)

# Modes that bypass Waypoint's PreToolUse approval card entirely.
CLAUDE_AUTO_APPROVE_MODES = frozenset({"auto", "bypassPermissions", "dontAsk"})

# Tools acceptEdits auto-approves; everything else still surfaces the card.
CLAUDE_ACCEPT_EDITS_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
