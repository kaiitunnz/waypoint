from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException

from waypoint.backends.opencode.plugin import (
    DEFAULT_OPENCODE_MODEL,
    OpenCodePlugin,
    _ruleset_for_mode,
)
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import SessionCreateRequest


def test_serialize_question_answers_preserves_choices_and_notes() -> None:
    plugin = OpenCodePlugin()

    result = plugin._serialize_question_answers(
        "fallback",
        [
            {
                "question": "Deploy target",
                "answer": "staging, prod",
                "notes": "after business hours",
            },
            {
                "question": "Rollback plan",
                "notes": "keep the old pods warm",
            },
        ],
    )

    assert result == [
        ["staging", "prod", "after business hours"],
        ["keep the old pods warm"],
    ]


def test_serialize_question_answers_falls_back_to_raw_answer() -> None:
    plugin = OpenCodePlugin()

    assert plugin._serialize_question_answers("just text", None) == [["just text"]]
    # Empty structured answers also fall back so the question still gets a reply.
    assert plugin._serialize_question_answers("just text", []) == [["just text"]]


@pytest.mark.parametrize(
    "mode,expected",
    [
        (None, None),
        ("", None),
        ("default", None),
        ("ask", [{"permission": "*", "pattern": "*", "action": "ask"}]),
        ("allow", [{"permission": "*", "pattern": "*", "action": "allow"}]),
        ("deny", [{"permission": "*", "pattern": "*", "action": "deny"}]),
    ],
)
def test_ruleset_for_mode(
    mode: str | None, expected: list[dict[str, str]] | None
) -> None:
    assert _ruleset_for_mode(mode) == expected


def test_validate_permission_mode_accepts_known_actions() -> None:
    plugin = OpenCodePlugin()

    assert plugin.validate_permission_mode(None) is None
    assert plugin.validate_permission_mode("") is None
    # "default" is a real mode (clears the ruleset) — pass it through so
    # set_permission_mode can round-trip it (the runtime rejects None).
    assert plugin.validate_permission_mode("default") == "default"
    assert plugin.validate_permission_mode("ask") == "ask"
    assert plugin.validate_permission_mode("allow") == "allow"
    assert plugin.validate_permission_mode("deny") == "deny"


def test_validate_permission_mode_rejects_legacy_auto() -> None:
    plugin = OpenCodePlugin()

    with pytest.raises(
        HTTPException, match="unsupported opencode permission mode: auto"
    ):
        plugin.validate_permission_mode("auto")


@pytest.mark.asyncio
async def test_list_models_rejects_remote_launch_targets() -> None:
    plugin = OpenCodePlugin()

    with pytest.raises(HTTPException, match="SSH launch targets are not supported yet"):
        await plugin.list_models(
            runtime=cast(Any, object()),
            launch_target_id="ssh-1",
        )


@pytest.mark.asyncio
async def test_create_session_rejects_remote_launch_targets() -> None:
    plugin = OpenCodePlugin()
    request = SessionCreateRequest(
        backend="opencode",
        cwd="/tmp/project",
        launch_target_id="ssh-1",
    )

    with pytest.raises(HTTPException, match="SSH launch targets are not supported yet"):
        await plugin.create_session(
            runtime=cast(Any, object()),
            request=request,
            session_id="opencode-test",
            launch_target=cast(SshLaunchTargetConfig, SimpleNamespace(id="ssh-1")),
            title="Test",
            raw_log=Path("/tmp/raw.log"),
            structured_log=Path("/tmp/events.jsonl"),
            git_meta=cast(Any, SimpleNamespace(repo_name=None, branch=None)),
            permission_mode=None,
            resolved_model=None,
            resolved_effort=None,
        )


def test_flatten_provider_models_skips_invalid_and_deprecated() -> None:
    plugin = OpenCodePlugin()
    payload = {
        "all": [
            {
                "id": "opencode",
                "name": "OpenCode",
                "models": {
                    "minimax-m2.5-free": {
                        "name": "MiniMax M2.5 Free",
                        "status": "active",
                    },
                    "old-model": {"name": "Old", "status": "deprecated"},
                },
            },
            {
                "id": "anthropic",
                "name": "Anthropic",
                "models": {
                    "claude-sonnet-4-6": {"status": "active"},
                },
            },
            "not-a-dict",
            {"id": ""},
        ],
    }

    flattened = plugin._flatten_provider_models(payload, include_hidden=False)

    assert flattened == [
        {"id": "anthropic/claude-sonnet-4-6", "label": "Anthropic · claude-sonnet-4-6"},
        {"id": "opencode/minimax-m2.5-free", "label": "OpenCode · MiniMax M2.5 Free"},
    ]


def test_flatten_provider_models_includes_deprecated_when_requested() -> None:
    plugin = OpenCodePlugin()
    payload = {
        "all": [
            {
                "id": "opencode",
                "name": "OpenCode",
                "models": {
                    "old-model": {"name": "Old", "status": "deprecated"},
                },
            },
        ],
    }

    flattened = plugin._flatten_provider_models(payload, include_hidden=True)

    assert flattened == [{"id": "opencode/old-model", "label": "OpenCode · Old"}]


def test_select_default_model_prefers_user_default_when_available() -> None:
    plugin = OpenCodePlugin()
    models = [
        {"id": DEFAULT_OPENCODE_MODEL, "label": "MiniMax"},
        {"id": "anthropic/claude-sonnet-4-6", "label": "Sonnet"},
    ]

    assert plugin._select_default_model(models, providers={}) == (
        DEFAULT_OPENCODE_MODEL,
        "MiniMax",
    )


def test_select_default_model_falls_back_to_provider_defaults() -> None:
    plugin = OpenCodePlugin()
    models = [{"id": "anthropic/claude-sonnet-4-6", "label": "Sonnet"}]
    providers = {"default": {"anthropic": "claude-sonnet-4-6", "missing": "x"}}

    assert plugin._select_default_model(models, providers) == (
        "anthropic/claude-sonnet-4-6",
        "Sonnet",
    )


def test_select_default_model_falls_back_to_first_available() -> None:
    plugin = OpenCodePlugin()
    models = [{"id": "anthropic/claude-sonnet-4-6", "label": "Sonnet"}]

    assert plugin._select_default_model(models, providers={}) == (
        "anthropic/claude-sonnet-4-6",
        "Sonnet",
    )
