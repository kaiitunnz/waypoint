// Mirrors backend/src/waypoint/telemetry/tokens.py — keep in sync.
//
// Every backend's ledger totals use a different, overlapping native
// vocabulary; summing them verbatim double-counts Codex/OpenCode (their
// input/output totals already include cached/reasoning tokens). `unifyTokens`
// folds a backend's raw totals onto 5 disjoint buckets via a deterministic,
// source-keyed subset subtraction so they can always be summed safely.

export const UNIFIED_TOKEN_CATEGORIES = [
  "fresh_input",
  "cache_read",
  "cache_write",
  "output",
  "reasoning",
] as const;

export type UnifiedTokenCategory = (typeof UNIFIED_TOKEN_CATEGORIES)[number];

export const UNIFIED_TOKEN_LABELS: Record<UnifiedTokenCategory, string> = {
  fresh_input: "Fresh input",
  cache_read: "Cached input (read)",
  cache_write: "Cache write",
  output: "Output",
  reasoning: "Reasoning",
};

function amount(totals: Record<string, number>, key: string): number {
  const value = totals[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function unifyTokens(
  source: string,
  rawTotals: Record<string, number>,
): Record<UnifiedTokenCategory, number> {
  if (source === "codex") {
    const input = amount(rawTotals, "input_tokens");
    const cachedInput = amount(rawTotals, "cached_input_tokens");
    const output = amount(rawTotals, "output_tokens");
    const reasoningOutput = amount(rawTotals, "reasoning_output_tokens");
    return {
      fresh_input: input - cachedInput,
      cache_read: cachedInput,
      cache_write: 0,
      output: output - reasoningOutput,
      reasoning: reasoningOutput,
    };
  }
  if (source === "opencode") {
    const output = amount(rawTotals, "output_tokens");
    const reasoning = amount(rawTotals, "reasoning_tokens");
    return {
      fresh_input: amount(rawTotals, "input_tokens"),
      cache_read: amount(rawTotals, "cache_read_tokens"),
      cache_write: amount(rawTotals, "cache_write_tokens"),
      output: output - reasoning,
      reasoning,
    };
  }
  // claude_code, and any unrecognized source: treated as already-disjoint
  // native categories (the conservative default — never a guess at overlap).
  return {
    fresh_input: amount(rawTotals, "input_tokens"),
    cache_read: amount(rawTotals, "cache_read_tokens"),
    cache_write: amount(rawTotals, "cache_creation_tokens"),
    output: amount(rawTotals, "output_tokens"),
    reasoning: 0,
  };
}
