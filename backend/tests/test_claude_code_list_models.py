"""Unit tests for ClaudeCodePlugin.list_models's version-gated offering."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.backends.claude_code.models import (
    CLAUDE_EFFORT_LEVELS,
    DEFAULT_CLAUDE_MODELS,
)
from waypoint.backends.claude_code.plugin import (
    ClaudeCodePlugin,
    ClaudeCodePluginConfig,
)
from waypoint.launch_targets import SshLaunchTargetConfig
from waypoint.schemas import BackendModelOption


def _fake_runtime(
    config: ClaudeCodePluginConfig | None = None,
    launch_target: SshLaunchTargetConfig | None = None,
) -> Any:
    resolved_config = config or ClaudeCodePluginConfig()
    return SimpleNamespace(
        settings=SimpleNamespace(plugin_config=lambda plugin_id: resolved_config),
        _find_launch_target=lambda launch_target_id: launch_target,
    )


@pytest.mark.asyncio
async def test_list_models_uses_current_catalogue_for_recent_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: (2, 1, 197),
    )

    result = await plugin.list_models(cast(Any, _fake_runtime()))

    sonnet = next(m for m in result["models"] if m["id"] == "sonnet")
    assert sonnet["label"] == "Sonnet 5"
    assert "max" in sonnet["supported_efforts"]
    assert result["default_model_id"] == "opus[1m]"


@pytest.mark.asyncio
async def test_list_models_uses_legacy_catalogue_for_older_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: (2, 1, 100),
    )

    result = await plugin.list_models(cast(Any, _fake_runtime()))

    sonnet = next(m for m in result["models"] if m["id"] == "sonnet")
    assert sonnet["label"] == "Sonnet 4.6"
    # Sonnet 4.6 accepts `max` but not `xhigh`.
    assert "xhigh" not in sonnet["supported_efforts"]
    assert "max" in sonnet["supported_efforts"]
    # opus[1m] stays the default across both epochs.
    assert result["default_model_id"] == "opus[1m]"
    opus_1m = next(m for m in result["models"] if m["id"] == "opus[1m]")
    assert opus_1m["is_default"] is True


@pytest.mark.asyncio
async def test_list_models_treats_undetectable_version_as_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: None,
    )

    result = await plugin.list_models(cast(Any, _fake_runtime()))

    sonnet = next(m for m in result["models"] if m["id"] == "sonnet")
    assert sonnet["label"] == "Sonnet 5"


@pytest.mark.asyncio
async def test_list_models_passes_resolved_launch_target_to_detector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    target = SshLaunchTargetConfig(id="t", name="t", ssh_destination="remote")
    seen: dict[str, Any] = {}

    def fake_detect(binary: str, launch_target: Any) -> tuple[int, ...] | None:
        seen["binary"] = binary
        seen["launch_target"] = launch_target
        return None

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version", fake_detect
    )
    runtime = _fake_runtime(launch_target=target)

    await plugin.list_models(cast(Any, runtime), launch_target_id="remote-1")

    assert seen["launch_target"] is target


@pytest.mark.asyncio
async def test_list_models_honors_explicit_models_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    custom = [DEFAULT_CLAUDE_MODELS[0]]
    config = ClaudeCodePluginConfig(models=custom)
    called = False

    def fake_detect(binary: str, launch_target: Any) -> tuple[int, ...] | None:
        nonlocal called
        called = True
        return (2, 0, 0)

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version", fake_detect
    )

    result = await plugin.list_models(cast(Any, _fake_runtime(config=config)))

    assert not called
    assert [m["id"] for m in result["models"]] == [custom[0].id]


@pytest.mark.asyncio
async def test_list_models_includes_effort_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: (2, 1, 197),
    )

    result = await plugin.list_models(cast(Any, _fake_runtime()))

    assert result["effort_levels"] == list(CLAUDE_EFFORT_LEVELS)


@pytest.mark.asyncio
async def test_list_models_surfaces_extra_model_with_null_effort_as_json_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: (2, 1, 197),
    )
    config = ClaudeCodePluginConfig(
        extra_models=[
            BackendModelOption(id="kimi-k3[1m]", label="Kimi K3 (1M context)")
        ]
    )

    result = await plugin.list_models(cast(Any, _fake_runtime(config=config)))

    kimi = next(m for m in result["models"] if m["id"] == "kimi-k3[1m]")
    # Unknown efforts must serialize as JSON null (distinct from []) so the
    # frontend can tell "unknown" from "no knob".
    assert kimi["supported_efforts"] is None
    # Haiku's pinned [] stays an empty list, not null.
    haiku = next(m for m in result["models"] if m["id"] == "haiku")
    assert haiku["supported_efforts"] == []
