import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_FILTERS,
  DEFAULT_RANGE,
  DRILLDOWN_KIND_OPTIONS,
  TOKEN_DISPLAY_ORDER,
  coverageLabel,
  confidenceLabel,
  deriveDatabaseContent,
  factKindLabel,
  formatBytes,
  formatCompactNumber,
  formatExactBytes,
  formatHourLabel,
  formatRangeLabel,
  insightSeverityTone,
  instanceCategoryColor,
  instanceCategoryLabel,
  readTelemetryQuery,
  shortId,
  splitTokenTiers,
  telemetryParams,
  tokenCategoryColor,
  tokenCategoryLabel,
  toolOutcomeLabel,
  toolOutcomeTone,
  transitionLabel,
  turnKindLabel,
  writeTelemetryQuery,
  type TelemetryFiltersState,
} from "./telemetry";
import type { TelemetryRange } from "./types";

const filters = (overrides: Partial<TelemetryFiltersState> = {}): TelemetryFiltersState => ({
  ...DEFAULT_FILTERS,
  ...overrides,
});

describe("telemetryParams", () => {
  it("encodes a preset range with no filters", () => {
    expect(telemetryParams(DEFAULT_RANGE, DEFAULT_FILTERS).toString()).toBe("preset=7d");
    expect(telemetryParams({ preset: "today", start: "x", end: "y" }, DEFAULT_FILTERS).toString()).toBe("preset=today");
  });

  it("encodes a custom range and omits blank bounds", () => {
    expect(
      telemetryParams({ preset: "custom", start: "2026-01-01", end: "2026-01-31" }, DEFAULT_FILTERS).toString(),
    ).toBe("preset=custom&start=2026-01-01&end=2026-01-31");
    expect(telemetryParams({ preset: "custom", start: "", end: "" }, DEFAULT_FILTERS).toString()).toBe("preset=custom");
  });

  it("repeats list filters in a fixed order", () => {
    const params = telemetryParams(
      DEFAULT_RANGE,
      filters({
        backends: ["a", "b"],
        models: ["m"],
        repos: ["r"],
        tags: ["k:v"],
        sources: ["s"],
        transports: ["t"],
      }),
    );
    expect(params.toString()).toBe("preset=7d&backend=a&backend=b&model=m&repo=r&tag=k%3Av&source=s&transport=t");
  });

  it("adds scope, a trimmed parent and the descendants switch only when non-default", () => {
    const params = telemetryParams(
      DEFAULT_RANGE,
      filters({ parentScope: "roots" as never, parentSessionId: "  p1 ", includeDescendants: false }),
    );
    expect(params.toString()).toBe("preset=7d&scope=roots&parent=p1&descendants=false");
  });

  it("layers extra params, dropping undefined and null and stringifying the rest", () => {
    const params = telemetryParams(DEFAULT_RANGE, DEFAULT_FILTERS, {
      group_by: "model",
      page: 2,
      flag: false,
      skipped: undefined,
      nothing: null,
    });
    expect(params.toString()).toBe("preset=7d&group_by=model&page=2&flag=false");
  });

  it("lets an extra param override a base one", () => {
    expect(telemetryParams(DEFAULT_RANGE, DEFAULT_FILTERS, { preset: "30d" }).toString()).toBe("preset=30d");
  });
});

describe("telemetry query persistence", () => {
  afterEach(() => vi.unstubAllGlobals());

  const stubStorage = (initial: Record<string, string> = {}) => {
    const store = new Map(Object.entries(initial));
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => store.get(key) ?? null,
        setItem: (key: string, value: string) => void store.set(key, value),
      },
    });
    return store;
  };

  it("returns the defaults outside a browser", () => {
    expect(readTelemetryQuery()).toEqual({ range: DEFAULT_RANGE, filters: DEFAULT_FILTERS });
    expect(() => writeTelemetryQuery(DEFAULT_RANGE, DEFAULT_FILTERS)).not.toThrow();
  });

  it("round-trips a stored query", () => {
    stubStorage();
    const range = { preset: "custom" as const, start: "2026-01-01", end: "2026-01-02" };
    const stored = filters({ backends: ["codex"], includeDescendants: false });
    writeTelemetryQuery(range, stored);
    expect(readTelemetryQuery()).toEqual({ range, filters: stored });
  });

  it("returns the defaults when nothing is stored or the stored value is not JSON", () => {
    stubStorage();
    expect(readTelemetryQuery()).toEqual({ range: DEFAULT_RANGE, filters: DEFAULT_FILTERS });
    stubStorage({ "waypoint.telemetry-query": "{not json" });
    expect(readTelemetryQuery()).toEqual({ range: DEFAULT_RANGE, filters: DEFAULT_FILTERS });
  });

  it("fills fields missing from an older stored query with defaults", () => {
    stubStorage({ "waypoint.telemetry-query": JSON.stringify({ range: { preset: "30d" }, filters: { repos: ["r"] } }) });
    expect(readTelemetryQuery()).toEqual({
      range: { ...DEFAULT_RANGE, preset: "30d" },
      filters: { ...DEFAULT_FILTERS, repos: ["r"] },
    });
  });
});

describe("labels", () => {
  it("labels lifecycle transitions, turn kinds and fact kinds, passing unknowns through", () => {
    expect(transitionLabel("waiting")).toBe("Waiting on input");
    expect(transitionLabel("error")).toBe("Errored");
    expect(transitionLabel("mystery")).toBe("mystery");
    expect(turnKindLabel("agent")).toBe("Agent turn");
    expect(turnKindLabel("user")).toBe("User turn");
    expect(turnKindLabel("other")).toBe("other");
    expect(factKindLabel("tool_call")).toBe("Tool calls");
    expect(factKindLabel("nope")).toBe("nope");
    for (const option of DRILLDOWN_KIND_OPTIONS) expect(factKindLabel(option.value)).toBe(option.label);
  });

  it("labels and tones tool outcomes", () => {
    expect(toolOutcomeLabel("timed_out")).toBe("Timed out");
    expect(toolOutcomeLabel("odd")).toBe("odd");
    expect(toolOutcomeLabel(null)).toBe("Unknown");
    expect(toolOutcomeLabel("")).toBe("Unknown");
    expect(toolOutcomeTone("succeeded")).toBe("good");
    expect(toolOutcomeTone("failed")).toBe("danger");
    expect(toolOutcomeTone("timed_out")).toBe("danger");
    expect(toolOutcomeTone("cancelled")).toBe("warn");
    expect(toolOutcomeTone("unknown")).toBe("neutral");
    expect(toolOutcomeTone(undefined)).toBe("neutral");
  });

  it("maps insight severity to a tone", () => {
    expect(insightSeverityTone("critical")).toBe("danger");
    expect(insightSeverityTone("warning")).toBe("warn");
    expect(insightSeverityTone("info" as never)).toBe("good");
  });

  it("labels token coverage", () => {
    expect(coverageLabel("entire")).toBe("Full history");
    expect(coverageLabel("tracked_since")).toBe("Tracked since backfill");
    expect(coverageLabel("partial")).toBe("Partial — some sessions untracked");
    expect(coverageLabel("odd" as never)).toBe("odd");
  });

  it("capitalises a confidence label", () => {
    expect(confidenceLabel("high")).toBe("High");
    expect(confidenceLabel("")).toBe("");
  });
});

describe("token categories", () => {
  it("labels known categories and title-cases unknown ones", () => {
    expect(tokenCategoryLabel("cache_read")).toBe("Cached input (read)");
    expect(tokenCategoryLabel("some_new_bucket")).toBe("Some New Bucket");
  });

  it("colours categories and falls back to the last series slot", () => {
    expect(tokenCategoryColor("fresh_input")).toBe("var(--tm-series-1)");
    expect(tokenCategoryColor("cache_read")).toBe("var(--tm-token-reread)");
    expect(tokenCategoryColor("other")).toBe("var(--tm-series-6)");
  });

  it("keeps the re-read tier last in the display order", () => {
    expect(TOKEN_DISPLAY_ORDER).toEqual(["fresh_input", "output", "reasoning", "cache_write", "cache_read"]);
  });

  it("splits totals into new work and re-read tiers, ignoring missing buckets", () => {
    expect(splitTokenTiers({ fresh_input: 10, output: 5, reasoning: 2, cache_write: 3, cache_read: 100 })).toEqual({
      newWork: 20,
      reread: 100,
      total: 120,
    });
    expect(splitTokenTiers({})).toEqual({ newWork: 0, reread: 0, total: 0 });
  });
});

describe("instance categories", () => {
  it("labels and colours storage categories with fallbacks", () => {
    expect(instanceCategoryLabel("sqlite_companions")).toBe("SQLite companions");
    expect(instanceCategoryLabel("brand_new")).toBe("Brand New");
    expect(instanceCategoryColor("database")).toBe("var(--tm-series-1)");
    expect(instanceCategoryColor("brand_new")).toBe("var(--tm-series-6)");
  });
});

describe("deriveDatabaseContent", () => {
  it("is empty without any data", () => {
    expect(deriveDatabaseContent({}, {}, {})).toEqual({
      session: [],
      telemetry: [],
      sessionBytes: null,
      telemetryBytes: null,
      otherManagedRecords: null,
      otherManagedBytes: null,
      eventMix: [],
      hasContent: false,
      hasBytes: false,
    });
  });

  it("groups allowlisted tables, folds the rest into a remainder and sorts the event mix", () => {
    const model = deriveDatabaseContent(
      { sessions: 3, events: 100, telemetry_facts: 7, mystery: 5, other: 2 },
      { sessions: 4096, events: 8192, telemetry_facts: 1024, mystery: 512, sqlite_internal: 256 },
      { tool_call: 10, user_input: 10, "agent-output": 40, bad: -1, worse: Number.NaN },
    );
    expect(model.session).toEqual([
      { key: "sessions", label: "Session records", count: 3, bytes: 4096 },
      { key: "events", label: "Session events", count: 100, bytes: 8192 },
    ]);
    expect(model.telemetry).toEqual([
      { key: "telemetry_facts", label: "Telemetry facts", count: 7, bytes: 1024 },
    ]);
    expect(model.sessionBytes).toBe(12288);
    expect(model.telemetryBytes).toBe(1024);
    expect(model.otherManagedRecords).toBe(7);
    expect(model.otherManagedBytes).toBe(768);
    expect(model.eventMix).toEqual([
      { kind: "agent-output", label: "Agent Output", count: 40 },
      { kind: "tool_call", label: "Tool Call", count: 10 },
      { kind: "user_input", label: "User Input", count: 10 },
    ]);
    expect(model.hasContent).toBe(true);
    expect(model.hasBytes).toBe(true);
  });

  it("leaves byte totals null when no byte data exists and skips invalid counts", () => {
    const model = deriveDatabaseContent({ sessions: 3, events: -1, telemetry_facts: Number.NaN }, {}, {});
    expect(model.session).toEqual([{ key: "sessions", label: "Session records", count: 3, bytes: null }]);
    expect(model.telemetry).toEqual([]);
    expect(model.sessionBytes).toBeNull();
    expect(model.hasBytes).toBe(false);
    expect(model.hasContent).toBe(true);
  });
});

describe("number and id formatting", () => {
  it("labels hours on a 12-hour clock", () => {
    expect([0, 1, 11, 12, 13, 23].map(formatHourLabel)).toEqual(["12a", "1a", "11a", "12p", "1p", "11p"]);
  });

  it("formats compact numbers", () => {
    expect(formatCompactNumber(999)).toBe("999");
    expect(formatCompactNumber(1234)).toBe("1.2K");
    expect(formatCompactNumber(2_500_000)).toBe("2.5M");
  });

  it("formats IEC byte sizes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(-5)).toBe("0 B");
    expect(formatBytes(Number.NaN)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1.0 KiB");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GiB");
    expect(formatBytes(1024 ** 6 * 3)).toBe("3072.0 PiB");
  });

  it("spells out exact bytes", () => {
    expect(formatExactBytes(1234567)).toBe("1,234,567 bytes");
  });

  it("keeps short ids whole and keeps both ends of long ones", () => {
    expect(shortId("claude_code-196bdf37")).toBe("claude_code-196bdf37");
    expect(shortId("a".repeat(24))).toBe("a".repeat(24));
    expect(shortId("0123456789abcdefghijklmnopqrstuvwxyz")).toBe("0123456789ab…stuvwxyz");
  });
});

describe("formatRangeLabel", () => {
  const range = (overrides: Partial<TelemetryRange>): TelemetryRange => ({
    start: "2026-11-01T00:00:00Z",
    end: "2026-11-08T00:00:00Z",
    tz: "UTC",
    ...overrides,
  });
  const day = (iso: string) =>
    new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

  it("shows the inclusive last day in the host timezone using the echoed offset", () => {
    expect(formatRangeLabel(range({ utc_offset_minutes: 0 }))).toBe(
      `${day("2026-11-01T00:00:00Z")} – ${day("2026-11-07T00:00:00Z")} (UTC)`,
    );
  });

  it("shifts each instant by the host offset before choosing the day", () => {
    const label = formatRangeLabel(
      range({
        start: "2026-10-31T16:00:00Z",
        end: "2026-11-07T16:00:00Z",
        tz: "Asia/Singapore",
        utc_offset_minutes: 480,
      }),
    );
    expect(label).toBe(`${day("2026-11-01T00:00:00Z")} – ${day("2026-11-07T00:00:00Z")} (Asia/Singapore)`);
  });

  it("falls back to the raw strings for unparsable instants", () => {
    expect(formatRangeLabel(range({ start: "x", end: "y" }))).toBe("x – y (UTC)");
  });
});
