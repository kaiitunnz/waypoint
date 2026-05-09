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
    PermissionModeSpec(id="default", label="Default"),
    PermissionModeSpec(id="plan", label="Plan"),
    PermissionModeSpec(id="acceptEdits", label="Accept edits"),
    PermissionModeSpec(id="auto", label="Auto"),
    PermissionModeSpec(id="bypassPermissions", label="Bypass"),
    PermissionModeSpec(id="dontAsk", label="Don't ask"),
)

# Modes that bypass Waypoint's PreToolUse approval card entirely.
CLAUDE_AUTO_APPROVE_MODES = frozenset({"auto", "bypassPermissions", "dontAsk"})

# Tools acceptEdits auto-approves; everything else still surfaces the card.
CLAUDE_ACCEPT_EDITS_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})

CLAUDE_PERMISSION_MODE_LABELS = {
    spec.id: spec.label for spec in CLAUDE_PERMISSION_MODE_SPECS
}


def claude_permission_mode_label(mode: str) -> str:
    return CLAUDE_PERMISSION_MODE_LABELS.get(mode, mode)
