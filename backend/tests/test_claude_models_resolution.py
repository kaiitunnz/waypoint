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
        ("opus[1m]", 1_000_000),
        ("haiku", 200_000),
    ],
)
def test_bare_aliases(alias: str, window: int) -> None:
    assert claude_context_window_for_model(alias) == window


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
