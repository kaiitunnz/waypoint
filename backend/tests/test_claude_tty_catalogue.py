"""claude_tty shares claude_code's version-gated offering and preflight.

claude_tty is the same Claude agent over the tmux/TUI transport, so the model
catalogue it offers and the new-session preflight must track the installed CLI
exactly as claude_code does.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin, ClaudeTtyPluginConfig

# The gate lives in claude_code.plugin.offered_claude_models, so both plugins
# resolve the version through this one symbol.
_DETECT = "waypoint.backends.claude_code.plugin.detect_claude_cli_version"


def _fake_runtime(config: ClaudeTtyPluginConfig | None = None) -> Any:
    resolved = config or ClaudeTtyPluginConfig()
    return SimpleNamespace(
        settings=SimpleNamespace(plugin_config=lambda plugin_id: resolved),
        _find_launch_target=lambda launch_target_id: None,
    )


@pytest.mark.asyncio
async def test_list_models_is_version_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = ClaudeTtyPlugin()
    monkeypatch.setattr(_DETECT, lambda binary, launch_target: (2, 1, 190))

    result = await plugin.list_models(cast(Any, _fake_runtime()))

    sonnet = next(m for m in result["models"] if m["id"] == "sonnet")
    assert sonnet["label"] == "Sonnet 4.6"
    assert "xhigh" not in sonnet["supported_efforts"]
    assert "max" in sonnet["supported_efforts"]


@pytest.mark.asyncio
async def test_list_models_current_catalogue_for_recent_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeTtyPlugin()
    monkeypatch.setattr(_DETECT, lambda binary, launch_target: (2, 1, 197))

    result = await plugin.list_models(cast(Any, _fake_runtime()))

    sonnet = next(m for m in result["models"] if m["id"] == "sonnet")
    assert sonnet["label"] == "Sonnet 5"
    assert "xhigh" in sonnet["supported_efforts"]
    assert result["default_model_id"] == "opus[1m]"


def test_preflight_rejects_xhigh_on_sonnet_under_old_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeTtyPlugin()
    monkeypatch.setattr(_DETECT, lambda binary, launch_target: (2, 1, 190))

    with pytest.raises(ValueError) as exc:
        plugin.validate_new_session_selection(
            cast(Any, _fake_runtime()), "sonnet", "xhigh", None
        )

    assert "sonnet" in str(exc.value) and "xhigh" in str(exc.value)


def test_preflight_accepts_supported_combo(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = ClaudeTtyPlugin()
    monkeypatch.setattr(_DETECT, lambda binary, launch_target: (2, 1, 197))

    plugin.validate_new_session_selection(
        cast(Any, _fake_runtime()), "sonnet", "xhigh", None
    )
