import pytest

from waypoint.backends.claude_code.models import (
    is_claude_plan_switching_model,
    map_observed_model_id,
    observe_claude_model,
)


@pytest.mark.parametrize(
    ("concrete", "expected"),
    [
        # Current-epoch alias targets have no pinned entry -> map to the alias.
        ("claude-opus-5-5", "opus"),
        ("claude-sonnet-5", "sonnet"),
        ("claude-fable-5-1", "fable"),
        # Pinned legacy ids keep their distinct identity (checked before alias).
        ("claude-opus-5", "claude-opus-5"),
        ("claude-opus-4-8", "claude-opus-4-8"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),
        # The [1m] suffix is stripped from the stored base either way.
        ("claude-opus-5-5[1m]", "opus"),
        ("claude-opus-5[1m]", "claude-opus-5"),
        ("claude-opus-4-8[1m]", "claude-opus-4-8"),
        # Unknown ids round-trip unchanged (picker shows a Custom entry).
        ("claude-opus-9", "claude-opus-9"),
        ("", None),
        (None, None),
    ],
)
def test_map_observed_model_id(concrete: str | None, expected: str | None) -> None:
    assert map_observed_model_id(concrete) == expected


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("opusplan", True),
        ("opusplan[1m]", True),
        ("OpusPlan", True),
        ("opus", False),
        ("claude-opus-5", False),
        (None, False),
    ],
)
def test_is_plan_switching(model: str | None, expected: bool) -> None:
    assert is_claude_plan_switching_model(model) is expected


def test_alias_resolution_first_reply_no_toast() -> None:
    # select opus[1m], first reply runs claude-opus-5-5 -> same model, just resolved.
    obs = observe_claude_model("claude-opus-5-5", "opus[1m]", prev_base=None)
    assert obs is not None
    assert obs.resolved_base == "opus"
    assert obs.adopt_selection is None
    assert obs.reason is None


def test_first_reply_intra_family_fallback_adopts_and_toasts() -> None:
    # select opus (Opus 5.5), first reply runs Opus 4.8 -> a real fallback.
    obs = observe_claude_model("claude-opus-4-8", "opus", prev_base=None)
    assert obs is not None
    assert obs.resolved_base == "claude-opus-4-8"
    assert obs.adopt_selection == "claude-opus-4-8"
    assert obs.reason == "initial_mismatch"


def test_first_reply_previous_opus_keeps_pinned_identity() -> None:
    # select opus[1m], first reply runs Opus 5 (a CLI older than 2.1.279).
    obs = observe_claude_model("claude-opus-5", "opus[1m]", prev_base=None)
    assert obs is not None
    assert obs.resolved_base == "claude-opus-5"
    assert obs.adopt_selection == "claude-opus-5[1m]"
    assert obs.reason == "initial_mismatch"


def test_first_reply_family_mismatch() -> None:
    obs = observe_claude_model("claude-sonnet-5", "opus[1m]", prev_base=None)
    assert obs is not None
    assert obs.resolved_base == "sonnet"
    assert obs.adopt_selection == "sonnet[1m]"  # [1m] preserved from selection
    assert obs.reason == "initial_mismatch"


def test_mid_session_switch_toasts_and_marks() -> None:
    obs = observe_claude_model("claude-sonnet-5", "opus", prev_base="opus")
    assert obs is not None
    assert obs.resolved_base == "sonnet"
    assert obs.adopt_selection == "sonnet"
    assert obs.reason == "switch"


def test_stable_model_no_notice() -> None:
    obs = observe_claude_model("claude-opus-5-5", "opus", prev_base="opus")
    assert obs is not None
    assert obs.resolved_base == "opus"
    assert obs.adopt_selection is None
    assert obs.reason is None


def test_no_selection_adopts_without_toast() -> None:
    # No explicit selection (agent default) -> fill in the actual model, no alarm.
    obs = observe_claude_model("claude-opus-5-5", None, prev_base=None)
    assert obs is not None
    assert obs.resolved_base == "opus"
    assert obs.adopt_selection == "opus"
    assert obs.reason is None


def test_custom_gateway_model_is_not_observed() -> None:
    # A custom selection's gateway id must be left untouched.
    assert observe_claude_model("kimi-k3-0711", "kimi-k3[1m]", prev_base=None) is None
    # Even a Claude concrete id is left alone under a custom selection.
    assert observe_claude_model("claude-opus-5", "kimi-k3[1m]", prev_base=None) is None
    # A non-Claude concrete id under a Claude selection is also ignored.
    assert observe_claude_model("kimi-k3-0711", "opus", prev_base=None) is None


def test_uncatalogued_claude_model_still_observed() -> None:
    obs = observe_claude_model("claude-opus-9", "opus", prev_base=None)
    assert obs is not None
    assert obs.resolved_base == "claude-opus-9"
    assert obs.adopt_selection == "claude-opus-9"
    assert obs.reason == "initial_mismatch"


def test_one_m_not_reattached_for_family_without_variant() -> None:
    # haiku has no [1m] catalogue entry; the suffix must resolve away.
    obs = observe_claude_model("claude-haiku-4-5", "opus[1m]", prev_base="opus")
    assert obs is not None
    assert obs.resolved_base == "haiku"
    assert obs.adopt_selection == "haiku"
