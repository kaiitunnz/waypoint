import { describe, expect, it } from "vitest";

import type { SessionContextUsage, SessionRecord, SessionRateLimitUsage, SessionTokenUsage } from "../types";
import { sessionCoverageLabel, USAGE_POPOVER_SIZE_KEY, usageBars, usagePanelModel } from "./usagePanel";

const context = (patch: Partial<SessionContextUsage> = {}): SessionContextUsage => ({
  used_tokens: 620_000,
  context_window_tokens: 1_000_000,
  updated_at: "2026-01-01T00:00:00Z",
  source: "claude_code",
  ...patch,
});

const limits = (patch: Partial<SessionRateLimitUsage> = {}): SessionRateLimitUsage => ({
  source: "claude_code",
  updated_at: "2026-01-01T00:00:00Z",
  windows: [{ id: "5h", label: "5H", used_percent: 77 }],
  ...patch,
});

const totals = (patch: Partial<SessionTokenUsage> = {}): SessionTokenUsage => ({
  source: "codex",
  tracked_turns: 2,
  totals: { input_tokens: 100, cached_input_tokens: 40, output_tokens: 30, reasoning_output_tokens: 10 },
  observed_from: "2026-01-01T00:00:00Z",
  complete_through: "2026-01-01T00:00:00Z",
  coverage: "entire_waypoint_session",
  updated_at: "2026-01-01T00:00:00Z",
  ...patch,
});

const session = (patch: Partial<SessionRecord>) => patch as SessionRecord;

describe("usagePanelModel", () => {
  const percent = (patch: Parameters<typeof context>[0]) =>
    usagePanelModel(session({ context_usage: context(patch) })).contextPercent;

  it("knows no context fill without a window or without reported use", () => {
    expect(percent({})).toBe(62);
    expect(percent({ context_window_tokens: null })).toBeNull();
    expect(percent({ used_tokens: 0 })).toBeNull();
  });

  it("has nothing to show for a session without usage", () => {
    expect(usagePanelModel(session({})).hasUsage).toBe(false);
    expect(usagePanelModel(null).hasUsage).toBe(false);
  });

  it("takes the more urgent tone", () => {
    expect(usagePanelModel(session({ context_usage: context(), rate_limit_usage: limits() })).tone).toBe("warn");
    expect(
      usagePanelModel(session({ context_usage: context({ used_tokens: 950_000 }), rate_limit_usage: limits() })).tone,
    ).toBe("danger");
    expect(usagePanelModel(session({ context_usage: context() })).tone).toBe("good");
  });

  it("flags an unreported context instead of showing 0%", () => {
    const model = usagePanelModel(session({ context_usage: context({ used_tokens: 0 }) }));
    expect(model.contextUnreported).toBe(true);
    expect(model.contextPercent).toBeNull();
    expect(model.summary).toEqual(["Current context 1,000,000 window · usage not reported"]);
  });

  it("names a provider source and its unavailability", () => {
    expect(
      usagePanelModel(
        session({ rate_limit_usage: limits({ origin: "usage_provider", source_label: "Lumid", unavailable: true }) }),
      ).rateLimitSourceLabel,
    ).toBe("Lumid (unavailable)");
    expect(usagePanelModel(session({ rate_limit_usage: limits() })).rateLimitSourceLabel).toBe("Claude Code");
    expect(usagePanelModel(session({ rate_limit_usage: limits({ notes: ["Estimated"] }) })).rateLimitSourceLabel).toBe(
      "Estimated",
    );
  });

  it("unifies overlapping totals", () => {
    const model = usagePanelModel(session({ session_token_usage: totals() }));
    expect(Object.fromEntries(model.tokenTotals.map((total) => [total.label, total.tokens]))).toMatchObject({
      "Fresh input": 60,
      "Cached input (read)": 40,
      Output: 20,
      Reasoning: 10,
    });
    expect(model.hasTrackedTurns).toBe(true);
  });

  it("labels the last turn's categories and drops the empty ones", () => {
    const model = usagePanelModel(
      session({
        context_usage: context({
          breakdown: { input_tokens: 5, output_tokens: 0, cache_creation_tokens: 2, made_up_tokens: 1 },
        }),
      }),
    );
    expect(model.contextBreakdown).toEqual([
      { key: "input_tokens", label: "Input", tokens: 5 },
      { key: "cache_creation_tokens", label: "Cache write", tokens: 2 },
      { key: "made_up_tokens", label: "made up tokens", tokens: 1 },
    ]);
  });

  it("keeps categories that share a label apart by their raw key", () => {
    const { contextBreakdown } = usagePanelModel(
      session({ context_usage: context({ breakdown: { reasoning_output_tokens: 1, reasoning_tokens: 2 } }) }),
    );
    expect(contextBreakdown.map((entry) => entry.label)).toEqual(["Reasoning", "Reasoning"]);
    expect(contextBreakdown.map((entry) => entry.key)).toEqual(["reasoning_output_tokens", "reasoning_tokens"]);
  });
});

describe("USAGE_POPOVER_SIZE_KEY", () => {
  it("keeps the key sizes are already stored under", () => {
    expect(USAGE_POPOVER_SIZE_KEY).toBe("waypoint.usage-popover-size.v1");
  });
});

describe("sessionCoverageLabel", () => {
  it("names the coverage", () => {
    expect(sessionCoverageLabel(totals())).toBe("Entire Waypoint session");
    expect(sessionCoverageLabel(totals({ coverage: "partial" }))).toBe("Partial coverage");
  });
});

describe("usageBars", () => {
  const NOW = Date.parse("2026-01-01T00:00:00Z");
  const bars = (patch: Partial<SessionRecord>) => usageBars(usagePanelModel(session(patch)), NOW);

  it("has no bars for a session without usage", () => {
    expect(bars({})).toEqual([]);
  });

  it("leads with the context window, then each rate-limit window", () => {
    const shown = bars({
      context_usage: context(),
      rate_limit_usage: limits({
        windows: [
          { id: "5h", label: "Claude Max, 5 hours", used_percent: 77, resets_at: "2026-01-01T01:12:00Z" },
          { id: "wk", label: "Claude Max, week", used_percent: 16 },
        ],
      }),
    });
    expect(shown.map((bar) => bar.id)).toEqual(["context", "window-5h", "window-wk"]);
    expect(shown[0]).toMatchObject({ label: "Context", percent: 62, tone: "good", detail: "620K of 1M" });
    expect(shown[1]).toMatchObject({ percent: 77, tone: "warn", detail: "resets in 1h 12m" });
    expect(shown[2]).toMatchObject({ percent: 16, tone: "good", detail: null });
  });

  it("falls back to the backend's reset description when a window gives no time", () => {
    const [bar] = bars({
      rate_limit_usage: limits({ windows: [{ id: "w", label: "Week", used_percent: 40, reset_description: "resets Monday" }] }),
    });
    expect(bar.detail).toBe("resets Monday");
  });

  it("says so when the context fill is unknown", () => {
    const [unreported] = bars({ context_usage: context({ used_tokens: 0 }) });
    expect(unreported).toMatchObject({ percent: null, detail: "1M window, usage not reported" });
    const [noWindow] = bars({ context_usage: context({ context_window_tokens: null }) });
    expect(noWindow).toMatchObject({ percent: null, detail: "620K" });
  });
});
