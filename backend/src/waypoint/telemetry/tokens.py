"""Read-layer token category unification (token-unify-spec).

Every backend's ledger totals use a different, overlapping native vocabulary
(see ``waypoint.schemas.TokenUsageRecord``): Claude's four categories are
already disjoint; Codex's ``inputTokens``/``outputTokens`` are provider
*totals* that already include ``cachedInputTokens``/``reasoningOutputTokens``;
OpenCode's ``output`` likewise includes ``reasoning``. Summing the raw
categories verbatim double-counts Codex/OpenCode and silently drops
OpenCode's missing declared total.

``unify_tokens`` is the one place generic aggregate code is allowed to
normalize across backends (``waypoint.schemas.TOKEN_USAGE_CATEGORIES``'s
doctrine comment) — a deterministic, source-keyed subset subtraction, never a
guess. It never writes back to the ledger; the ledger keeps its raw native
keys (they still feed the per-session context pill).
"""

from waypoint.schemas import TOKEN_USAGE_CATEGORIES

UNIFIED_TOKEN_CATEGORIES = TOKEN_USAGE_CATEGORIES

UNIFIED_TOKEN_LABELS: dict[str, str] = {
    "fresh_input": "Fresh input",
    "cache_read": "Cached input (read)",
    "cache_write": "Cache write",
    "output": "Output",
    "reasoning": "Reasoning",
}


def _amount(raw_totals: dict[str, int], key: str) -> int:
    value = raw_totals.get(key)
    return int(value) if isinstance(value, int | float) else 0


def unify_tokens(source: str, raw_totals: dict[str, int]) -> dict[str, int]:
    """Fold one backend's raw ledger ``totals`` onto the 5 disjoint buckets.

    The result always sums to the provider's true total for ``source`` and
    always carries all 5 keys (0 where the backend has no such category), so
    callers can fold/sum results across mixed-backend rows without an
    ``all(... is not None)`` gate.
    """
    if source == "codex":
        input_total = _amount(raw_totals, "input_tokens")
        cached_input = _amount(raw_totals, "cached_input_tokens")
        output_total = _amount(raw_totals, "output_tokens")
        reasoning_output = _amount(raw_totals, "reasoning_output_tokens")
        return {
            "fresh_input": input_total - cached_input,
            "cache_read": cached_input,
            "cache_write": 0,
            "output": output_total - reasoning_output,
            "reasoning": reasoning_output,
        }
    if source == "opencode":
        output_total = _amount(raw_totals, "output_tokens")
        reasoning = _amount(raw_totals, "reasoning_tokens")
        return {
            "fresh_input": _amount(raw_totals, "input_tokens"),
            "cache_read": _amount(raw_totals, "cache_read_tokens"),
            "cache_write": _amount(raw_totals, "cache_write_tokens"),
            "output": output_total - reasoning,
            "reasoning": reasoning,
        }
    # claude_code, and any future/unrecognized source: treated as already
    # disjoint native categories (the conservative default — never a guess
    # at overlap, so it can only under-populate a not-yet-mapped backend's
    # totals, never double-count one).
    return {
        "fresh_input": _amount(raw_totals, "input_tokens"),
        "cache_read": _amount(raw_totals, "cache_read_tokens"),
        "cache_write": _amount(raw_totals, "cache_creation_tokens"),
        "output": _amount(raw_totals, "output_tokens"),
        "reasoning": 0,
    }
