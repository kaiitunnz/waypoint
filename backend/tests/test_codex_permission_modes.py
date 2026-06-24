"""Regression tests for Codex turn-param wire format.

Codex's app-server deserializes ``TurnStartParams`` with
``rename_all = "camelCase"`` and silently drops unknown fields, so
emitting snake_case keys here would route ``auto_review`` approvals to
the user instead of the guardian subagent — observable only when the
local sandbox forces an escalation.
"""

from waypoint.backends.codex.permission_modes import (
    CODEX_PERMISSION_PRESETS,
    codex_turn_params_for,
)

# Top-level keys the v2 TurnStartParams wire format accepts. Mirrors the JSON
# schema at ``codex-rs/app-server-protocol/schema/json/v2/TurnStartParams.json``
# in the upstream openai/codex repo.
_V2_TURN_START_KEYS = frozenset(
    {
        "approvalPolicy",
        "approvalsReviewer",
        "collaborationMode",
        "cwd",
        "effort",
        "input",
        "model",
        "outputSchema",
        "permissionProfile",
        "personality",
        "sandboxPolicy",
        "serviceTier",
        "summary",
        "threadId",
    }
)


def test_presets_use_v2_camel_case_wire_keys() -> None:
    for mode, preset in CODEX_PERMISSION_PRESETS.items():
        assert preset.keys() <= _V2_TURN_START_KEYS, (
            f"preset {mode!r} has non-wire keys: "
            f"{sorted(set(preset) - _V2_TURN_START_KEYS)}"
        )


def test_turn_params_emit_v2_camel_case_wire_keys() -> None:
    for mode in CODEX_PERMISSION_PRESETS:
        params = codex_turn_params_for(mode, model="gpt-5", effort="high")
        assert params is not None
        assert params.keys() <= _V2_TURN_START_KEYS, (
            f"mode {mode!r} emitted non-wire keys: "
            f"{sorted(set(params) - _V2_TURN_START_KEYS)}"
        )


def test_auto_review_routes_to_guardian_subagent() -> None:
    params = codex_turn_params_for("auto_review", model="gpt-5", effort="high")
    assert params is not None
    assert params["approvalsReviewer"] == "guardian_subagent"
    assert params["approvalPolicy"] == "on-request"
    assert params["sandboxPolicy"] == {"type": "workspaceWrite"}


def test_full_access_disables_approvals_and_sandbox() -> None:
    params = codex_turn_params_for("full_access", model="gpt-5", effort="high")
    assert params is not None
    assert params["approvalPolicy"] == "never"
    assert params["sandboxPolicy"] == {"type": "dangerFullAccess"}
