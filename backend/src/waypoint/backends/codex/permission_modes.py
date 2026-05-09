"""Codex permission-mode catalogue.

Each preset maps Waypoint's per-session mode string to the params
Codex's TUI builds for the equivalent /permissions picker entry. See
``tmp/docs/BACKEND_CONTROL_PROTOCOLS.md`` for the source-of-truth wiring.
"""

from typing import Any

from waypoint.backends.capabilities import PermissionModeSpec

CODEX_PLAN_MODE = "plan"

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
    PermissionModeSpec(id="default", label="Default"),
    PermissionModeSpec(
        id=CODEX_PLAN_MODE,
        label="Plan",
        description="Use Codex Plan collaboration mode with the previous permission preset",
    ),
    PermissionModeSpec(id="auto_review", label="Auto review"),
    PermissionModeSpec(id="full_access", label="Full access"),
)

CODEX_PERMISSION_MODE_IDS: tuple[str, ...] = tuple(CODEX_PERMISSION_PRESETS) + (
    CODEX_PLAN_MODE,
)


def codex_turn_params_for(
    mode: str | None,
    *,
    model: str | None = None,
    effort: str | None = None,
    pre_plan_mode: str | None = None,
) -> dict[str, Any] | None:
    if mode is None:
        return None
    preset_mode = (
        pre_plan_mode
        if mode == CODEX_PLAN_MODE and pre_plan_mode in CODEX_PERMISSION_PRESETS
        else mode
    )
    if mode == CODEX_PLAN_MODE and preset_mode == CODEX_PLAN_MODE:
        preset_mode = "default"
    preset = CODEX_PERMISSION_PRESETS.get(preset_mode)
    if preset is None:
        return None
    params = dict(preset)
    if model:
        collaboration_mode = CODEX_PLAN_MODE if mode == CODEX_PLAN_MODE else "default"
        params["collaborationMode"] = {
            "mode": collaboration_mode,
            "settings": {
                "model": model,
                "reasoning_effort": "medium" if mode == CODEX_PLAN_MODE else effort,
                # Let app-server inject Codex's built-in mode instructions.
                "developer_instructions": None,
            },
        }
    return params
