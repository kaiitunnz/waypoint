"""Unit tests for the version-gated claude_code model offering.

Distinct from test_claude_models_resolution.py, which covers the
Phase 1 resolution layer (normalize/family/context-window) that stays full
and version-independent. This file covers claude_models_for_version, which
decides which *offering* (subset/labels) a given CLI build should see.
"""

import logging

import pytest

from waypoint.backends.claude_code.models import (
    DEFAULT_CLAUDE_MODELS,
    SONNET5_MIN_CLI_VERSION,
    claude_models_for_version,
    merge_model_catalogue,
    overridden_builtin_ids,
)
from waypoint.backends.claude_code.plugin import (
    ClaudeCodePluginConfig,
    offered_claude_models,
)
from waypoint.schemas import BackendModelOption


def _by_id(models: tuple, model_id: str):
    return next(opt for opt in models if opt.id == model_id)


@pytest.mark.parametrize("version", [None, SONNET5_MIN_CLI_VERSION, (2, 2, 0)])
def test_current_offering_for_none_or_recent_version(version) -> None:
    models = claude_models_for_version(version)
    assert models == DEFAULT_CLAUDE_MODELS
    sonnet = _by_id(models, "sonnet")
    assert sonnet.label == "Sonnet 5"
    assert "max" in sonnet.supported_efforts


def test_legacy_offering_below_sonnet5_min_version() -> None:
    models = claude_models_for_version((2, 1, 190))

    # Sonnet 4.6 accepts `max` but not `xhigh` (only the sonnet family differs
    # across the 2.1.197 boundary).
    sonnet = _by_id(models, "sonnet")
    assert sonnet.label == "Sonnet 4.6"
    assert sonnet.supported_efforts == ["low", "medium", "high", "max"]

    sonnet_1m = _by_id(models, "sonnet[1m]")
    assert sonnet_1m.label == "Sonnet 4.6 (1M context)"
    assert sonnet_1m.supported_efforts == ["low", "medium", "high", "max"]

    # Opus 4.8 and Fable 5 are unchanged across the boundary: full set incl. max.
    opus = _by_id(models, "opus")
    assert opus.label == "Opus 4.8"
    assert "xhigh" in opus.supported_efforts and "max" in opus.supported_efforts

    fable = _by_id(models, "fable")
    assert "xhigh" in fable.supported_efforts and "max" in fable.supported_efforts

    haiku = _by_id(models, "haiku")
    assert haiku.supported_efforts == []

    opus_1m = _by_id(models, "opus[1m]")
    assert opus_1m.is_default is True
    assert "xhigh" in opus_1m.supported_efforts and "max" in opus_1m.supported_efforts


def test_legacy_offering_has_same_model_ids_as_default() -> None:
    legacy = claude_models_for_version((2, 0, 0))
    assert {opt.id for opt in legacy} == {opt.id for opt in DEFAULT_CLAUDE_MODELS}
    assert sum(opt.is_default for opt in legacy) == 1


# --- merge_model_catalogue ------------------------------------------------


def test_merge_appends_net_new_in_declared_order() -> None:
    base = list(DEFAULT_CLAUDE_MODELS)
    extra = [
        BackendModelOption(id="kimi-k3[1m]", label="Kimi K3 (1M context)"),
        BackendModelOption(id="gw-mini", label="Gateway Mini"),
    ]
    merged = merge_model_catalogue(base, extra)
    assert [opt.id for opt in merged] == (
        [opt.id for opt in base] + ["kimi-k3[1m]", "gw-mini"]
    )


def test_merge_replaces_colliding_id_in_place() -> None:
    base = list(DEFAULT_CLAUDE_MODELS)
    sonnet_index = next(i for i, opt in enumerate(base) if opt.id == "sonnet")
    replacement = BackendModelOption(id="sonnet", label="Sonnet (relabeled)")
    merged = merge_model_catalogue(base, [replacement])

    assert len(merged) == len(base)
    assert [opt.id for opt in merged] == [opt.id for opt in base]
    assert merged[sonnet_index] is replacement
    assert merged[sonnet_index].label == "Sonnet (relabeled)"


def test_merge_empty_extra_is_noop() -> None:
    base = list(DEFAULT_CLAUDE_MODELS)
    merged = merge_model_catalogue(base, [])
    assert merged == base
    assert merged is not base  # a fresh list, not the caller's


# --- overridden_builtin_ids ----------------------------------------------


def test_overridden_builtin_ids_returns_only_colliding() -> None:
    extra = [
        BackendModelOption(id="sonnet", label="X"),
        BackendModelOption(id="kimi-k3[1m]", label="Y"),
        BackendModelOption(id="opus", label="Z"),
    ]
    assert overridden_builtin_ids(extra) == ["sonnet", "opus"]


def test_config_logs_override_line(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="waypoint.backends.claude_code"):
        ClaudeCodePluginConfig(
            extra_models=[BackendModelOption(id="sonnet", label="Relabeled")]
        )
    assert "sonnet" in caplog.text
    assert "overrides a built-in" in caplog.text


# --- offered_claude_models with extra_models ------------------------------


def test_offered_appends_extras_and_keeps_version_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version",
        lambda binary, launch_target: SONNET5_MIN_CLI_VERSION,
    )
    config = ClaudeCodePluginConfig(
        extra_models=[
            BackendModelOption(id="kimi-k3[1m]", label="Kimi K3 (1M context)")
        ]
    )

    models, version = offered_claude_models(config, "claude", None)

    # Version gate preserved (extras did not opt out of it).
    assert version == SONNET5_MIN_CLI_VERSION
    ids = [opt.id for opt in models]
    assert ids[: len(DEFAULT_CLAUDE_MODELS)] == [
        opt.id for opt in DEFAULT_CLAUDE_MODELS
    ]
    assert ids[-1] == "kimi-k3[1m]"


def test_offered_merges_extras_on_explicit_models_with_none_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_detect(binary: str, launch_target: object) -> tuple[int, ...] | None:
        raise AssertionError("explicit models opt out of version detection")

    monkeypatch.setattr(
        "waypoint.backends.claude_code.plugin.detect_claude_cli_version", fail_detect
    )
    config = ClaudeCodePluginConfig(
        models=[BackendModelOption(id="only-model", label="Only")],
        extra_models=[
            BackendModelOption(id="kimi-k3[1m]", label="Kimi K3 (1M context)")
        ],
    )

    models, version = offered_claude_models(config, "claude", None)

    assert version is None
    assert [opt.id for opt in models] == ["only-model", "kimi-k3[1m]"]
