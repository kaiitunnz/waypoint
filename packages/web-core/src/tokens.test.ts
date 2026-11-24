import { describe, expect, it } from "vitest";

import { UNIFIED_TOKEN_CATEGORIES, unifyTokens } from "./tokens";

describe("unifyTokens", () => {
  it("subtracts cached and reasoning subsets for codex totals", () => {
    expect(
      unifyTokens("codex", {
        input_tokens: 1000,
        cached_input_tokens: 400,
        output_tokens: 300,
        reasoning_output_tokens: 100,
      }),
    ).toEqual({ fresh_input: 600, cache_read: 400, cache_write: 0, output: 200, reasoning: 100 });
  });

  it("subtracts reasoning from output for opencode and keeps its cache buckets", () => {
    expect(
      unifyTokens("opencode", {
        input_tokens: 50,
        cache_read_tokens: 20,
        cache_write_tokens: 5,
        output_tokens: 80,
        reasoning_tokens: 30,
      }),
    ).toEqual({ fresh_input: 50, cache_read: 20, cache_write: 5, output: 50, reasoning: 30 });
  });

  it("treats claude_code totals as already disjoint", () => {
    expect(
      unifyTokens("claude_code", {
        input_tokens: 10,
        cache_read_tokens: 20,
        cache_creation_tokens: 30,
        output_tokens: 40,
      }),
    ).toEqual({ fresh_input: 10, cache_read: 20, cache_write: 30, output: 40, reasoning: 0 });
  });

  it("falls back to the claude_code mapping for an unknown source", () => {
    expect(unifyTokens("something-new", { input_tokens: 7, output_tokens: 3 })).toEqual({
      fresh_input: 7,
      cache_read: 0,
      cache_write: 0,
      output: 3,
      reasoning: 0,
    });
  });

  it("reads missing, non-numeric and non-finite fields as zero", () => {
    const totals = { input_tokens: Number.NaN, output_tokens: Infinity, cache_read_tokens: "5" as unknown as number };
    expect(unifyTokens("claude_code", totals)).toEqual({
      fresh_input: 0,
      cache_read: 0,
      cache_write: 0,
      output: 0,
      reasoning: 0,
    });
    expect(unifyTokens("codex", {})).toEqual({
      fresh_input: 0,
      cache_read: 0,
      cache_write: 0,
      output: 0,
      reasoning: 0,
    });
  });

  it("preserves the total across the disjoint buckets", () => {
    const unified = unifyTokens("codex", {
      input_tokens: 900,
      cached_input_tokens: 300,
      output_tokens: 250,
      reasoning_output_tokens: 50,
    });
    const sum = UNIFIED_TOKEN_CATEGORIES.reduce((total, key) => total + unified[key], 0);
    expect(sum).toBe(900 + 250);
  });
});
