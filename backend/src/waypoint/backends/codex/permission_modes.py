"""Codex permission-mode catalogue.

Each preset maps Waypoint's per-session mode string to the params
Codex's TUI builds for the equivalent /permissions picker entry. See
``tmp/docs/BACKEND_CONTROL_PROTOCOLS.md`` for the source-of-truth wiring.
"""

from functools import cache
from pathlib import Path
from typing import Any

from waypoint.backends.capabilities import PermissionModeSpec

CODEX_PLAN_MODE = "plan"

# Source-of-truth Codex collaboration-mode templates ship in the vendored
# codex submodule. The TUI bundles `plan.md`/`default.md` into its
# `builtin_collaboration_mode_presets` and ships them as the per-mode
# ``developer_instructions``. Sending ``null`` from an app-server client
# instead suppresses Codex's `build_collaboration_mode_update_item`
# emit (see ``codex-rs/core/src/context_manager/updates.rs``), so the
# previous mode's instructions linger in the prompt — i.e. switching
# out of plan mode visibly fails because the model never receives the
# default-mode developer message that supersedes it. We render the
# same templates here so a Waypoint mode switch carries the same
# instruction body the TUI would.
_CODEX_MODE_TEMPLATES_DIR = (
    Path(__file__).resolve().parents[5]
    / "3rdparty"
    / "codex"
    / "codex-rs"
    / "collaboration-mode-templates"
    / "templates"
)
# Mirrors `format_mode_names(&TUI_VISIBLE_COLLABORATION_MODES)` in
# `models-manager/src/collaboration_mode_presets.rs`. The TUI lists Plan
# and Default; Waypoint renders the same set so the model's view of
# "known modes" matches Codex's own prompts.
_KNOWN_MODE_NAMES = "Plan and Default"
_DEFAULT_MODE_REQUEST_USER_INPUT_AVAILABILITY = (
    "The `request_user_input` tool is unavailable in Default mode. "
    "If you call it while in Default mode, it will return an error."
)
_DEFAULT_MODE_ASKING_QUESTIONS_GUIDANCE = (
    "In Default mode, strongly prefer making reasonable assumptions and "
    "executing the user's request rather than stopping to ask questions. "
    "If you absolutely must ask a question because the answer cannot be "
    "discovered from local context and a reasonable assumption would be "
    "risky, ask the user directly with a concise plain-text question. "
    "Never write a multiple choice question as a textual assistant message."
)


@cache
def _load_mode_template(name: str) -> str | None:
    path = _CODEX_MODE_TEMPLATES_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        # Submodule is optional at install time; fall back to ``None``
        # so we still send something rather than crash on every turn.
        return None


@cache
def codex_mode_developer_instructions(mode: str) -> str | None:
    """Return the developer-instructions body Codex's TUI sends for ``mode``.

    Returns ``None`` only when the bundled templates are missing; callers
    should treat ``None`` as "no instruction switch available" and fall
    back to a non-empty placeholder so the App Server still emits a
    collaboration-mode update item.
    """

    if mode == CODEX_PLAN_MODE:
        return _load_mode_template("plan.md")
    template = _load_mode_template("default.md")
    if template is None:
        return None
    return (
        template.replace("{{KNOWN_MODE_NAMES}}", _KNOWN_MODE_NAMES)
        .replace(
            "{{REQUEST_USER_INPUT_AVAILABILITY}}",
            _DEFAULT_MODE_REQUEST_USER_INPUT_AVAILABILITY,
        )
        .replace(
            "{{ASKING_QUESTIONS_GUIDANCE}}",
            _DEFAULT_MODE_ASKING_QUESTIONS_GUIDANCE,
        )
    )


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
        # Always send the rendered mode template. ``None`` here would let
        # Codex's prior collaboration_mode instructions linger because the
        # App Server only emits a switch when the next turn carries
        # non-empty developer instructions.
        params["collaborationMode"] = {
            "mode": collaboration_mode,
            "settings": {
                "model": model,
                "reasoning_effort": "medium" if mode == CODEX_PLAN_MODE else effort,
                "developer_instructions": codex_mode_developer_instructions(
                    collaboration_mode
                ),
            },
        }
    return params
