"""Unit tests for ClaudeCodePlugin.validate_new_session_selection.

The new-session preflight only rejects combinations it can *prove* the
installed CLI won't honor (a recognized model paired with an effort that
model's catalogue entry doesn't list). Free-text/unrecognized models always
pass through, since claude_code advertises supports_free_text=True and users
may type an arbitrary model string.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from waypoint.backends.claude_code.models import DEFAULT_CLAUDE_MODELS
from waypoint.backends.claude_code.plugin import (
    ClaudeCodePlugin,
    ClaudeCodePluginConfig,
)


def _fake_runtime(config: ClaudeCodePluginConfig | None = None) -> Any:
    resolved_config = config or ClaudeCodePluginConfig()
    return SimpleNamespace(
        settings=SimpleNamespace(plugin_config=lambda plugin_id: resolved_config),
        _find_launch_target=lambda launch_target_id: None,
    )


def test_rejects_xhigh_effort_on_sonnet_under_old_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pre-2.1.197 the `sonnet` alias is Sonnet 4.6, which accepts `max` but
    # not `xhigh`.
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: (2, 1, 190),
    )

    with pytest.raises(ValueError) as exc:
        plugin.validate_new_session_selection(
            cast(Any, _fake_runtime()), "sonnet", "xhigh", None
        )

    message = str(exc.value)
    assert "sonnet" in message
    assert "xhigh" in message
    assert "2.1.190" in message
    assert "max" in message  # the supported-efforts list is named


def test_accepts_max_effort_on_sonnet_under_old_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sonnet 4.6 does accept `max` -- only `xhigh` is unsupported pre-2.1.197.
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: (2, 1, 190),
    )

    plugin.validate_new_session_selection(
        cast(Any, _fake_runtime()), "sonnet", "max", None
    )


def test_rejects_any_effort_on_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: (2, 1, 197),
    )

    with pytest.raises(ValueError) as exc:
        plugin.validate_new_session_selection(
            cast(Any, _fake_runtime()), "haiku", "low", None
        )

    assert "haiku" in str(exc.value)


@pytest.mark.parametrize("version", [(2, 1, 197), (2, 2, 0)])
def test_accepts_max_effort_on_sonnet_under_recent_cli(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, ...]
) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: version,
    )

    plugin.validate_new_session_selection(
        cast(Any, _fake_runtime()), "sonnet", "max", None
    )


def test_accepts_max_effort_on_sonnet_when_version_undetectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: None,
    )

    plugin.validate_new_session_selection(
        cast(Any, _fake_runtime()), "sonnet", "max", None
    )


def test_passes_through_unknown_free_text_model_with_any_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()
    called = False

    def fake_detect(binary: str, launch_target: Any) -> tuple[int, ...] | None:
        nonlocal called
        called = True
        return (2, 1, 100)

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version", fake_detect
    )

    plugin.validate_new_session_selection(
        cast(Any, _fake_runtime()), "claude-sonnet-9000", "max", None
    )
    # Free text is looked up in the catalogue (miss) but never rejected --
    # the detector still runs since we can't skip resolving the catalogue.
    assert called is True


def test_effort_none_never_raises_and_skips_version_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ClaudeCodePlugin()

    def fail_detect(binary: str, launch_target: Any) -> tuple[int, ...] | None:
        raise AssertionError("should not detect CLI version when effort is None")

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version", fail_detect
    )

    plugin.validate_new_session_selection(
        cast(Any, _fake_runtime()), "haiku", None, None
    )


def test_honors_explicit_models_override(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = ClaudeCodePlugin()
    # A deployment-overridden catalogue where sonnet is deliberately capped
    # at "low" -- the preflight should judge against this list, not the
    # built-in default, and never touch version detection.
    custom_sonnet = next(
        opt for opt in DEFAULT_CLAUDE_MODELS if opt.id == "sonnet"
    ).model_copy(update={"supported_efforts": ["low"]})
    config = ClaudeCodePluginConfig(models=[custom_sonnet])

    def fail_detect(binary: str, launch_target: Any) -> tuple[int, ...] | None:
        raise AssertionError("should not detect CLI version for an explicit override")

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version", fail_detect
    )

    with pytest.raises(ValueError) as exc:
        plugin.validate_new_session_selection(
            cast(Any, _fake_runtime(config=config)), "sonnet", "high", None
        )
    assert "unknown" in str(exc.value)

    plugin.validate_new_session_selection(
        cast(Any, _fake_runtime(config=config)), "sonnet", "low", None
    )
