"""Codex permission-mode catalogue.

Each preset maps Waypoint's per-session mode string to the params
Codex's TUI builds for the equivalent /permissions picker entry. See
``tmp/docs/BACKEND_CONTROL_PROTOCOLS.md`` for the source-of-truth wiring.
"""

from typing import Any

from waypoint.backends.capabilities import PermissionModeSpec

CODEX_PERMISSION_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        "approval_policy": "on-request",
        "sandbox_policy": {"type": "workspaceWrite"},
        "approvals_reviewer": "user",
    },
    "auto_review": {
        "approval_policy": "on-request",
        "sandbox_policy": {"type": "workspaceWrite"},
        "approvals_reviewer": "guardian_subagent",
    },
    "full_access": {
        "approval_policy": "never",
        "sandbox_policy": {"type": "dangerFullAccess"},
        "approvals_reviewer": "user",
    },
}

CODEX_PERMISSION_MODE_SPECS: tuple[PermissionModeSpec, ...] = (
    PermissionModeSpec("default", "Default"),
    PermissionModeSpec("auto_review", "Auto review"),
    PermissionModeSpec("full_access", "Full access"),
)


def codex_turn_params_for(mode: str | None) -> dict[str, Any] | None:
    if mode is None:
        return None
    preset = CODEX_PERMISSION_PRESETS.get(mode)
    if preset is None:
        return None
    return dict(preset)
