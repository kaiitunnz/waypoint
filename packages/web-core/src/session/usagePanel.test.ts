import { describe, expect, it } from "vitest";

import type { SessionContextUsage, SessionRecord, SessionRateLimitUsage, SessionTokenUsage } from "../types";
import { sessionCoverageLabel, usagePanelModel } from "./usagePanel";

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
    expect(Object.fromEntries(model.tokenTotals)).toMatchObject({
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
      ["Input", 5],
      ["Cache write", 2],
      ["made up tokens", 1],
    ]);
  });
});

describe("sessionCoverageLabel", () => {
  it("names the coverage", () => {
    expect(sessionCoverageLabel(totals())).toBe("Entire Waypoint session");
    expect(sessionCoverageLabel(totals({ coverage: "partial" }))).toBe("Partial coverage");
  });
});
