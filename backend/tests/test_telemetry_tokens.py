"""Unit tests for ``telemetry/tokens.py::unify_tokens`` (token-unify-spec).

Each backend's ledger totals use a different, overlapping native vocabulary;
``unify_tokens`` folds them onto 5 disjoint buckets via a deterministic,
source-keyed subset subtraction so the result always sums to the backend's
true provider total.
"""

from waypoint.telemetry.tokens import UNIFIED_TOKEN_CATEGORIES, unify_tokens


def test_unify_tokens_claude_code_categories_are_already_disjoint() -> None:
    raw = {
        "input_tokens": 50,
        "cache_read_tokens": 10,
        "cache_creation_tokens": 5,
        "output_tokens": 20,
    }
    unified = unify_tokens("claude_code", raw)
    assert unified == {
        "fresh_input": 50,
        "cache_read": 10,
        "cache_write": 5,
        "output": 20,
        "reasoning": 0,
    }
    assert set(unified) == set(UNIFIED_TOKEN_CATEGORIES)
    # Claude's native categories are already disjoint, so the sum is
    # unchanged by unification.
    assert sum(unified.values()) == sum(raw.values())


def test_unify_tokens_codex_subtracts_overlapping_totals() -> None:
    raw = {
        "input_tokens": 100,  # TOTAL, includes cached_input_tokens
        "cached_input_tokens": 30,
        "output_tokens": 40,  # TOTAL, includes reasoning_output_tokens
        "reasoning_output_tokens": 10,
    }
    unified = unify_tokens("codex", raw)
    assert unified == {
        "fresh_input": 70,
        "cache_read": 30,
        "cache_write": 0,
        "output": 30,
        "reasoning": 10,
    }
    # Codex's own totalTokens = inputTokens + outputTokens.
    assert sum(unified.values()) == raw["input_tokens"] + raw["output_tokens"]


def test_unify_tokens_opencode_subtracts_reasoning_from_output() -> None:
    raw = {
        "input_tokens": 60,
        "cache_read_tokens": 5,
        "cache_write_tokens": 2,
        "output_tokens": 25,  # TOTAL, includes reasoning_tokens
        "reasoning_tokens": 8,
    }
    unified = unify_tokens("opencode", raw)
    assert unified == {
        "fresh_input": 60,
        "cache_read": 5,
        "cache_write": 2,
        "output": 17,
        "reasoning": 8,
    }
    provider_total = (
        raw["input_tokens"]
        + raw["cache_read_tokens"]
        + raw["cache_write_tokens"]
        + raw["output_tokens"]
    )
    assert sum(unified.values()) == provider_total


def test_unify_tokens_missing_fields_default_to_zero() -> None:
    assert unify_tokens("codex", {}) == {
        "fresh_input": 0,
        "cache_read": 0,
        "cache_write": 0,
        "output": 0,
        "reasoning": 0,
    }


def test_unify_tokens_unknown_source_falls_back_to_disjoint_native() -> None:
    raw = {"input_tokens": 5, "output_tokens": 7}
    assert unify_tokens("some_future_backend", raw) == unify_tokens("claude_code", raw)


def test_unify_tokens_clamps_negative_codex_subtractions() -> None:
    # A malformed provider payload where the "cached"/"reasoning" subsets exceed
    # their declared totals must never push a bucket negative and silently
    # reduce every downstream sum.
    raw = {
        "input_tokens": 20,
        "cached_input_tokens": 50,
        "output_tokens": 10,
        "reasoning_output_tokens": 40,
    }
    unified = unify_tokens("codex", raw)
    assert unified["fresh_input"] == 0
    assert unified["output"] == 0
    assert all(amount >= 0 for amount in unified.values())


def test_unify_tokens_clamps_negative_opencode_reasoning() -> None:
    raw = {"input_tokens": 15, "output_tokens": 5, "reasoning_tokens": 30}
    unified = unify_tokens("opencode", raw)
    assert unified["output"] == 0
    assert all(amount >= 0 for amount in unified.values())
