"""Custom-model ``context_window``: schema parsing and the unified resolver.

Covers the two pure units of ticket 1405 -- the ``BackendModelOption``
``context_window`` grammar and the configured-then-static Claude resolver --
independent of any transport wiring.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from waypoint.backends.claude_code.models import (
    claude_context_window_for_model,
    configured_context_windows,
    make_context_window_resolver,
    resolve_claude_context_window,
)
from waypoint.backends.claude_code.plugin import ClaudeCodePluginConfig
from waypoint.backends.claude_tty.plugin import ClaudeTtyPlugin, ClaudeTtyPluginConfig
from waypoint.schemas import BackendModelOption


def _model(model_id: str, context_window: object) -> BackendModelOption:
    return BackendModelOption(
        id=model_id, label=model_id, context_window=context_window
    )


# ── Schema grammar ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (256_000, 256_000),
        ("256000", 256_000),
        ("256k", 256_000),
        ("256K", 256_000),
        ("1m", 1_000_000),
        ("1M", 1_000_000),
        ("  1m  ", 1_000_000),
        (200_000, 200_000),
    ],
)
def test_context_window_accepts_and_normalizes(value: object, expected: int) -> None:
    assert _model("kimi-k3[1m]", value).context_window == expected


def test_context_window_absent_defaults_to_none() -> None:
    assert BackendModelOption(id="x", label="X").context_window is None


def test_context_window_explicit_null_is_none() -> None:
    assert _model("x", None).context_window is None


@pytest.mark.parametrize(
    "value",
    [True, 0, -1, "1.5m", "256 k", "2g", "", "  ", "abc", "0", "1.5", 1.5],
)
def test_context_window_rejects_invalid(value: object) -> None:
    with pytest.raises(ValidationError) as exc:
        _model("x", value)
    # The field-specific error names the offending field so an operator can fix
    # the exact line in waypoint.yaml.
    assert "context_window" in str(exc.value)


# ── configured_context_windows ────────────────────────────────────────────


def test_configured_windows_keeps_only_declared_entries() -> None:
    mapping = configured_context_windows(
        [BackendModelOption(id="opus", label="Opus")],  # no context_window
        [_model("kimi-k3[1m]", "1m")],
    )
    assert mapping == {"kimi-k3[1m]": 1_000_000}


def test_configured_windows_extra_overlays_models_on_collision() -> None:
    # extra_models is overlaid last, so it wins a same-id collision with the
    # replacement-style models list (matching merge_model_catalogue precedence).
    mapping = configured_context_windows(
        [_model("opus", "128k")],
        [_model("opus", "256k")],
    )
    assert mapping == {"opus": 256_000}


def test_configured_windows_trims_ids() -> None:
    mapping = configured_context_windows([_model("  spaced  ", "1m")], [])
    assert mapping == {"spaced": 1_000_000}


# ── resolver precedence ───────────────────────────────────────────────────


def test_resolver_exact_custom_id_wins() -> None:
    configured = {"kimi-k3[1m]": 1_000_000}
    assert resolve_claude_context_window("kimi-k3[1m]", configured) == 1_000_000
    # Trims the selected id before the exact lookup.
    assert resolve_claude_context_window("  kimi-k3[1m]  ", configured) == 1_000_000


def test_resolver_configured_overrides_builtin() -> None:
    # An operator may override a built-in id through replacement semantics.
    configured = {"opus": 256_000}
    assert resolve_claude_context_window("opus", configured) == 256_000


def test_resolver_falls_back_to_static_table() -> None:
    assert resolve_claude_context_window("opus[1m]", {}) == 1_000_000
    assert resolve_claude_context_window("claude-opus-4-8", {}) == 200_000


def test_resolver_unknown_without_config_is_none() -> None:
    assert resolve_claude_context_window("gw-mystery", {}) is None
    assert resolve_claude_context_window(None, {"x": 1}) is None


def test_make_resolver_covers_custom_and_static_paths() -> None:
    resolver = make_context_window_resolver(
        [BackendModelOption(id="opus", label="Opus")],
        [_model("kimi-k3[1m]", "1m")],
    )
    assert resolver("kimi-k3[1m]") == 1_000_000  # configured custom
    assert resolver("opus[1m]") == 1_000_000  # static fallback
    assert resolver("gw-mystery") is None  # neither knows it


def test_make_resolver_without_overrides_is_the_static_resolver() -> None:
    # No declared windows -> the static resolver is returned verbatim (no wrapper).
    resolver = make_context_window_resolver(
        [BackendModelOption(id="opus", label="Opus")], []
    )
    assert resolver is claude_context_window_for_model


# ── claude_tty transport resolves from the DRIVEN agent's config ───────────


def test_claude_tty_resolver_reads_driven_agent_config() -> None:
    # Regression: claude_tty is a transport that also drives the claude_code
    # agent, and a custom model's context_window lives under the AGENT's config
    # block. The transport must resolve from the session's backend config, not
    # its own claude_tty block -- otherwise a claude_code session on its default
    # claude_tty transport falls back to the static window and the pill is wrong.
    configs = {
        "claude_code": ClaudeCodePluginConfig(
            extra_models=[
                BackendModelOption(id="kimi-k3[1m]", label="Kimi", context_window="1m")
            ]
        ),
        "claude_tty": ClaudeTtyPluginConfig(),  # no custom models here
    }
    runtime = SimpleNamespace(
        settings=SimpleNamespace(plugin_config=lambda pid: configs[pid])
    )
    plugin = ClaudeTtyPlugin()

    agent_resolver = plugin._context_window_resolver_for(
        cast(Any, runtime), "claude_code"
    )
    assert agent_resolver("kimi-k3[1m]") == 1_000_000

    # Reading its own (empty) claude_tty config would return None -- the bug.
    own_resolver = plugin._context_window_resolver_for(cast(Any, runtime), "claude_tty")
    assert own_resolver("kimi-k3[1m]") is None
