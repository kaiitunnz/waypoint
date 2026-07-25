import pytest

from waypoint.backends.claude_code.models import (
    CLAUDE_MODEL_ALIASES,
    claude_context_window_for_model,
    claude_model_family,
    normalize_claude_model_id,
)


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("claude-sonnet-4-5", "sonnet"),
        ("claude-sonnet-4-6", "sonnet"),
        ("claude-opus-4-7", "opus"),
        ("claude-opus-4-8", "opus"),
    ],
)
def test_legacy_concrete_ids_resolve_to_family(model: str, family: str) -> None:
    assert claude_model_family(model) == family
    assert claude_context_window_for_model(model) == 200_000


@pytest.mark.parametrize(
    ("alias", "window"),
    [
        ("sonnet", 200_000),
        ("sonnet[1m]", 1_000_000),
        ("opus", 200_000),
        ("opus[1m]", 1_000_000),
        ("haiku", 200_000),
    ],
)
def test_bare_aliases(alias: str, window: int) -> None:
    assert claude_context_window_for_model(alias) == window


def test_opus5_concrete_id_resolves_to_opus() -> None:
    # The resolved id the CLI reports for the `opus` selection from 2.1.219 on.
    assert normalize_claude_model_id("claude-opus-5") == "opus"
    assert claude_model_family("claude-opus-5") == "opus"
    assert claude_context_window_for_model("claude-opus-5") == 200_000


@pytest.mark.parametrize(
    ("model", "normalized"),
    [
        ("claude-opus-5[1m]", "opus[1m]"),
        ("claude-opus-4-8[1m]", "opus[1m]"),
        ("claude-sonnet-5[1m]", "sonnet[1m]"),
        ("claude-fable-5[1m]", "fable[1m]"),
    ],
)
def test_concrete_id_keeps_1m_entitlement(model: str, normalized: str) -> None:
    # A concrete id carrying the suffix must not collapse to the bare 200K
    # family id -- the `[1m]` selection is what grants the 1M window.
    assert normalize_claude_model_id(model) == normalized
    assert claude_model_family(model) == normalized.removesuffix("[1m]")
    assert claude_context_window_for_model(model) == 1_000_000


def test_1m_suffix_dropped_for_family_without_a_1m_offering() -> None:
    # Haiku has no `haiku[1m]` catalogue entry, so the suffix resolves away
    # rather than fabricating a 1M window.
    assert normalize_claude_model_id("claude-haiku-4-5[1m]") == "haiku"
    assert claude_context_window_for_model("claude-haiku-4-5[1m]") == 200_000


@pytest.mark.parametrize("model", ["gpt-4o[1m]", "weird[1m]"])
def test_1m_suffix_on_garbage_still_passes_through(model: str) -> None:
    assert normalize_claude_model_id(model) == model
    assert claude_context_window_for_model(model) is None


@pytest.mark.parametrize(
    "model",
    ["claude-sonnet-9", "some-sonnet-thing"],
)
def test_forward_unknown_but_familyish_ids_infer_family(model: str) -> None:
    assert claude_model_family(model) == "sonnet"
    assert claude_context_window_for_model(model) == 200_000


@pytest.mark.parametrize("model", ["gpt-4o", "random"])
def test_genuine_garbage_passes_through_unresolved(model: str) -> None:
    assert normalize_claude_model_id(model) == model
    assert claude_context_window_for_model(model) is None


def test_every_alias_normalizes_to_its_mapped_family() -> None:
    for concrete_id, family in CLAUDE_MODEL_ALIASES.items():
        assert normalize_claude_model_id(concrete_id) == family
