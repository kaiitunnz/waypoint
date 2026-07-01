"""Unit tests for the version-gated claude_code model offering.

Distinct from test_claude_models_resolution.py, which covers the
Phase 1 resolution layer (normalize/family/context-window) that stays full
and version-independent. This file covers claude_models_for_version, which
decides which *offering* (subset/labels) a given CLI build should see.
"""

import pytest

from waypoint.backends.claude_code.models import (
    DEFAULT_CLAUDE_MODELS,
    SONNET5_MIN_CLI_VERSION,
    claude_models_for_version,
)


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
