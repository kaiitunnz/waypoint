"""Unit tests for the version-gated claude_code model offering.

Distinct from test_claude_models_resolution.py, which covers the
Phase 1 resolution layer (normalize/family/context-window) that stays full
and version-independent. This file covers claude_models_for_version, which
decides which *offering* (subset/labels) a given CLI build should see.
"""

import logging

import pytest

from waypoint.backends.claude_code.models import (
    CLAUDE_EFFORT_LEVELS,
    DEFAULT_CLAUDE_MODELS,
    OPUS5_MIN_CLI_VERSION,
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


@pytest.mark.parametrize("version", [None, OPUS5_MIN_CLI_VERSION, (2, 2, 0)])
def test_current_offering_for_none_or_recent_version(version) -> None:
    models = claude_models_for_version(version)
    assert models == DEFAULT_CLAUDE_MODELS
    sonnet = _by_id(models, "sonnet")
    assert sonnet.label == "Sonnet 5"
    assert "max" in sonnet.supported_efforts

    # Opus 5 accepts the full effort set, same as Opus 4.8 before it.
    opus = _by_id(models, "opus")
    assert opus.label == "Opus 5"
    assert "xhigh" in opus.supported_efforts and "max" in opus.supported_efforts
    assert _by_id(models, "opus[1m]").label == "Opus 5 (1M context)"


def test_legacy_offering_below_opus5_min_version() -> None:
    models = claude_models_for_version((2, 1, 218))

    # Only the opus labels roll back across the 2.1.219 boundary: Opus 4.8
    # accepts the same full effort set as Opus 5.
    opus = _by_id(models, "opus")
    assert opus.label == "Opus 4.8"
    assert opus.supported_efforts == list(CLAUDE_EFFORT_LEVELS)

    opus_1m = _by_id(models, "opus[1m]")
    assert opus_1m.label == "Opus 4.8 (1M context)"
    assert opus_1m.is_default is True

    # Sonnet 5 already shipped by 2.1.218, so it is not rolled back here.
    sonnet = _by_id(models, "sonnet")
    assert sonnet.label == "Sonnet 5"
    assert "xhigh" in sonnet.supported_efforts


def test_legacy_offering_below_sonnet5_min_version() -> None:
    models = claude_models_for_version((2, 1, 190))

    # Sonnet 4.6 accepts `max` but not `xhigh` (only the sonnet family's
    # efforts differ across the 2.1.197 boundary).
    sonnet = _by_id(models, "sonnet")
    assert sonnet.label == "Sonnet 4.6"
    assert sonnet.supported_efforts == ["low", "medium", "high", "max"]

    sonnet_1m = _by_id(models, "sonnet[1m]")
    assert sonnet_1m.label == "Sonnet 4.6 (1M context)"
    assert sonnet_1m.supported_efforts == ["low", "medium", "high", "max"]

    # A build this old also predates Opus 5, so the opus rollback applies too.
    opus = _by_id(models, "opus")
    assert opus.label == "Opus 4.8"
    assert "xhigh" in opus.supported_efforts and "max" in opus.supported_efforts

    fable = _by_id(models, "fable")
    assert "xhigh" in fable.supported_efforts and "max" in fable.supported_efforts

    haiku = _by_id(models, "haiku")
    assert haiku.supported_efforts == []

    opus_1m = _by_id(models, "opus[1m]")
    assert opus_1m.label == "Opus 4.8 (1M context)"
    assert opus_1m.is_default is True
    assert "xhigh" in opus_1m.supported_efforts and "max" in opus_1m.supported_efforts


@pytest.mark.parametrize(
    ("version", "opus_label", "sonnet_label"),
    [
        ((2, 1, 219), "Opus 5", "Sonnet 5"),
        ((2, 1, 218), "Opus 4.8", "Sonnet 5"),
        ((2, 1, 197), "Opus 4.8", "Sonnet 5"),
        ((2, 1, 196), "Opus 4.8", "Sonnet 4.6"),
    ],
)
def test_rollbacks_apply_cumulatively_at_each_boundary(
    version, opus_label: str, sonnet_label: str
) -> None:
    # Each boundary is inclusive of its own epoch, and a build below several
    # boundaries gets every rollback rather than only the newest.
    models = claude_models_for_version(version)
    assert _by_id(models, "opus").label == opus_label
    assert _by_id(models, "sonnet").label == sonnet_label


@pytest.mark.parametrize("version", [(2, 0, 0), (2, 1, 190), (2, 1, 218), (2, 1, 220)])
def test_every_offering_is_an_ordered_subset_with_one_default(version) -> None:
    # Rollbacks may drop a pinned entry the rolled-back alias makes redundant, so an
    # older offering is a subset rather than an exact match -- but never a superset,
    # never reordered, and always with exactly one default (an alias id, which no
    # rollback removes).
    offering = claude_models_for_version(version)
    default_ids = [opt.id for opt in DEFAULT_CLAUDE_MODELS]
    ids = [opt.id for opt in offering]

    assert set(ids) <= set(default_ids)
    assert ids == [model_id for model_id in default_ids if model_id in set(ids)]
    assert sum(opt.is_default for opt in offering) == 1


@pytest.mark.parametrize("version", [(2, 0, 0), (2, 1, 196), (2, 1, 218), (2, 1, 220)])
def test_no_offering_lists_a_label_twice(version) -> None:
    # Below 2.1.219 the `opus` alias itself is labelled "Opus 4.8", which would collide
    # with the pinned claude-opus-4-8 entry; likewise `sonnet` below 2.1.197.
    labels = [opt.label for opt in claude_models_for_version(version)]
    assert len(labels) == len(set(labels)), sorted(
        label for label in labels if labels.count(label) > 1
    )


def test_rollbacks_drop_the_pin_the_alias_makes_redundant() -> None:
    below_opus5 = {opt.id for opt in claude_models_for_version((2, 1, 218))}
    assert "claude-opus-4-8" not in below_opus5
    assert "claude-opus-4-8[1m]" not in below_opus5
    # Older opus pins are still distinct models, so they stay.
    assert "claude-opus-4-7" in below_opus5
    # Sonnet 5 still shipped at 2.1.218, so its pin is untouched here.
    assert "claude-sonnet-4-6" in below_opus5

    below_sonnet5 = {opt.id for opt in claude_models_for_version((2, 1, 196))}
    assert "claude-sonnet-4-6" not in below_sonnet5
    assert "claude-sonnet-4-6[1m]" not in below_sonnet5
    assert "claude-sonnet-4-5" in below_sonnet5


# --- pinned legacy models -------------------------------------------------


_LEGACY_IDS = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
)


@pytest.mark.parametrize("model_id", _LEGACY_IDS)
def test_reachable_legacy_models_are_offered(model_id: str) -> None:
    assert _by_id(DEFAULT_CLAUDE_MODELS, model_id).id == model_id


@pytest.mark.parametrize(
    "model_id",
    # Remapped to Opus 5 / retired respectively -- offering either would let a user
    # pick a model that silently runs as something else.
    ["claude-opus-4-1", "claude-3-5-haiku", "opus48", "opus47", "sonnet46"],
)
def test_unreachable_models_and_internal_picker_keys_are_not_offered(
    model_id: str,
) -> None:
    assert all(opt.id != model_id for opt in DEFAULT_CLAUDE_MODELS)


@pytest.mark.parametrize("model_id", _LEGACY_IDS)
def test_legacy_models_forward_effort_unvalidated(model_id: str) -> None:
    # None, not a narrower ladder: the CLI accepts any --effort, so pinning a list
    # would only make Waypoint reject launches the CLI honors.
    assert _by_id(DEFAULT_CLAUDE_MODELS, model_id).supported_efforts is None


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
    ],
)
def test_legacy_models_with_a_1m_variant_are_paired(model_id: str) -> None:
    variant = _by_id(DEFAULT_CLAUDE_MODELS, f"{model_id}[1m]")
    assert variant.label.endswith("(1M context)")


def test_opus_4_5_has_no_1m_variant() -> None:
    # On the CLI's own 1M blocklist: requesting the suffix fails with "the long
    # context beta is not yet available".
    assert all(opt.id != "claude-opus-4-5[1m]" for opt in DEFAULT_CLAUDE_MODELS)


def test_catalogue_groups_families_with_each_1m_variant_after_its_base() -> None:
    ids = [opt.id for opt in DEFAULT_CLAUDE_MODELS]
    for index, model_id in enumerate(ids):
        base = model_id.removesuffix("[1m]")
        if base != model_id:
            assert ids[index - 1] == base


def test_legacy_models_are_visible() -> None:
    # The human chose to surface them alongside the current models.
    assert all(not opt.hidden for opt in DEFAULT_CLAUDE_MODELS)


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

    # Version gate preserved (extras did not opt out of it): extras append after the
    # *gated* base, not after the current-epoch catalogue.
    assert version == SONNET5_MIN_CLI_VERSION
    gated = [opt.id for opt in claude_models_for_version(SONNET5_MIN_CLI_VERSION)]
    ids = [opt.id for opt in models]
    assert ids[: len(gated)] == gated
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
